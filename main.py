"""
Lumen Bridge: entry point.

Run:  python main.py
Needs: Python 3.10+, `pip install bleak`. Bluetooth must be on.
"""

import logging
import tkinter as tk

import ble
import gui
import server

# ---------------------------------------------------------------- config ----
LOG_LEVEL = logging.INFO


def main():
    logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("bleak").setLevel(logging.WARNING)

    controller = ble.Controller()
    controller.start()
    try:
        server.start(controller)
    except OSError as exc:
        logging.getLogger("lumenbridge").warning(
            "HTTP API not started (port %d busy?): %s", server.PORT, exc)

    root = tk.Tk()
    gui.App(root, controller)
    root.mainloop()


if __name__ == "__main__":
    main()
