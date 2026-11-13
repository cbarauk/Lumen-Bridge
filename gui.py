"""
Windows window for Lumen Bridge, styled after the Neewer phone app.

The window is a fixed 9:16 portrait, the same proportions as a phone screen. The home page
lists the lights. Tapping a light opens its control page: mode tabs (CCT, Colour, Scene),
a brightness slider and a bottom power bar. The activity log opens from the menu icon.

Shapes are drawn with Pillow (see aa.py) so their edges are antialiased, and text is native tk
text. Buttons, slider thumbs and list rows fade between colours, and detail pages slide in from
the right. Background threads never touch widgets: log lines go through a queue that the window
drains on a timer.
"""

import colorsys
import logging
import math
import os
import queue
import tkinter as tk

import aa
import custom_scenes as cs

# ---------------------------------------------------------------- config ----
_HERE = os.path.dirname(os.path.abspath(__file__))
ICON_NAME = "color-scheme.ico"
LEGAL_TEXT = (
    "Lumen Bridge is an independent project. It is not affiliated with, endorsed by, or sponsored "
    "by Neewer or any of its products or brands. Neewer is named only to identify the lights this "
    "program works with. Neewer is a trademark of its respective owner.\n\n"
    "Using this program to control lights is entirely at your own risk. The program and its "
    "developers are not liable for damage, malfunction, or any other loss arising from its use."
)
REFRESH_MS = 1000          # how often the window refreshes light status and drains the log
WIN_W, WIN_H = 360, 640    # 9:16 portrait, like a phone screen
WINDOW_TITLE = "Lumen Bridge"
LOG_LINES = 200            # lines kept in the log drawer
UI_FONT = "Segoe UI"
MONO_FONT = "Consolas"

# ---------------------------------------------------------------- palette ---
BG = "#2D2A2F"             # screen background
PANEL = "#221F25"          # list rows, bottom bar, log drawer
PANEL_HI = "#34313A"       # hover
BORDER = "#3E3A44"         # hairline dividers
PILL = "#5A5661"           # selected tab
BLUE = "#1E7BFF"           # power on
BLUE_HI = "#3B8BFF"
SCENE_BG = "#3A373F"
SCENE_SEL = "#7A7584"
TEXT = "#FFFFFF"
MUTED = "#A09CA8"
CYAN = "#3CE7F2"
GOOD = "#43E5A1"
OFFLINE = "#6B6772"
WARN = "#FBBF24"
ERROR = "#F87171"
LOG_INFO = "#C9C5D0"

CCT_STOPS = ["#FFA94D", "#FFF4E8", "#CFE9FF"]          # warm -> neutral -> cool
BRIGHT_STOPS = ["#6B6872", "#FFFFFF"]
RAINBOW = ["#FF0000", "#FFFF00", "#00FF00", "#00FFFF", "#0000FF", "#FF00FF", "#FF0000"]


# ---------------------------------------------------------------- helpers ---
_mix = aa.mix


def _ramp(stops, t):
    """Colour at t (0..1) along a list of hex colour stops."""
    t = min(max(t, 0.0), 1.0) * (len(stops) - 1)
    i = min(int(t), len(stops) - 2)
    return _mix(stops[i], stops[i + 1], t - i)


def _hue_hex(hue_deg):
    r, g, b = colorsys.hsv_to_rgb((hue_deg % 360) / 360.0, 1.0, 1.0)
    return "#%02X%02X%02X" % (round(r * 255), round(g * 255), round(b * 255))


def _poly(c, cx, cy, s, pts, col):
    flat = []
    for dx, dy in pts:
        flat += [cx + dx * s, cy + dy * s]
    c.create_polygon(flat, fill=col, outline="")


# ---------------------------------------------------------------- glyphs ----
# Each glyph is drawn as g(canvas, centre_x, centre_y, size, colour).
def _g_menu(c, cx, cy, s, col):
    for dy in (-7, 0, 7):
        c.create_line(cx - 9, cy + dy, cx + 9, cy + dy, fill=col, width=2, capstyle="round")


def _g_back(c, cx, cy, s, col):
    c.create_line(cx + 4, cy - 9, cx - 5, cy, cx + 4, cy + 9, fill=col, width=2.5,
                  capstyle="round", joinstyle="round")


def _g_plus(c, cx, cy, s, col):
    c.create_line(cx - 9, cy, cx + 9, cy, fill=col, width=2.5, capstyle="round")
    c.create_line(cx, cy - 9, cx, cy + 9, fill=col, width=2.5, capstyle="round")


def _g_minus(c, cx, cy, s, col):
    c.create_line(cx - 9, cy, cx + 9, cy, fill=col, width=2.5, capstyle="round")


def _g_power(c, cx, cy, s, col):
    r = s * 0.2
    c.create_arc(cx - r, cy - r, cx + r, cy + r, start=120, extent=300, style="arc",
                 outline=col, width=2.5)
    c.create_line(cx, cy - r - 3, cx, cy - 1, fill=col, width=2.5, capstyle="round")


