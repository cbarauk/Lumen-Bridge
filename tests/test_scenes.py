"""Custom scenes: values stay in range, and the runner sends frames and stops cleanly."""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import custom_scenes as cs  # noqa: E402


class FakeController:
    def __init__(self):
        self.calls = []

    def set_hsi(self, h, s, b, addr):
        self.calls.append(("hsi", h, s, b, addr))

    def set_cct(self, b, k, gm, addr):
        self.calls.append(("cct", b, k, gm, addr))


class SceneTests(unittest.TestCase):
    def test_every_scene_stays_in_range(self):
        for cls in cs.SCENES:
            scene = cls()
            for i in range(400):
                f = scene.frame(i * 0.15, 80)
                self.assertGreaterEqual(f["brr"], 0, cls.key)
                self.assertLessEqual(f["brr"], 80.001, cls.key)
                self.assertGreaterEqual(f["k"], 1000, cls.key)

    def test_scene_ids_are_unique_and_round_trip(self):
        keys = [c.key for c in cs.SCENES]
        self.assertEqual(len(keys), len(set(keys)))
        for k in keys:
            self.assertEqual(cs.key_from_id(cs.soft_id(k)), k)
        self.assertIsNone(cs.key_from_id(7))

    def test_runner_sends_cct_for_lights_without_hsi(self):
        ctl = FakeController()
        r = cs.Runner(ctl, "AA", cs.Thunderstorm, lambda: 70, (2900, 7000), has_hsi=False).start()
        time.sleep(0.5)
        r.stop()
        time.sleep(0.3)
        self.assertTrue(ctl.calls)
        self.assertTrue(all(c[0] == "cct" for c in ctl.calls))
        self.assertTrue(all(2900 <= c[2] <= 7000 for c in ctl.calls))

    def test_runner_stops(self):
        ctl = FakeController()
        r = cs.Runner(ctl, "AA", cs.NeonDrift, lambda: 50, (2900, 7000), has_hsi=True).start()
        time.sleep(0.4)
        r.stop()
        time.sleep(0.3)
        n = len(ctl.calls)
        time.sleep(0.4)
        self.assertEqual(len(ctl.calls), n)


if __name__ == "__main__":
    unittest.main()
