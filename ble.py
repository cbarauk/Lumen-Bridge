"""
Bluetooth connection manager for Neewer lights, built on bleak.

All BLE I/O runs on one asyncio loop in a background thread. The public
methods on Controller are thread-safe and never block the GUI or HTTP server.
Port of the connection/keep-alive/write logic in NeewerLight.swift and AppDelegate.swift.
"""

import asyncio
import collections
import logging
import threading

from bleak import BleakClient, BleakScanner

import neewer as n

# ---------------------------------------------------------------- config ----
SCAN_SECONDS = 5.0        # how long each BLE scan runs
RESCAN_EVERY = 20.0       # seconds between automatic background scans while the program is open
KEEPALIVE_EVERY = 10.0    # seconds between keep-alive reads per light
FAILS_BEFORE_DROP = 2     # failed keep-alives before a light is marked offline
WRITE_GAP = 0.015         # seconds between BLE writes (avoids radio jams)
CONNECT_TIMEOUT = 10.0    # seconds to wait for a connection

log = logging.getLogger("lumenbridge.ble")


def name_is_neewer(name):
    """Port of NeewerLightConstant.isValidPeripheralName."""
    s = name.lower()
    return ("nwr" in s or "neewer" in s or "sl" in s or s.startswith("nw-")
            or s.startswith("nh-") or s.startswith("neewer-") or "nee" in s)


class Light:
    """One connected (or known) Neewer light."""

    def __init__(self, device, raw_name, db):
        self.device = device
        self.address = device.address
        self.raw_name = raw_name
        self.mac = device.address
        # The address without colons stands in for the iOS identifier suffix (last 6 chars).
        self.nick, self.project = n.get_light_names(raw_name, self.address.replace(":", ""))
        self.type = n.get_light_type(self.nick, raw_name, self.project)
        self.db = db
        self.client = None
        self.ctl = None
        self.gatt = None
        self.connected = False
        self.fails = 0
        self._loop = None
        self._wake = None
        self._writer = None
        self._urgent = collections.deque()   # power / scene: every packet is sent
        self._latest = None                  # brightness / CCT / HSI: only the newest matters
        # Last values we sent, for the UI.
        self.is_on = None
        self.brr = 100
        self.cct = 5600
        self.hue = 0
        self.sat = 100
        self.mode = None       # "cct", "hsi" or "scene": what the light is currently showing
        self.scene_id = None

    # capabilities come from the database entry for this light type
    def _entry(self):
        return self.db["lights"].get(self.type, {})

    @property
    def can_power(self):
        return "power" in self._entry().get("commandPatterns", {})

    @property
    def can_cct(self):
        return "cct" in self._entry().get("commandPatterns", {})

    @property
    def can_hsi(self):
        return "hsi" in self._entry().get("commandPatterns", {})

    @property
    def scenes(self):
        return n.scene_list(self.db, self.type)

    @property
    def cct_range(self):
        return n.cct_range(self.type, self.project)

    def matches(self, name):
        name = name.lower()
        return name in (self.nick.lower(), self.project.lower(), self.address.lower())

    # ---------------------------------------------------- connection (loop thread)
    async def connect(self, loop):
        self._loop = loop
        client = BleakClient(self.device, disconnected_callback=self._on_disconnect,
                             timeout=CONNECT_TIMEOUT)
        try:
            await client.connect()
            self.ctl = client.services.get_characteristic(n.CTL_UUID)
            self.gatt = client.services.get_characteristic(n.GATT_UUID)
            if self.ctl is None:
                raise RuntimeError("Neewer control characteristic not found")
        except Exception as exc:  # connection problems are normal; log and carry on
            log.warning("Could not connect to %s (%s): %s", self.nick, self.address, exc)
            try:
                await client.disconnect()
            except Exception:
                pass
            return False

        if self._writer and not self._writer.done():
            self._writer.cancel()  # a previous session's writer must not outlive it
        self.client = client
        self.connected = True
        self.fails = 0
        self._wake = asyncio.Event()
        self._writer = loop.create_task(self._write_loop())
        loop.create_task(self._keepalive())
        log.info("Connected to %s (type %s)", self.nick, self.type)
        return True

    def _on_disconnect(self, _client):
        if self.connected:
            log.info("%s disconnected", self.nick)
        self.connected = False

    async def _mark_dead(self):
        self.connected = False
        if self._writer and not self._writer.done():
            self._writer.cancel()
        try:
            if self.client is not None:
                await self.client.disconnect()
        except Exception:
            pass

    async def _keepalive(self):
        while self.connected:
            await asyncio.sleep(KEEPALIVE_EVERY)
            if not self.connected:
                break
            try:
                await asyncio.wait_for(self.client.read_gatt_char(self.gatt), timeout=5)
                self.fails = 0
            except Exception:
                self.fails += 1
                if self.fails >= FAILS_BEFORE_DROP:
                    log.warning("%s stopped responding; marking offline", self.nick)
                    await self._mark_dead()
                    return

    # ------------------------------------------------------ writes (loop thread)
    async def _write_loop(self):
        while self.connected:
            await self._wake.wait()
            self._wake.clear()
            while self.connected and (self._urgent or self._latest is not None):
                if self._urgent:
                    data = self._urgent.popleft()
                else:
                    data, self._latest = self._latest, None
                try:
                    await self.client.write_gatt_char(self.ctl, data, response=False)
                except Exception as exc:
                    log.warning("Write to %s failed: %s", self.nick, exc)
                    await self._mark_dead()
                    return
                await asyncio.sleep(WRITE_GAP)

    def _enqueue(self, data, urgent):
        if not self.connected or self._wake is None:
            return
        if urgent:
            self._urgent.append(data)
        else:
            self._latest = data
        self._wake.set()

    # ----------------------------------------------- thread-safe send (any thread)
    def send(self, data, urgent=False):
        if not self.connected or self._loop is None or data is None:
            return False
        self._loop.call_soon_threadsafe(self._enqueue, data, urgent)
        return True