def _g_bulb(c, cx, cy, s, col):
    r = s * 0.2
    c.create_oval(cx - r, cy - r - 3, cx + r, cy + r - 3, outline=col, width=2)
    c.create_line(cx - r * 0.5, cy + r - 1, cx + r * 0.5, cy + r - 1, fill=col, width=2)
    c.create_line(cx - r * 0.35, cy + r + 3, cx + r * 0.35, cy + r + 3, fill=col, width=2)


def _g_bolt(c, cx, cy, s, col):
    _poly(c, cx, cy, s, [(0.12, -0.36), (-0.2, 0.06), (-0.01, 0.06),
                         (-0.12, 0.36), (0.2, -0.06), (0.01, -0.06)], col)


def _g_star(c, cx, cy, s, col):
    _poly(c, cx, cy, s, [(0, -0.34), (0.07, -0.07), (0.34, 0), (0.07, 0.07),
                         (0, 0.34), (-0.07, 0.07), (-0.34, 0), (-0.07, -0.07)], col)


def _g_note(c, cx, cy, s, col):
    c.create_oval(cx - 0.24 * s, cy + 0.12 * s, cx - 0.02 * s, cy + 0.32 * s, fill=col, outline="")
    c.create_line(cx - 0.02 * s, cy + 0.22 * s, cx - 0.02 * s, cy - 0.26 * s, fill=col, width=3)
    c.create_line(cx - 0.02 * s, cy - 0.26 * s, cx + 0.2 * s, cy - 0.18 * s, fill=col, width=3)


def _g_camera(c, cx, cy, s, col):
    c.create_rectangle(cx - 0.3 * s, cy - 0.16 * s, cx + 0.3 * s, cy + 0.22 * s,
                       outline=col, width=2)
    c.create_oval(cx - 0.1 * s, cy - 0.06 * s, cx + 0.1 * s, cy + 0.14 * s, outline=col, width=2)


def _scene_glyph(name):
    n = name.lower()
    if "light" in n or "bolt" in n or "thunder" in n:
        return _g_bolt
    if "music" in n or "rhythm" in n or "party" in n:
        return _g_note
    if "paparazzi" in n or "camera" in n or "flash" in n:
        return _g_camera
    return _g_star


def _g_flame(c, cx, cy, s, col):
    _poly(c, cx, cy, s, [(0, -0.36), (0.16, -0.12), (0.22, 0.08), (0.14, 0.28), (0, 0.34),
                         (-0.14, 0.28), (-0.22, 0.08), (-0.16, -0.12)], col)


def _g_wave(c, cx, cy, s, col):
    pts = []
    for i in range(13):
        pts += [cx + (-0.36 + i * 0.06) * s, cy + 0.1 * s * math.sin(i * math.pi / 3)]
    c.create_line(pts, fill=col, width=3, smooth=True, capstyle="round")


def _g_sun(c, cx, cy, s, col):
    r = 0.14 * s
    c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=col, outline="")
    for k in range(8):
        a = k * math.pi / 4
        c.create_line(cx + math.cos(a) * 0.24 * s, cy + math.sin(a) * 0.24 * s,
                      cx + math.cos(a) * 0.36 * s, cy + math.sin(a) * 0.36 * s,
                      fill=col, width=2.5, capstyle="round")


GLYPHS = {"bolt": _g_bolt, "flame": _g_flame, "wave": _g_wave, "star": _g_star,
          "note": _g_note, "sun": _g_sun, "camera": _g_camera}


