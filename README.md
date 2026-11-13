# Lumen Bridge

Desktop control for Neewer Bluetooth LED lights on Windows. Lumen Bridge is a Windows version of [NeewerLite](https://github.com/keefo/NeewerLite), the macOS app. The program has a desktop window and a local HTTP API that other programs can call.

This project is not affiliated with Neewer. See the [Disclaimer](#disclaimer).

## Requirements

- Windows with a Bluetooth Low Energy adapter
- Python 3.10 or newer, from python.org. Tick "Add python.exe to PATH" during installation.
- Neewer lights that are listed in `lights.json`

## Installation

Open a terminal in the project folder and run:

    pip install -r requirements.txt
    python main.py

Turn Bluetooth on and power the lights. The program searches for lights when it starts and again every 20 seconds while it is open. Press Scan to search immediately.

## Using the window

Each light appears as a card on the home screen. Click a card to open its controls.

The page has three tabs:

- CCT sets colour temperature and brightness.
- Colour sets a colour with the hue and saturation sliders.
- Scene starts a built-in or custom effect on lights that support it.

The bottom bar holds the brightness slider and the power buttons. The menu icon on the home screen opens the activity log.

## HTTP API

The API listens on `127.0.0.1:18486` while the program is running. The parameters match the macOS app's URL scheme.

    http://127.0.0.1:18486/ping
    http://127.0.0.1:18486/lights
    http://127.0.0.1:18486/scanLight
    http://127.0.0.1:18486/turnOnLight?light=KeyLight
    http://127.0.0.1:18486/turnOffLight
    http://127.0.0.1:18486/toggleLight
    http://127.0.0.1:18486/setLightCCT?CCT=3200&Brightness=100&GM=0
    http://127.0.0.1:18486/setLightHSI?RGB=ff00ff&Saturation=100&Brightness=100
    http://127.0.0.1:18486/setLightHSI?HUE=200&Saturation=80&Brightness=60
    http://127.0.0.1:18486/setLightScene?Scene=SquadCar&Brightness=100

The `light` parameter matches a light's name or Bluetooth address. Leave it out to address every connected light.

Open the URLs in a browser, or call them from PowerShell with `Invoke-WebRequest`.

## Not yet supported

The macOS app has features that this version does not include:

- Audio-reactive modes
- MCP server for AI assistants
- Gel presets and light-source presets
- NEEWER Home lights (`NH-*`, 0x7A protocol). They are detected and skipped, with a message in the log.
- The macOS app's `neewerlite://` URL scheme. Use the HTTP API instead.
- Stream Deck plugin support (`/sd/*` routes)
- Remote database updates. The bundled `lights.json` is used.

## Project layout

| File | Purpose |
|------|---------|
| `main.py` | Entry point |
| `neewer.py` | Protocol: pattern engine, name parsing, light types, packet builders. Does not use Bluetooth. |
| `ble.py` | Bluetooth connections, writes, keep-alive, scanning (bleak) |
| `server.py` | Local HTTP API on port 18486 |
| `gui.py` | Desktop window (tkinter) |
| `aa.py` | Antialiased drawing and animation helpers (Pillow) |
| `custom_scenes.py` | Software scenes that run on the computer |
| `lights.json` | Light database, copied from the macOS app's `Database/` folder |
| `assets/` | Window icon |
| `tests/` | Unit tests |

## Tests

    python -m unittest discover -s tests -v

The tests check packet bytes against the documented power frames and against hand-calculated CCT, HSI and scene packets. They also check name-to-light-type mapping, check that every command pattern in `lights.json` builds, check the HTTP routes, check that icon edges are antialiased, and check that animations reach their end values. The tests do not use Bluetooth.

## Acknowledgements

The light database and the command formats come from [NeewerLite](https://github.com/keefo/NeewerLite) for macOS.

## Authorship

Some of the code in this project was written by Claude, an AI model. The code has not been tested on every Bluetooth light or Windows configuration, so review it and test it before relying on it.

## Disclaimer

Lumen Bridge is an independent project. It is not affiliated with, endorsed by, or sponsored by Neewer or any of its products or brands. Neewer is named only to identify the lights this program works with. Neewer is a trademark of its respective owner.

Using this program to control lights is entirely at your own risk. The program and its developers are not liable for damage, malfunction, or any other loss arising from its use.
