"""
Custom scenes for Lumen Bridge. The PC plays them, not the light.

The built-in scenes run inside each light's firmware, so their list is fixed. These scenes are
sequences of colour and brightness commands, sent about six times a second, so they work on any
light with CCT or HSI. Scenes that depend on hue fall back to colour temperature on lights
without HSI, so every scene works on every light.

To add a scene: subclass Scene, give it key, name and glyph, write frame(), and add it to SCENES.
"""

import logging
import math
import random
import threading
import time

log = logging.getLogger("lumenbridge.scenes")

FRAME_S = 0.15     # seconds between frames (about 6-7 per second)


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _sine(t, period, lo=0.0, hi=1.0):
    """Sine wave running between lo and hi with the given period in seconds."""
    return lo + (hi - lo) * (0.5 + 0.5 * math.sin(2 * math.pi * t / period))


class Scene:
    """frame(t, b) returns a dict with h (hue 0-360), s (sat 0-100), k (kelvin), brr (0-100).
    t is seconds since the scene started, b is the brightness the user set (0-100)."""
    key = ""
    name = ""
    glyph = "star"

    def frame(self, t, b):
        raise NotImplementedError


class Thunderstorm(Scene):
    key = "thunderstorm"
    name = "Thunderstorm"
    glyph = "bolt"

    def __init__(self):
        self.rng = random.Random()
        self.next_flash = self.rng.uniform(1.5, 4.0)
        self.flash_start = None

    def frame(self, t, b):
        if self.flash_start is None and t >= self.next_flash:
            self.flash_start = t
        level = 0.08                                  # dim, cool, between flashes
        if self.flash_start is not None:
            dt = t - self.flash_start
            if dt < 0.35:
                level = 1.0                           # first flash
            elif 0.5 <= dt < 0.75:
                level = 0.6                           # echo flash
            else:
                if dt >= 0.75:
                    self.flash_start = None
                    self.next_flash = t + self.rng.uniform(3.0, 9.0)
        return {"h": 215, "s": 25, "k": 6500, "brr": b * level}


class Campfire(Scene):
    key = "campfire"
    name = "Campfire"
    glyph = "flame"

    def __init__(self):
        self.rng = random.Random()
        self.level = 0.6
        self.target = 0.6

    def frame(self, t, b):
        if self.rng.random() < 0.3:
            self.target = self.rng.uniform(0.3, 1.0)
        self.level += (self.target - self.level) * 0.35      # ease towards the target
        h = 18 + 6 * math.sin(t * 1.7)
        return {"h": h, "s": 95, "k": 2200, "brr": b * (0.2 + 0.7 * self.level)}


class Ocean(Scene):
    key = "ocean"
    name = "Ocean waves"
    glyph = "wave"

    def frame(self, t, b):
        return {
            "h": 196 + 16 * math.sin(2 * math.pi * t / 9),
            "s": 85 + 12 * math.sin(2 * math.pi * t / 13),
            "k": 9000,
            "brr": b * _sine(t, 7, 0.45, 1.0),
        }


class Aurora(Scene):
    key = "aurora"
    name = "Aurora"
    glyph = "star"

    def frame(self, t, b):
        shimmer = 0.2 * _sine(t, 5.3) + 0.35 * _sine(t, 23)
        return {
            "h": 205 + 85 * math.sin(2 * math.pi * t / 40),     # drifts green to violet
            "s": 70 + 15 * math.sin(2 * math.pi * t / 17),
            "k": 8000,
            "brr": b * (0.3 + shimmer),
        }


class NeonDrift(Scene):
    key = "neon_drift"
    name = "Neon drift"
    glyph = "note"

    def frame(self, t, b):
        return {"h": (t * 12) % 360, "s": 100, "k": 5600, "brr": b}


class Sunrise(Scene):
    key = "sunrise"
    name = "Sunrise"
    glyph = "sun"
    duration = 300.0   # seconds to full daylight, then it holds

    def frame(self, t, b):
        p = _clamp(t / self.duration, 0.0, 1.0)
        return {
            "h": 12 + 28 * p,
            "s": 100 - 90 * p,
            "k": 2000 + 4000 * p,
            "brr": max(1.0, b * (0.05 + 0.95 * p)),
        }


SCENES = [Thunderstorm, Campfire, Ocean, Aurora, NeonDrift, Sunrise]
SCENE_BY_KEY = {cls.key: cls for cls in SCENES}


def soft_id(key):
    """The id the GUI uses for a custom scene, so it can tell it from a built-in id."""
    return "custom:" + key


def key_from_id(scene_id):
    if isinstance(scene_id, str) and scene_id.startswith("custom:"):
        return scene_id[len("custom:"):]
    return None


class Runner:
    """Plays one custom scene on one light in a background thread until stop() is called.

    brightness is a callable that returns the current brightness, so the slider can change
    the scene while it runs. Controller.set_hsi and set_cct are safe to call from any thread.
    """

    def __init__(self, ctl, addr, scene_cls, brightness, cct_range, has_hsi):
        self.ctl = ctl
        self.addr = addr
        self.scene = scene_cls()
        self.brightness = brightness
        self.cct_lo, self.cct_hi = cct_range
        self.has_hsi = has_hsi
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"scene-{addr}", daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def _run(self):
        t0 = time.monotonic()
        while not self._stop.is_set():
            t = time.monotonic() - t0
            try:
                self._send(self.scene.frame(t, float(self.brightness())))
            except Exception:
                log.exception("Custom scene %s stopped on %s", self.scene.key, self.addr)
                return
            self._stop.wait(FRAME_S)

    def _send(self, f):
        brr = int(round(_clamp(f["brr"], 0, 100)))
        if self.has_hsi:
            self.ctl.set_hsi(int(f["h"]) % 360, int(_clamp(f["s"], 0, 100)), brr, self.addr)
        else:
            k = _clamp(f["k"], self.cct_lo, self.cct_hi)
            self.ctl.set_cct(brr, int(round(k / 100.0)) * 100, 0, self.addr)