# ---------------------------------------------------------------- widgets ---
class Btn(tk.Canvas):
    """Rounded button. Colours fade on hover, darken while pressed, and fade back on release.
    Shows text, a glyph, or both."""

    def __init__(self, parent, w, h, *, parent_bg, bg=PANEL, hover=PANEL_HI, fg=TEXT,
                 text=None, font=(UI_FONT, 10, "bold"), glyph=None, circle=False, command=None):
        super().__init__(parent, width=w, height=h, bg=parent_bg, highlightthickness=0,
                         bd=0, cursor="hand2")
        self.bw, self.bh = w, h
        self.colours = {"bg": bg, "hover": hover, "fg": fg}
        self._shown = (bg, fg)        # colours on screen now; they fade towards the target
        self.label_text, self.font, self.glyph = text, font, glyph
        self.radius = min(w, h) / 2 if circle else h / 2
        self.command = command
        self.hot = False
        self.pressed = False
        self._photo = None
        self.bind("<Enter>", lambda e: self._set_hover_press(hot=True))
        self.bind("<Leave>", lambda e: self._set_hover_press(hot=False))
        self.bind("<ButtonPress-1>", lambda e: self._set_hover_press(pressed=True))
        self.bind("<ButtonRelease-1>", self._click)
        self._draw()

    def set_colours(self, bg=None, hover=None, fg=None):
        if bg is not None:
            self.colours["bg"] = bg
        if hover is not None:
            self.colours["hover"] = hover
        if fg is not None:
            self.colours["fg"] = fg
        self._fade()

    def _set_hover_press(self, hot=None, pressed=None):
        if hot is not None:
            self.hot = hot
        if pressed is not None:
            self.pressed = pressed
        self._fade()

    def _target(self):
        bg = self.colours["hover"] if self.hot else self.colours["bg"]
        if self.pressed:
            bg = _mix(bg, "#000000", 0.22)
        return bg, self.colours["fg"]

    def _fade(self):
        bg, fg = self._target()
        start_bg, start_fg = self._shown
        if (bg, fg) == (start_bg, start_fg):
            return

        def step(e):
            self._shown = (_mix(start_bg, bg, e), _mix(start_fg, fg, e))
            self._draw()

        aa.tween(self, step, ms=120)

    def _click(self, e):
        self._set_hover_press(pressed=False)
        if self.command and 0 <= e.x <= self.bw and 0 <= e.y <= self.bh:
            self.command()

    def _draw(self):
        bg, fg = self._shown
        r = min(self.radius, self.bw / 2, self.bh / 2)

        def paint(p):
            p.rounded(1, 1, self.bw - 1, self.bh - 1, r, bg)
            if self.glyph:
                self.glyph(p, self.bw / 2, self.bh / 2, min(self.bw, self.bh), fg)

        self.delete("all")
        self._photo = aa.photo(aa.render(self.bw, self.bh, paint))
        self.create_image(0, 0, anchor="nw", image=self._photo)
        if self.label_text:
            self.create_text(self.bw / 2, self.bh / 2, text=self.label_text, fill=fg,
                             font=self.font)


