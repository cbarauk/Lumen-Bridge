import os
import sys
import unittest
from urllib.parse import parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ble  # noqa: E402
import neewer as n  # noqa: E402
import server  # noqa: E402


class FakeController:
    def __init__(self):
        self.calls = []

    def _rec(self, name, *args):
        self.calls.append((name,) + args)
        return 1

    def power(self, on, light=None): return self._rec("power", on, light)
    def toggle(self, light=None): return self._rec("toggle", light)
    def set_cct(self, brr, k, gm=0, light=None): return self._rec("cct", brr, k, gm, light)
    def set_hsi(self, hue, sat, brr, light=None): return self._rec("hsi", hue, sat, brr, light)
    def set_scene(self, sid, brr, light=None): return self._rec("scene", sid, brr, light)
    def list_lights(self): return []
    def scan(self): self.calls.append(("scan",))


def call(ctl, path_query):
    path, _, q = path_query.partition("?")
    return server.handle(ctl, path, parse_qs(q))


class RouteTests(unittest.TestCase):
    def test_ping_and_404(self):
        ctl = FakeController()
        self.assertEqual(call(ctl, "/ping"), (200, {"status": "pong"}))
        self.assertEqual(call(ctl, "/nope")[0], 404)

    def test_power_routes_with_light_filter(self):
        ctl = FakeController()
        call(ctl, "/turnOnLight?light=KeyLight")
        call(ctl, "/turnOffLight")
        call(ctl, "/toggleLight?light=Fill")
        self.assertEqual(ctl.calls, [("power", True, "KeyLight"), ("power", False, None), ("toggle", "Fill")])

    def test_cct_defaults_and_clamp(self):
        ctl = FakeController()
        call(ctl, "/setLightCCT?CCT=3200")
        call(ctl, "/setLightCCT?CCT=99999&Brightness=150&GM=80")
        self.assertEqual(ctl.calls[0], ("cct", 100, 3200, 0, None))
        self.assertEqual(ctl.calls[1], ("cct", 100, 8500, 50, None))

    def test_hsi_from_rgb_and_hue(self):
        ctl = FakeController()
        call(ctl, "/setLightHSI?RGB=ff0000&Saturation=100&Brightness=50")
        call(ctl, "/setLightHSI?RGB=00ff00&Saturation=100&Brightness=50")
        call(ctl, "/setLightHSI?HUE=200&Saturation=0.5&Brightness=80")
        self.assertEqual(ctl.calls[0], ("hsi", 0, 100, 50, None))
        self.assertEqual(ctl.calls[1], ("hsi", 120, 100, 50, None))
        self.assertEqual(ctl.calls[2], ("hsi", 200, 50, 80, None))

    def test_hsi_bad_input(self):
        ctl = FakeController()
        self.assertEqual(call(ctl, "/setLightHSI?RGB=xyz")[0], 400)
        self.assertEqual(call(ctl, "/setLightHSI?Brightness=50")[0], 400)

    def test_scene_by_name_and_id(self):
        ctl = FakeController()
        call(ctl, "/setLightScene?Scene=SquadCar&Brightness=100")
        call(ctl, "/setLightScene?Scene=Candle%20Light&Brightness=40")
        call(ctl, "/setLightScene?SceneId=5")
        self.assertEqual(ctl.calls[0], ("scene", 1, 100, None))
        self.assertEqual(ctl.calls[1], ("scene", 6, 40, None))
        self.assertEqual(ctl.calls[2], ("scene", 5, 100, None))
        self.assertEqual(call(ctl, "/setLightScene?Scene=Bogus")[0], 400)


class FakeDevice:
    def __init__(self, address, name):
        self.address, self.name = address, name


class ControllerPacketTests(unittest.TestCase):
    """Drive the real Controller with fake connected lights and capture what would be written."""

    def setUp(self):
        self.ctl = ble.Controller(n.DB_PATH)
        self.sent = []
        light = ble.Light(FakeDevice("AA:BB:CC:DD:EE:01", "NW-20220014&00000000"), "NW-20220014&00000000", self.ctl.db)
        light.connected = True
        light.send = lambda data, urgent=False: (self.sent.append((data, urgent)) or True)
        self.light = light
        self.ctl.lights[light.address] = light

    def tearDown(self):
        self.ctl._loop.close()

    def test_power_is_urgent_and_matches_protocol(self):
        self.ctl.power(True, "CB60B")
        self.assertEqual(self.sent[-1], (bytes.fromhex("78810101fb"), True))

    def test_cct_is_latest_wins_and_scaled(self):
        self.ctl.set_cct(60, 4000, 0)
        data, urgent = self.sent[-1]
        self.assertFalse(urgent)
        self.assertEqual(data, n.cct_command(self.ctl.db, self.light.type, self.light.mac, 60, 4000, 0))

    def test_scene_on_light_without_scenes_sends_nothing_extra(self):
        before = len(self.sent)
        self.ctl.set_scene(999, 50)   # no such scene id -> no packet
        self.assertEqual(len(self.sent), before)

    def test_unknown_light_name_targets_nobody(self):
        self.assertEqual(self.ctl.power(True, "Nope"), 0)

    def _rgb_light(self):
        dev = FakeDevice("AA:BB:CC:DD:EE:02", "NW-RGB660 PRO")
        light = ble.Light(dev, "NW-RGB660 PRO", self.ctl.db)
        self.assertEqual(light.type, 3)          # RGB + scenes light
        light.connected = True
        light.send = lambda data, urgent=False: (self.sent.append((data, urgent)) or True)
        self.ctl.lights[light.address] = light
        return light

    def test_brightness_keeps_colour(self):
        light = self._rgb_light()
        self.ctl.set_hsi(120, 100, 50, light.nick)
        self.ctl.set_brightness(70, light.nick)
        self.assertEqual(self.sent[-1][0], n.hsi_command(self.ctl.db, light.type, light.mac, 120, 100, 70))

    def test_brightness_keeps_scene(self):
        light = self._rgb_light()
        self.ctl.set_scene(1, 50, light.nick)
        self.ctl.set_brightness(70, light.nick)
        self.assertEqual(self.sent[-1][0], n.scene_command(self.ctl.db, light.type, light.mac, 1, 70))

    def test_brightness_keeps_cct(self):
        self.ctl.set_cct(50, 4000, 0, "CB60B")
        self.ctl.set_brightness(70, "CB60B")
        self.assertEqual(self.sent[-1][0], n.cct_command(self.ctl.db, self.light.type, self.light.mac, 70, 4000, 0))

    def test_list_lights_shape(self):
        info = self.ctl.list_lights()[0]
        for key in ("name", "address", "connected", "canCCT", "cctRange", "scenes"):
            self.assertIn(key, info)
        self.assertTrue(info["canCCT"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
