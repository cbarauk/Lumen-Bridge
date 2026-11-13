"""
Smooth drawing for the window.

tk.Canvas draws shapes without antialiasing, so circles, rounded corners and thin strokes look
stair-stepped. Shapes are drawn here with Pillow at SS times the size and scaled down, then shown
as images. Text stays as native tk text, which is already smooth.

Painter takes the same canvas calls the glyph functions in gui.py use (create_polygon,
create_oval, create_line, create_arc, create_rectangle), so those functions work unchanged.

tween() runs a step function over time with easing. The window uses it for colour fades,
slider glides and page slides.
"""

import math

from PIL import Image, ImageDraw

SS = 4                     # supersampling factor
FRAME_MS = 16              # about 60 frames per second
EASE_MS = 150              # default length of a colour or position change


def _rgba(col):
    """'#RRGGBB' -> (r, g, b, 255). Empty means no colour, so return None."""
    if not col:
        return None
    return (int(col[1:3], 16), int(col[3:5], 16), int(col[5:7], 16), 255)


def mix(c1, c2, t):
    """Blend two '#RRGGBB' colours. t=0 gives c1, t=1 gives c2."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02X%02X%02X" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


class Painter:
    """Draws in logical pixels. Coordinates are multiplied by SS on the way to the image."""

    def __init__(self, draw):
        self.d = draw

    @staticmethod
    def _s(v):
        return int(round(v * SS))

    def _box(self, x1, y1, x2, y2):
        return [self._s(x1), self._s(y1), self._s(x2), self._s(y2)]

    def _pts(self, flat):
        return [self._s(v) for v in flat]

    def create_polygon(self, *args, fill="", outline="", smooth=False, **kw):
        flat = args[0] if len(args) == 1 and isinstance(args[0], (list, tuple)) else list(args)
        self.d.polygon(self._pts(flat), fill=_rgba(fill))

    def create_oval(self, x1, y1, x2, y2, fill="", outline="", width=1, **kw):
        self.d.ellipse(self._box(x1, y1, x2, y2), fill=_rgba(fill),
                       outline=_rgba(outline), width=self._s(width))

    def create_rectangle(self, x1, y1, x2, y2, fill="", outline="", width=1, **kw):
        self.d.rectangle(self._box(x1, y1, x2, y2), fill=_rgba(fill),
                         outline=_rgba(outline), width=self._s(width))

    def create_line(self, *args, fill="", width=1, capstyle="butt", **kw):
        pts = args[0] if len(args) == 1 and isinstance(args[0], (list, tuple)) else list(args)
        w = self._s(width)
        col = _rgba(fill)
        pairs = [(self._s(pts[i]), self._s(pts[i + 1])) for i in range(0, len(pts), 2)]
        if len(pairs) >= 2:
            self.d.line(pairs, fill=col, width=w, joint="curve")
        if capstyle == "round" and w > 1:
            r = w / 2.0
            for x, y in (pairs[0], pairs[-1]):
                self.d.ellipse([x - r, y - r, x + r, y + r], fill=col)

    def create_arc(self, x1, y1, x2, y2, start=0, extent=90, style="arc",
                   outline="", width=1, **kw):
        # tk angles run counter-clockwise from 3 o'clock; Pillow's run clockwise, so flip them.
        a0 = (-(start + extent)) % 360
        a1 = (-start) % 360
        if a1 <= a0:
            a1 += 360
        col = _rgba(outline)
        w = self._s(width)
        box = self._box(x1, y1, x2, y2)
        self.d.arc(box, a0, a1, fill=col, width=w)
        # Round the two ends, so the stroke matches the rounded caps used elsewhere.
        cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
        rad = (box[2] - box[0]) / 2.0
        r = w / 2.0
        for ang in (a0, a1):
            ex = cx + rad * math.cos(math.radians(ang))
            ey = cy + rad * math.sin(math.radians(ang))
            self.d.ellipse([ex - r, ey - r, ex + r, ey + r], fill=col)

    def rounded(self, x1, y1, x2, y2, radius, fill):
        self.d.rounded_rectangle(self._box(x1, y1, x2, y2), radius=self._s(radius),
                                 fill=_rgba(fill))


def render(w, h, paint):
    """Run paint(Painter) at SS scale and return a logical-size RGBA image."""
    w, h = max(1, int(round(w))), max(1, int(round(h)))
    img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    paint(Painter(ImageDraw.Draw(img)))
    return img.resize((w, h), Image.LANCZOS)


def photo(img):
    """Wrap a PIL image for a tk canvas. Keep a reference to the result, or tk drops it."""
    from PIL import ImageTk  # imported here so the drawing code can be tested without tkinter
    return ImageTk.PhotoImage(img)


def _ease_out(t):
    return 1.0 - (1.0 - t) ** 3


def tween(widget, step, ms=EASE_MS, done=None, key="_tween_job"):
    """Call step(e) from 0 to 1 over ms milliseconds, eased out. Starting a new tween on the same
    widget cancels the old one, so rapid hover changes do not fight each other."""
    old = getattr(widget, key, None)
    if old is not None:
        try:
            widget.after_cancel(old)
        except Exception:
            pass
        setattr(widget, key, None)
    frames = max(1, int(ms) // FRAME_MS)

    def run(i):
        step(_ease_out(i / frames))
        if i < frames:
            setattr(widget, key, widget.after(FRAME_MS, run, i + 1))
        else:
            setattr(widget, key, None)
            if done:
                done()

    run(0)


def cancel(widget, key="_tween_job"):
    """Stop a running tween on widget, leaving its current state as it is."""
    old = getattr(widget, key, None)
    if old is not None:
        try:
            widget.after_cancel(old)
        except Exception:
            pass
        setattr(widget, key, None)