class Slider(tk.Frame):
    """Name and value on one line, then a gradient track with minus and plus buttons.
    The thumb glides to a new value when a button is pressed, and follows the pointer while dragging."""

    def __init__(self, parent, *, name, from_, to, value, stops, step, fmt, on_change, bg=BG):
        super().__init__(parent, bg=bg)
        self.from_, self.to, self.step = from_, to, step
        self.value = float(value)
        self.shown = float(value)     # where the thumb is drawn; glides towards value
        self.stops, self.fmt, self.on_change = stops, fmt, on_change
        self._track_w = 0
        self._photo = None

        head = tk.Frame(self, bg=bg)
        head.pack(fill="x")
        tk.Label(head, text=name.upper(), bg=bg, fg=MUTED,
                 font=(UI_FONT, 8, "bold")).pack(side="left")
        self.readout = tk.Label(head, text=fmt(self.value), bg=bg, fg=TEXT,
                                font=(UI_FONT, 10, "bold"))
        self.readout.pack(side="right")

        row = tk.Frame(self, bg=bg)
        row.pack(fill="x", pady=(8, 0))
        Btn(row, 30, 30, parent_bg=bg, bg=PANEL, hover=PANEL_HI, glyph=_g_minus, circle=True,
            command=lambda: self._nudge(-step)).pack(side="left")
        Btn(row, 30, 30, parent_bg=bg, bg=PANEL, hover=PANEL_HI, glyph=_g_plus, circle=True,
            command=lambda: self._nudge(step)).pack(side="right")

        self.track = tk.Canvas(row, height=30, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.track.pack(side="left", fill="x", expand=True, padx=8)
        self.track.bind("<Configure>", lambda e: self._on_resize())
        self.track.bind("<Button-1>", lambda e: self._from_x(e.x, glide=True))
        self.track.bind("<B1-Motion>", lambda e: self._from_x(e.x, glide=False))

    def set_stops(self, stops):
        self.stops = stops
        self._redraw()

    def _on_resize(self):
        self._track_w = max(self.track.winfo_width(), 21)
        self._redraw()

    def _from_x(self, x, glide):
        w = max(self.track.winfo_width(), 21)
        t = min(max((x - 10) / (w - 20), 0.0), 1.0)
        v = self.from_ + t * (self.to - self.from_)
        self._set_value(round(v / self.step) * self.step, notify=True, glide=glide)

    def _nudge(self, delta):
        self._set_value(self.value + delta, notify=True, glide=True)

    def _set_value(self, v, notify, glide):
        v = min(max(v, self.from_), self.to)
        if v == self.value:
            return
        self.value = v
        self.readout.config(text=self.fmt(v))
        if glide:
            start = self.shown

            def step(e):
                self.shown = start + (v - start) * e
                self._redraw()

            aa.tween(self, step, ms=160, key="_glide_job")
        else:
            aa.cancel(self, "_glide_job")
            self.shown = v
            self._redraw()
        if notify and self.on_change:
            self.on_change(v)

    def _redraw(self):
        w = self._track_w or max(self.track.winfo_width(), 21)
        span = w - 20
        t = (self.shown - self.from_) / ((self.to - self.from_) or 1)
        x = 10 + t * span
        stops = self.stops

        def paint(p):
            n = 48
            for i in range(n):
                x0 = 10 + span * i / n
                x1 = 10 + span * (i + 1) / n + 1
                p.create_rectangle(x0, 10, x1, 20, fill=_ramp(stops, i / (n - 1)), outline="")
            p.create_oval(5, 10, 15, 20, fill=stops[0], outline="")
            p.create_oval(w - 15, 10, w - 5, 20, fill=stops[-1], outline="")
            p.create_oval(x - 10, 5, x + 10, 25, fill="#FFFFFF", outline="#D9D9DE", width=1)

        self.track.delete("all")
        self._photo = aa.photo(aa.render(w, 30, paint))
        self.track.create_image(0, 0, anchor="nw", image=self._photo)


class Thumb(tk.Canvas):
    """Slim scroll indicator that follows a canvas's scroll position. Hidden when nothing scrolls."""

    def __init__(self, parent, target):
        super().__init__(parent, width=6, bg=BG, highlightthickness=0, bd=0)
        self.first, self.last = 0.0, 1.0
        self._photo = None
        target.configure(yscrollcommand=self._moved)
        self.bind("<Configure>", lambda e: self._draw())

    def _moved(self, first, last):
        self.first, self.last = float(first), float(last)
        self._draw()

    def _draw(self):
        self.delete("all")
        if self.last - self.first >= 0.999:
            return
        h = max(self.winfo_height(), 1)
        y1, y2 = self.first * h + 4, self.last * h - 4

        def paint(p):
            p.create_line(3, y1, 3, y2, fill=MUTED, width=4, capstyle="round")

        self._photo = aa.photo(aa.render(6, h, paint))
        self.create_image(0, 0, anchor="nw", image=self._photo)


class LightRow(tk.Canvas):
    """One light in the home list: bulb tile, name, status and a menu dots icon. Fades on hover."""
    H = 72

    def __init__(self, parent, light, on_open):
        self.w = WIN_W - 32
        super().__init__(parent, width=self.w, height=self.H, bg=BG, highlightthickness=0,
                         bd=0, cursor="hand2")
        self.addr = light["address"]
        self.light = light
        self.on_open = on_open
        self.hot = False
        self._shown_bg = PANEL
        self._photo = None
        self.pack(pady=5)
        self.bind("<Button-1>", lambda e: self.on_open(self.addr))
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.draw()

    def update_light(self, light):
        self.light = light
        self.draw()

    def _hover(self, on):
        self.hot = on
        target = PANEL_HI if on else PANEL
        start = self._shown_bg

        def step(e):
            self._shown_bg = aa.mix(start, target, e)
            self.draw()

        aa.tween(self, step, ms=140, key="_hover_job")

    def draw(self):
        self.delete("all")
        w, on, card = self.w, self.light["connected"], self._shown_bg

        def paint(p):
            p.rounded(0, 0, w, self.H, 18, card)
            p.rounded(12, 12, 60, 60, 12, SCENE_BG)
            _g_bulb(p, 36, 36, 48, TEXT)
            p.create_oval(74, 46, 82, 54, fill=GOOD if on else OFFLINE, outline="")
            for y in (24, 36, 48):
                p.create_oval(w - 26, y - 2.5, w - 21, y + 2.5, fill=MUTED, outline="")

        self._photo = aa.photo(aa.render(w, self.H, paint))
        self.create_image(0, 0, anchor="nw", image=self._photo)
        self.create_text(74, 27, text=self.light["name"], anchor="w", fill=TEXT,
                         font=(UI_FONT, 11, "bold"))
        self.create_text(90, 50, text="Connected" if on else "Offline", anchor="w",
                         fill=GOOD if on else MUTED, font=(UI_FONT, 9))


# ---------------------------------------------------------------- app -------
class _QueueLogHandler(logging.Handler):
    def __init__(self, q):
        super().__init__()
        self.q = q

    def emit(self, record):
        self.q.put((record.levelno, self.format(record)))


class App:
    def __init__(self, root, controller):
        self.root = root
        self.ctl = controller
        self.logq = queue.Queue()
        self.rows = {}          # address -> LightRow on the home list
        self.light_info = {}    # address -> last light dict from the controller
        self.details = {}       # address -> detail page state
        self.soft = {}          # address -> custom scene Runner playing on that light
        self.current = None     # address of the open detail page, None on home
        self.log_shown = False
        self._scroll = None     # canvas the mouse wheel scrolls

        root.title(WINDOW_TITLE)
        self._apply_icon(root)
        root.geometry(f"{WIN_W}x{WIN_H}")
        root.resizable(False, False)
        root.configure(bg=BG)

        # top bar: menu or back on the left, action on the right, title in the middle
        self.topbar = tk.Frame(root, bg=BG, height=64)
        self.topbar.pack(side="top", fill="x")
        self.topbar.pack_propagate(False)
        self.left_slot = tk.Frame(self.topbar, bg=BG, width=60)
        self.left_slot.pack(side="left", fill="y")
        self.left_slot.pack_propagate(False)
        self.right_slot = tk.Frame(self.topbar, bg=BG, width=76)
        self.right_slot.pack(side="right", fill="y")
        self.right_slot.pack_propagate(False)
        mid = tk.Frame(self.topbar, bg=BG)
        mid.pack(side="left", fill="both", expand=True)
        self.title = tk.Label(mid, bg=BG, fg=TEXT, font=(UI_FONT, 14, "bold"))
        self.title.pack(pady=(10, 0))
        self.subtitle = tk.Label(mid, bg=BG, fg=MUTED, font=(UI_FONT, 9))
        self.subtitle.pack()

        # activity log drawer, shown from the menu on the home page
        self.log_frame = tk.Frame(root, bg=PANEL)
        tk.Label(self.log_frame, text="ACTIVITY", bg=PANEL, fg=MUTED,
                 font=(UI_FONT, 8, "bold")).pack(anchor="w", padx=14, pady=(10, 4))
        self.log_box = tk.Text(self.log_frame, height=7, state="disabled", wrap="word", bd=0,
                               bg=PANEL, fg=LOG_INFO, font=(MONO_FONT, 9), relief="flat",
                               highlightthickness=0, padx=14, pady=4, insertbackground=TEXT,
                               selectbackground=PANEL_HI, selectforeground=TEXT)
        self.log_box.pack(fill="both", expand=True, pady=(0, 8))
        self.log_box.tag_configure("info", foreground=LOG_INFO)
        self.log_box.tag_configure("warn", foreground=WARN)
        self.log_box.tag_configure("error", foreground=ERROR)

        self.content = tk.Frame(root, bg=BG)
        self.content.pack(fill="both", expand=True)

        self._build_home()
        self._show_home()

        handler = _QueueLogHandler(self.logq)
        handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
        handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(handler)

        root.bind_all("<MouseWheel>", self._on_wheel)
        root.after(REFRESH_MS, self._tick)

    # ---------------------------------------------------------- navigation
    def _set_slot(self, slot, make):
        for w in slot.winfo_children():
            w.destroy()
        if make:
            make(slot).pack(expand=True)

    def _build_home(self):
        self.home = tk.Frame(self.content, bg=BG)

        bar = tk.Frame(self.home, bg=BG)
        bar.pack(fill="x", padx=16, pady=(4, 12))
        Btn(bar, 160, 42, parent_bg=BG, bg=BLUE, hover=BLUE_HI, text="All on",
            font=(UI_FONT, 11, "bold"), command=lambda: self.ctl.power(True)).pack(side="left")
        Btn(bar, 160, 42, parent_bg=BG, bg=PANEL, hover=PANEL_HI, text="All off",
            font=(UI_FONT, 11, "bold"), command=self._all_off).pack(side="right")

        # footer with a small "Legal" link; the text stays hidden until it is clicked
        footer = tk.Frame(self.home, bg=BG)
        footer.pack(side="bottom", fill="x", padx=16, pady=(0, 4))
        self.legal_link = tk.Label(footer, bg=BG, fg=MUTED, font=(UI_FONT, 8, "underline"),
                                   text="Legal", cursor="hand2")
        self.legal_link.pack(side="right")
        self.legal_link.bind("<Button-1>", lambda e: self._toggle_legal())
        self.legal_text = tk.Label(footer, bg=BG, fg=MUTED, font=(UI_FONT, 8), justify="left",
                                   wraplength=WIN_W - 48, text=LEGAL_TEXT)
        self.legal_open = False

        self.home_canvas = tk.Canvas(self.home, bg=BG, highlightthickness=0, bd=0)
        self.home_canvas.pack(fill="both", expand=True, padx=16)
        self.home_inner = tk.Frame(self.home_canvas, bg=BG)
        win = self.home_canvas.create_window((0, 0), window=self.home_inner, anchor="nw")
        self.home_inner.bind("<Configure>", lambda e: self.home_canvas.configure(
            scrollregion=self.home_canvas.bbox("all")))
        self.home_canvas.bind("<Configure>", lambda e: self.home_canvas.itemconfigure(
            win, width=e.width))
        self.placeholder = tk.Label(
            self.home_inner, bg=BG, fg=MUTED, font=(UI_FONT, 10), justify="center",
            text="No lights yet.\nTurn a Neewer light on, then press Scan.")

    def _show_home(self):
        if self.current is not None and self.current in self.details:
            frame = self.details[self.current]["frame"]
            self._slide(frame, 0, WIN_W, done=frame.place_forget)
        self.current = None
        self.home.pack(fill="both", expand=True)
        self._scroll = self.home_canvas
        self.title.config(text="My lights")
        self._set_slot(self.left_slot, lambda p: Btn(
            p, 40, 40, parent_bg=BG, bg=BG, hover=PANEL, glyph=_g_menu, circle=True,
            command=self._toggle_log))
        self._set_slot(self.right_slot, lambda p: Btn(
            p, 64, 30, parent_bg=BG, bg=PANEL, hover=PANEL_HI, text="Scan",
            font=(UI_FONT, 9, "bold"), command=self._scan))
        self._place_log()

    def _show_detail(self, addr):
        if self.current is not None and self.current in self.details:
            self.details[self.current]["frame"].place_forget()
        self.current = addr
        card = self.details[addr]
        frame = card["frame"]
        frame.place(x=WIN_W, y=0, relwidth=1, relheight=1)
        frame.lift()
        self._slide(frame, WIN_W, 0)
        self._set_slot(self.left_slot, lambda p: Btn(
            p, 40, 40, parent_bg=BG, bg=BG, hover=PANEL, glyph=_g_back, circle=True,
            command=self._show_home))
        self._set_slot(self.right_slot, None)
        self.title.config(text=self.light_info[addr]["name"])
        self._set_mode(addr, card["mode"], send=False)
        self._place_log()

    @staticmethod
    def _apply_icon(root):
        """Set the title bar and taskbar icon. Looks in assets/ and next to gui.py, and logs where
        it looked so a missing file shows up in the log panel."""
        candidates = [os.path.join(_HERE, "assets", ICON_NAME), os.path.join(_HERE, ICON_NAME)]
        path = next((c for c in candidates if os.path.isfile(c)), None)
        if path is None:
            logging.warning("Window icon not found; looked in: %s", ", ".join(candidates))
            return
        try:
            # Pillow reads the .ico and Tk gets it as a photo, so the icon does not depend on
            # Windows loading the file directly.
            from PIL import Image, ImageTk
            photo = ImageTk.PhotoImage(Image.open(path).convert("RGBA"))
            root.iconphoto(True, photo)
            root._app_icon = photo          # keep a reference, or Tk drops the image
            logging.info("Window icon loaded from %s", path)
        except Exception as exc:
            logging.warning("Could not load window icon from %s: %s", path, exc)
            try:
                root.iconbitmap(path)
            except tk.TclError as exc2:
                logging.warning("Windows also refused the icon file: %s", exc2)

    def _slide(self, frame, x_from, x_to, done=None):
        def step(e):
            frame.place_configure(x=int(round(x_from + (x_to - x_from) * e)))
        aa.tween(frame, step, ms=220, done=done, key="_slide_job")

    def _toggle_log(self):
        self.log_shown = not self.log_shown
        self._place_log()

    def _place_log(self):
        """The log drawer only shows on the home page, so it never squeezes a light's controls."""
        if self.log_shown and self.current is None:
            self.log_frame.pack(side="bottom", fill="x", before=self.content)
        else:
            self.log_frame.pack_forget()

    def _scan(self):
        self.ctl.scan()

    def _toggle_legal(self):
        self.legal_open = not self.legal_open
        if self.legal_open:
            self.legal_text.pack(fill="x", pady=(0, 4), before=self.legal_link)
        else:
            self.legal_text.pack_forget()

    def _on_wheel(self, e):
        if self._scroll is not None:
            self._scroll.yview_scroll(int(-e.delta / 120), "units")

    # ---------------------------------------------------------- refresh loop
    def _tick(self):
        self._drain_log()
        lights = self.ctl.list_lights()
        if lights:
            self.placeholder.pack_forget()
        else:
            self.placeholder.pack(pady=60)
        for light in lights:
            addr = light["address"]
            self.light_info[addr] = light
            if addr not in self.rows:
                self.rows[addr] = LightRow(self.home_inner, light, self._open_detail)
            self.rows[addr].update_light(light)

        if self.current is None:
            connected = sum(1 for l in lights if l["connected"])
            self.subtitle.config(text=f"{connected} of {len(lights)} lights connected")
        elif self.current in self.light_info:
            on = self.light_info[self.current]["connected"]
            self.subtitle.config(text=("Connected" if on else "Offline") + "  ·  " + self.current)
        self.root.after(REFRESH_MS, self._tick)

    def _drain_log(self):
        lines = []
        while True:
            try:
                lines.append(self.logq.get_nowait())
            except queue.Empty:
                break
        if not lines:
            return
        box = self.log_box
        box.config(state="normal")
        for levelno, text in lines:
            if levelno >= logging.ERROR:
                tag = "error"
            elif levelno >= logging.WARNING:
                tag = "warn"
            else:
                tag = "info"
            box.insert("end", text + "\n", tag)
        extra = int(box.index("end-1c").split(".")[0]) - LOG_LINES
        if extra > 0:
            box.delete("1.0", f"{extra + 1}.0")
        box.see("end")
        box.config(state="disabled")

    # ---------------------------------------------------------- light page
    def _open_detail(self, addr):
        if addr not in self.details:
            self._build_detail(self.light_info[addr])
        self._show_detail(addr)

    def _build_detail(self, light):
        addr = light["address"]
        f = tk.Frame(self.content, bg=BG)
        card = {"frame": f, "mode": None, "tabs": {}, "modes": {}, "scene_cells": {},
                "scene_id": None, "scene_canvas": None,
                "brr": float(light["brightness"] or 100),
                "kelvin": float(light["cct"] or 5600), "hue": 210.0, "sat": 100.0}

        modes = []
        if light["canCCT"]:
            modes.append(("cct", "CCT"))
        if light["canHSI"]:
            modes.append(("colour", "Colour"))
        if light["canCCT"] or light["canHSI"]:
            modes.append(("scene", "Scene"))

        # Pack the top (tabs) and the bottom (power bar, brightness) first, so the mode area
        # takes whatever height is left between them.
        tabs = tk.Frame(f, bg=BG)
        tabs.pack(side="top", pady=(14, 6))
        for key, label in modes:
            b = Btn(tabs, 84, 30, parent_bg=BG, bg=BG, hover=PANEL, fg=MUTED, text=label,
                    command=lambda k=key: self._set_mode(addr, k))
            b.pack(side="left", padx=4)
            card["tabs"][key] = b

        bar_w = WIN_W - 40
        bar = tk.Canvas(f, width=bar_w, height=64, bg=BG, highlightthickness=0, bd=0)
        card["bar_img"] = aa.photo(aa.render(bar_w, 64, lambda p: p.rounded(0, 0, bar_w, 64, 32, PANEL)))
        bar.create_image(0, 0, anchor="nw", image=card["bar_img"])
        bar.create_window(bar_w / 2 - 36, 32, window=Btn(
            bar, 48, 48, parent_bg=PANEL, bg=BLUE, hover=BLUE_HI, glyph=_g_power, circle=True,
            command=lambda: self.ctl.power(True, addr)))
        bar.create_window(bar_w / 2 + 36, 32, window=Btn(
            bar, 48, 48, parent_bg=PANEL, bg=SCENE_BG, hover=SCENE_SEL, fg=MUTED,
            glyph=_g_power, circle=True, command=lambda: self._power_off(addr)))
        bar.pack(side="bottom", pady=(8, 18))

        bright = Slider(f, name="Brightness", from_=0, to=100, value=card["brr"],
                        stops=BRIGHT_STOPS, step=1, fmt=lambda v: f"{int(v)}%",
                        on_change=lambda v: self._brightness(addr, v))
        bright.pack(side="bottom", fill="x", padx=24, pady=(0, 4))
        tk.Frame(f, bg=BORDER, height=1).pack(side="bottom", fill="x", padx=24, pady=(10, 12))

        body = tk.Frame(f, bg=BG)
        body.pack(side="top", fill="both", expand=True)

        if light["canCCT"]:
            lo, hi = light["cctRange"]
            fr = tk.Frame(body, bg=BG)
            Slider(fr, name="Colour temp", from_=lo * 100, to=hi * 100, value=card["kelvin"],
                   stops=CCT_STOPS, step=100,
                   fmt=lambda v: f"{int(round(v / 100)) * 100}K",
                   on_change=lambda v: self._cct(addr, v)).pack(fill="x", padx=24, pady=(28, 0))
            card["modes"]["cct"] = fr

        if light["canHSI"]:
            fr = tk.Frame(body, bg=BG)
            hue = Slider(fr, name="Hue", from_=0, to=360, value=card["hue"], stops=RAINBOW,
                         step=1, fmt=lambda v: f"{int(v)}°",
                         on_change=lambda v: self._hue(addr, v))
            hue.pack(fill="x", padx=24, pady=(28, 0))
            sat = Slider(fr, name="Saturation", from_=0, to=100, value=card["sat"],
                         stops=["#FFFFFF", _hue_hex(card["hue"])], step=1,
                         fmt=lambda v: f"{int(v)}%", on_change=lambda v: self._sat(addr, v))
            sat.pack(fill="x", padx=24, pady=(24, 0))
            card["sat_slider"] = sat
            card["modes"]["colour"] = fr

        if light["canCCT"] or light["canHSI"]:
            fr = tk.Frame(body, bg=BG)
            cv = tk.Canvas(fr, bg=BG, highlightthickness=0, bd=0)
            cv.pack(fill="both", expand=True, padx=(16, 22), pady=(6, 0))
            grid = tk.Frame(cv, bg=BG)
            for c in range(4):
                grid.columnconfigure(c, weight=1, uniform="scene")
            win = cv.create_window((0, 0), window=grid, anchor="nw")
            grid.bind("<Configure>", lambda e, c=cv: c.configure(scrollregion=c.bbox("all")))
            cv.bind("<Configure>", lambda e, c=cv, w=win: c.itemconfigure(w, width=e.width))
            Thumb(fr, cv).place(relx=1.0, x=-8, y=8, anchor="ne", relheight=1.0, height=-16)
            row = 0
            if light["scenes"]:
                row = self._scene_heading(grid, "BUILT-IN", row, MUTED)
                row = self._scene_cells(grid, row, addr, card, [
                    (s["id"], s["name"], _scene_glyph(s["name"])) for s in light["scenes"]])
            row = self._scene_heading(grid, "CUSTOM", row, CYAN)
            self._scene_cells(grid, row, addr, card, [
                (cs.soft_id(c.key), c.name, GLYPHS[c.glyph]) for c in cs.SCENES])
            card["scene_canvas"] = cv
            card["modes"]["scene"] = fr

        self.details[addr] = card
        card["mode"] = modes[0][0] if modes else None
        self._set_mode(addr, card["mode"], send=False)

    def _set_mode(self, addr, mode, send=True):
        card = self.details[addr]
        card["mode"] = mode
        if mode != "scene":
            self._stop_soft(addr)
        for key, b in card["tabs"].items():
            sel = key == mode
            b.set_colours(bg=PILL if sel else BG, hover=PILL if sel else PANEL,
                          fg=TEXT if sel else MUTED)
        for fr in card["modes"].values():
            fr.pack_forget()
        if mode in card["modes"]:
            card["modes"][mode].pack(fill="both", expand=True)
        self._scroll = card["scene_canvas"] if mode == "scene" else None
        if send:
            self._send(addr)

    def _send(self, addr):
        """Send the command for the current mode, using the values every control holds."""
        if addr in self.soft:
            return
        card = self.details[addr]
        brr = int(round(card["brr"]))
        mode = card["mode"]
        if mode == "cct":
            self.ctl.set_cct(brr, int(round(card["kelvin"] / 100.0)) * 100, 0, addr)
        elif mode == "colour":
            self.ctl.set_hsi(int(card["hue"]) % 360, int(card["sat"]), brr, addr)
        elif mode == "scene" and card["scene_id"] is not None:
            self.ctl.set_scene(card["scene_id"], brr, addr)
        else:
            self.ctl.set_brightness(brr, addr)

    def _brightness(self, addr, v):
        self.details[addr]["brr"] = v
        self._send(addr)

    def _cct(self, addr, v):
        self.details[addr]["kelvin"] = v
        self._send(addr)

    def _hue(self, addr, v):
        card = self.details[addr]
        card["hue"] = v
        card["sat_slider"].set_stops(["#FFFFFF", _hue_hex(v)])
        self._send(addr)

    def _sat(self, addr, v):
        self.details[addr]["sat"] = v
        self._send(addr)

    def _scene(self, addr, sid):
        card = self.details[addr]
        key = cs.key_from_id(sid)
        if key:
            self._start_soft(addr, key)
        else:
            self._stop_soft(addr)
            card["scene_id"] = sid
            self._send(addr)
        for k, b in card["scene_cells"].items():
            sel = k == sid
            b.set_colours(bg=SCENE_SEL if sel else SCENE_BG, hover=SCENE_SEL)

    def _scene_heading(self, grid, text, row, colour):
        tk.Label(grid, text=text, bg=BG, fg=colour, font=(UI_FONT, 8, "bold")).grid(
            row=row, column=0, columnspan=4, sticky="w", pady=(12 if row else 2, 2))
        return row + 1

    def _scene_cells(self, grid, row, addr, card, items):
        for i, (sid, name, glyph) in enumerate(items):
            cell = tk.Frame(grid, bg=BG)
            cell.grid(row=row + i // 4, column=i % 4, pady=4, sticky="n")
            b = Btn(cell, 56, 56, parent_bg=BG, bg=SCENE_BG, hover=SCENE_SEL, glyph=glyph,
                    circle=True, command=lambda s=sid: self._scene(addr, s))
            b.pack()
            tk.Label(cell, text=name, bg=BG, fg=MUTED, font=(UI_FONT, 8), wraplength=84,
                     justify="center").pack(pady=(4, 0))
            card["scene_cells"][sid] = b
        return row + (len(items) + 3) // 4

    # ---------------------------------------------------------- custom scenes
    def _start_soft(self, addr, key):
        self._stop_soft(addr)
        info = self.light_info[addr]
        rng = tuple(v * 100 for v in info["cctRange"]) if info["canCCT"] else (2700, 6500)
        self.soft[addr] = cs.Runner(
            self.ctl, addr, cs.SCENE_BY_KEY[key],
            lambda: self.details[addr]["brr"], rng, info["canHSI"]).start()
        self.details[addr]["scene_id"] = cs.soft_id(key)

    def _stop_soft(self, addr):
        runner = self.soft.pop(addr, None)
        if runner:
            runner.stop()
        card = self.details.get(addr)
        if card and cs.key_from_id(card["scene_id"]):
            card["scene_id"] = None

    def _power_off(self, addr):
        self._stop_soft(addr)
        self.ctl.power(False, addr)

    def _all_off(self):
        for addr in list(self.soft):
            self._stop_soft(addr)
        self.ctl.power(False)
