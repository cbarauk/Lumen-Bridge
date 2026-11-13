"""Antialiased drawing and tweens: edges are soft, colours and easing land on their targets."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import aa  # noqa: E402


class FakeWidget:
    """Stands in for a tk widget: after() queues a callback and run_all() runs the queue."""

    def __init__(self):
        self.jobs = []
        self.count = 0

    def after(self, ms, fn, *args):
        self.count += 1
        jid = f"job{self.count}"
        self.jobs.append((jid, fn, args))
        return jid

    def after_cancel(self, jid):
        self.jobs = [j for j in self.jobs if j[0] != jid]

    def run_all(self, limit=1000):
        while self.jobs and limit:
            _, fn, args = self.jobs.pop(0)
            fn(*args)
            limit -= 1


class RenderTests(unittest.TestCase):
    def test_circle_edge_pixels_are_partly_transparent(self):
        img = aa.render(40, 40, lambda p: p.create_oval(4, 4, 36, 36, fill="#FFFFFF", outline=""))
        alphas = {img.getpixel((x, 20))[3] for x in range(0, 12)}
        # A hard-edged shape would only ever have alpha 0 or 255 along its boundary.
        self.assertTrue(any(0 < a < 255 for a in alphas), alphas)

    def test_centre_is_solid_and_corner_is_clear(self):
        img = aa.render(40, 40, lambda p: p.create_oval(4, 4, 36, 36, fill="#FFFFFF", outline=""))
        self.assertEqual(img.getpixel((20, 20))[3], 255)
        self.assertEqual(img.getpixel((0, 0))[3], 0)

    def test_rounded_rect_fill_colour_is_kept(self):
        img = aa.render(30, 30, lambda p: p.rounded(0, 0, 30, 30, 10, "#3CE7F2"))
        self.assertEqual(img.getpixel((15, 15))[:3], (0x3C, 0xE7, 0xF2))

    def test_arc_draws_only_its_span(self):
        # tk's arc: start 120, extent 300 leaves a gap at the top, where the power icon opens.
        img = aa.render(40, 40, lambda p: p.create_arc(8, 8, 32, 32, start=120, extent=300,
                                                       style="arc", outline="#FFFFFF", width=2))
        self.assertEqual(img.getpixel((20, 20))[3], 0)   # centre is empty
        self.assertTrue(any(img.getpixel((x, y))[3] > 0 for x in range(40) for y in range(40)))


class MixTests(unittest.TestCase):
    def test_endpoints(self):
        self.assertEqual(aa.mix("#000000", "#FFFFFF", 0), "#000000")
        self.assertEqual(aa.mix("#000000", "#FFFFFF", 1), "#FFFFFF")

    def test_midpoint(self):
        self.assertEqual(aa.mix("#000000", "#FFFFFF", 0.5), "#808080")


class TweenTests(unittest.TestCase):
    def test_tween_reaches_one_and_calls_done(self):
        w, seen, done = FakeWidget(), [], []
        aa.tween(w, seen.append, ms=80, done=lambda: done.append(True))
        w.run_all()
        self.assertAlmostEqual(seen[-1], 1.0)
        self.assertEqual(done, [True])
        self.assertEqual(seen, sorted(seen))             # eases forward, never backwards

    def test_new_tween_replaces_the_old_one(self):
        w, a, b = FakeWidget(), [], []
        aa.tween(w, a.append, ms=800)
        aa.tween(w, b.append, ms=80)
        w.run_all()
        self.assertEqual(len(a), 1)                          # only its first step ran before it was replaced
        self.assertTrue(b and abs(b[-1] - 1.0) < 1e-9)

    def test_cancel_stops_further_steps(self):
        w, seen = FakeWidget(), []
        aa.tween(w, seen.append, ms=800)
        n = len(seen)
        aa.cancel(w)
        w.run_all()
        self.assertEqual(len(seen), n)


if __name__ == "__main__":
    unittest.main()
