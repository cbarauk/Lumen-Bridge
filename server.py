"""
Local HTTP API on 127.0.0.1:18486.

Mirrors the macOS neewerlite:// URL commands, so the same parameters work:
    http://127.0.0.1:18486/turnOnLight?light=KeyLight
    http://127.0.0.1:18486/setLightCCT?CCT=3200&Brightness=100
    http://127.0.0.1:18486/setLightHSI?RGB=ff00ff&Saturation=100&Brightness=100
    http://127.0.0.1:18486/setLightScene?Scene=SquadCar&Brightness=100
Plus /lights (JSON status), /ping and /scanLight.
"""

import colorsys
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import neewer as n

# ---------------------------------------------------------------- config ----
HOST = "127.0.0.1"
PORT = 18486

log = logging.getLogger("lumenbridge.server")


def _num(params, key, default):
    try:
        return float(params[key][0]) if key in params else default
    except (ValueError, IndexError):
        raise ValueError(f"{key} must be a number")


def _sat_pct(value):
    """Saturation as 0-100. Values <= 1 are treated as fractions, as in the Swift app."""
    return value * 100 if value <= 1 else value


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def handle(controller, path, params):
    """Route one request. Returns (status, body_dict). Pure, so it can be tested."""
    light = params.get("light", [""])[0] or None

    if path == "/ping":
        return 200, {"status": "pong"}
    if path == "/lights":
        return 200, {"lights": controller.list_lights()}
    if path == "/scanLight":
        controller.scan()
        return 200, {"success": True}

    if path == "/turnOnLight":
        return 200, {"success": True, "count": controller.power(True, light)}
    if path == "/turnOffLight":
        return 200, {"success": True, "count": controller.power(False, light)}
    if path == "/toggleLight":
        return 200, {"success": True, "count": controller.toggle(light)}

    try:
        if path == "/setLightCCT":
            kelvin = _clamp(_num(params, "CCT", 3200), 2500, 8500)
            brr = _clamp(_num(params, "Brightness", 100), 0, 100)
            gm = _clamp(_num(params, "GM", 0), -50, 50)
            count = controller.set_cct(int(brr), int(kelvin), int(gm), light)
            return 200, {"success": True, "count": count}

        if path == "/setLightHSI":
            brr = _clamp(_num(params, "Brightness", 100), 0, 100)
            if "RGB" in params:
                hex_str = params["RGB"][0].lstrip("#")
                if len(hex_str) != 6:
                    return 400, {"error": "RGB must be 6 hex digits"}
                r, g, b = (int(hex_str[i:i + 2], 16) / 255 for i in (0, 2, 4))
                h, s, v = colorsys.rgb_to_hsv(r, g, b)
                hue = h * 360
                sat = _sat_pct(_num(params, "Saturation", s * 100))
            elif "HUE" in params:
                hue = _clamp(_num(params, "HUE", 0), 0, 360)
                sat = _sat_pct(_num(params, "Saturation", 100))
            else:
                return 400, {"error": "setLightHSI needs RGB or HUE"}
            count = controller.set_hsi(int(hue), int(_clamp(sat, 0, 100)), int(brr), light)
            return 200, {"success": True, "count": count}

        if path == "/setLightScene":
            brr = _clamp(_num(params, "Brightness", 100), 0, 100)
            scene_id = None
            if "SceneId" in params:
                scene_id = int(_num(params, "SceneId", 1))
            elif "Scene" in params:
                scene_id = n.SCENE_IDS.get(params["Scene"][0].lower().replace(" ", ""))
            if scene_id is None:
                return 400, {"error": "unknown Scene"}
            count = controller.set_scene(scene_id, int(brr), light)
            return 200, {"success": True, "count": count}
    except ValueError as exc:
        return 400, {"error": str(exc)}

    return 404, {"error": f"unknown command {path}"}


def make_handler(controller):
    class Handler(BaseHTTPRequestHandler):
        def _respond(self):
            url = urlparse(self.path)
            status, body = handle(controller, url.path, parse_qs(url.query))
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = _respond
        do_POST = _respond

        def log_message(self, fmt, *args):  # keep the console quiet
            log.debug("%s - %s", self.address_string(), fmt % args)

    return Handler


def start(controller, port=PORT):
    """Start the server on a daemon thread. Returns the server object."""
    server = ThreadingHTTPServer((HOST, port), make_handler(controller))
    thread = threading.Thread(target=server.serve_forever, name="neewer-http", daemon=True)
    thread.start()
    log.info("HTTP API listening on http://%s:%d", HOST, port)
    return server