class Controller:
    """Owns the BLE loop and the list of lights. Safe to call from any thread."""

    def __init__(self, db_path=n.DB_PATH):
        self.db = n.load_database(db_path)
        self.lights = {}  # address -> Light
        self._lock = threading.Lock()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="neewer-ble", daemon=True)
        self._scanning = False

    def start(self):
        self._thread.start()
        asyncio.run_coroutine_threadsafe(self._housekeeping(), self._loop)

    def scan(self):
        """Ask for a scan now. Returns immediately."""
        asyncio.run_coroutine_threadsafe(self._scan_and_connect(), self._loop)

    # ----------------------------------------------------- scanning (loop thread)
    async def _housekeeping(self):
        # Scan on launch, then every RESCAN_EVERY seconds while the program is open, so lights
        # switched on later are picked up without pressing Scan. Lights already connected are skipped.
        while True:
            await self._scan_and_connect()
            await asyncio.sleep(RESCAN_EVERY)

    async def _scan_and_connect(self):
        if self._scanning:
            return
        self._scanning = True
        try:
            devices = await BleakScanner.discover(timeout=SCAN_SECONDS)
            for dev in devices:
                name = dev.name or ""
                if not name or not name_is_neewer(name):
                    continue
                with self._lock:
                    light = self.lights.get(dev.address)
                if light is not None and light.connected:
                    continue
                if light is None:
                    if _is_neewer_home(name):
                        log.info("Ignoring %s: NEEWER Home (0x7A) lights are not supported yet", name)
                        continue
                    light = Light(dev, name, self.db)
                    if light.type == 0:
                        log.info("Ignoring %s: unknown light type", name)
                        continue
                else:
                    light.device = dev
                ok = await light.connect(self._loop)
                if ok:
                    with self._lock:
                        self.lights[dev.address] = light
        except Exception as exc:
            log.error("Scan failed: %s", exc)
        finally:
            self._scanning = False

    # ------------------------------------------------------ public API (any thread)
    def _targets(self, light_name=None):
        with self._lock:
            lights = list(self.lights.values())
        out = [l for l in lights if l.connected]
        if light_name:
            out = [l for l in out if l.matches(light_name)]
        return out

    def list_lights(self):
        with self._lock:
            lights = list(self.lights.values())
        return [{
            "name": l.nick, "project": l.project, "address": l.address, "type": l.type,
            "connected": l.connected, "on": l.is_on, "brightness": l.brr,
            "cct": l.cct, "hue": l.hue, "saturation": l.sat, "cctRange": l.cct_range,
            "canPower": l.can_power, "canCCT": l.can_cct, "canHSI": l.can_hsi,
            "scenes": [{"id": i, "name": s} for i, s in l.scenes],
        } for l in lights]

    def power(self, on, light_name=None):
        count = 0
        for l in self._targets(light_name):
            pkt = n.power_command(self.db, l.type, l.mac, on)
            if pkt and l.send(pkt, urgent=True):
                l.is_on = on
                count += 1
        return count

    def toggle(self, light_name=None):
        targets = self._targets(light_name)
        if not targets:
            return 0
        turn_on = not all(bool(l.is_on) for l in targets)
        return self.power(turn_on, light_name)

    def set_cct(self, brightness, kelvin, gm=0, light_name=None):
        count = 0
        for l in self._targets(light_name):
            if not l.can_cct:
                continue
            pkt = n.cct_command(self.db, l.type, l.mac, brightness, kelvin, gm)
            if pkt and l.send(pkt, urgent=False):
                l.brr, l.cct, l.is_on, l.mode = brightness, kelvin, True, "cct"
                count += 1
        return count

    def set_hsi(self, hue, sat, brightness, light_name=None):
        count = 0
        for l in self._targets(light_name):
            if not l.can_hsi:
                continue
            pkt = n.hsi_command(self.db, l.type, l.mac, hue, sat, brightness)
            if pkt and l.send(pkt, urgent=False):
                l.hue, l.sat, l.brr, l.is_on, l.mode = hue, sat, brightness, True, "hsi"
                count += 1
        return count

    def set_scene(self, scene_id, brightness, light_name=None):
        count = 0
        for l in self._targets(light_name):
            pkt = n.scene_command(self.db, l.type, l.mac, scene_id, brightness)
            if pkt and l.send(pkt, urgent=True):
                l.brr, l.is_on, l.mode, l.scene_id = brightness, True, "scene", scene_id
                count += 1
        return count

    def set_brightness(self, brightness, light_name=None):
        """Change brightness without changing the colour, colour temperature or scene."""
        count = 0
        for l in self._targets(light_name):
            pkt = None
            if l.mode == "scene" and l.scene_id is not None:
                pkt = n.scene_command(self.db, l.type, l.mac, l.scene_id, brightness)
            elif l.mode == "hsi" and l.can_hsi:
                pkt = n.hsi_command(self.db, l.type, l.mac, l.hue, l.sat, brightness)
            elif l.can_cct:
                pkt = n.cct_command(self.db, l.type, l.mac, brightness, l.cct)
            elif l.can_hsi:
                pkt = n.hsi_command(self.db, l.type, l.mac, l.hue, l.sat, brightness)
            if pkt and l.send(pkt, urgent=False):
                l.brr = brightness
                count += 1
        return count


def _is_neewer_home(name):
    return name.upper().startswith("NH-")
