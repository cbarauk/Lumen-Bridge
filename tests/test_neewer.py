import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import neewer as n  # noqa: E402

DB = n.load_database()
MAC = "AA:BB:CC:DD:EE:FF"


class PacketTests(unittest.TestCase):
    def test_power_on_off_match_documented_packets(self):
        self.assertEqual(n.power_command(DB, 3, MAC, True).hex(), "78810101fb")
        self.assertEqual(n.power_command(DB, 3, MAC, False).hex(), "78810102fc")

    def test_cct_packet(self):
        # brr 50, 4000K -> cct 40. size 05, checksum 0x90.
        self.assertEqual(n.cct_command(DB, 3, MAC, 50, 4000).hex(), "78870532283200" + "0090")

    def test_hsi_packet(self):
        # hue 120 (LE 78 00), sat 100 (64), brr 50 (32). checksum 0x10.
        self.assertEqual(n.hsi_command(DB, 3, MAC, 120, 100, 50).hex(), "78860478006432" + "10")

    def test_scene_packet(self):
        # SquadCar (fx9 id 1), brr 100 -> 78 88 02 64 01 67
        self.assertEqual(n.scene_command(DB, 3, MAC, 1, 100).hex(), "788802640167")

    def test_mac_pattern_layout(self):
        pattern = ("{cmdtag} {fxdatatag} {size} {mac} {fxsubtag} 0x01 {brr:uint8:range(0,100)} "
                   "{cct:uint8:range(29,70)} {speed:uint8:range(1,10)} {checksum}")
        pkt = n.build_command(pattern, {"mac": MAC, "brr": 80, "cct": 50, "speed": 5})
        self.assertEqual(len(pkt), 15)
        self.assertEqual(pkt[0], 0x78)
        self.assertEqual(pkt[1], 0x91)
        self.assertEqual(pkt[2], 11)                 # payload: mac(6) + subtag + 0x01 + brr + cct + speed
        self.assertEqual(pkt[3:9].hex(), "aabbccddeeff")

    def test_range_clamps_and_missing_var_fails(self):
        p = n.build_command("{cmdtag} {ccttag} {size} {brr:uint8:range(0,100)} 0x00 {checksum}", {"brr": 150})
        self.assertEqual(p[3], 100)
        self.assertIsNone(n.build_command("{cmdtag} {ccttag} {size} {nope} {checksum}", {}))

    def test_enum_accepts_string(self):
        self.assertEqual(n.build_command("{cmdtag} {powertag} {size} {state:uint8:enum(1=on,2=off)} {checksum}",
                                         {"state": "off"}).hex(), "78810102fc")


class NameTests(unittest.TestCase):
    def check(self, raw, ident, nick, project, type_id):
        got_nick, got_project = n.get_light_names(raw, ident)
        self.assertEqual((got_nick, got_project), (nick, project))
        self.assertEqual(n.get_light_type(got_nick, raw, got_project), type_id)

    def test_known_names(self):
        self.check("NW-20200015&00000000", "", "RGB1", "RGB1", 8)
        self.check("NW-20220014&00000000", "", "CB60B", "CB60B", 31)
        self.check("NW-20210012&FFFFFFFF", "", "CB60 RGB", "CB60 RGB", 22)
        self.check("NW-20240063&FFFFFFFF", "", "BH20C", "BH20C", 89)
        self.check("NW-20240073&00000000", "", "SL90", "SL90", 71)
        self.check("NW-RGB176 A1", "", "RGB176 A1", "RGB176 A1", 20)
        self.check("NW-20230042&00000000", "", "HS60C", "HS60C", 87)

    def test_suffix_from_identifier(self):
        nick, project = n.get_light_names("NW-20220014&00000000", "AEE0BA8C-D9B4-B7DB-0FD2-4531C7E5B053")
        self.assertEqual(nick, "CB60B-E5B053")
        self.assertEqual(project, "CB60B")

    def test_cct_range(self):
        self.assertEqual(n.cct_range(3, "X"), (32, 56))
        self.assertEqual(n.cct_range(6, "SL140"), (25, 90))
        self.assertEqual(n.cct_range(6, "SL80"), (25, 85))


class DatabaseTests(unittest.TestCase):
    def test_scenes_present_for_fx9_light(self):
        names = [name for _, name in n.scene_list(DB, 3)]
        self.assertEqual(len(names), 9)
        self.assertEqual(names[0], "Squard Car")

    def test_every_light_with_patterns_builds_power_cct_hsi(self):
        # Every database pattern must parse with dummy values (catches typos in lights.json).
        bad = []
        for t, entry in DB["lights"].items():
            for name, pattern in entry.get("commandPatterns", {}).items():
                vals = {"state": "on", "brr": 50, "cct": 40, "gm": 50, "hue": 120, "sat": 80,
                        "mac": MAC, "ccttag": 0}
                if n.build_command(pattern, vals) is None:
                    bad.append((t, name))
        self.assertEqual(bad, [], f"patterns that failed to build: {bad}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
