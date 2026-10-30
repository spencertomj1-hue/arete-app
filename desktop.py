"""
Desktop launcher: runs the Streamlit app in its own native window (no browser).

Run with:   .venv/bin/python desktop.py          (macOS / Linux)
            .venv/Scripts/pythonw.exe desktop.py  (Windows)
Or open "Arete.app" (macOS) or the Arete shortcut (Windows), made by setup.sh / setup.ps1.
"""

import atexit
import base64
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import webview

APP_DIR = Path(__file__).parent
ICON = APP_DIR / "assets" / "icon.png"   # built by scripts/make_icon.py
SPLASH_LOGO = APP_DIR / "assets" / "splash.png"   # also built by scripts/make_icon.py
SPLASH_SECONDS = 2.0                     # minimum time the loading screen shows
READY_TIMEOUT = 45                       # give up waiting for the page after this (it shows anyway)


def splash_html():
    """splash.html with the logo embedded, so it shows before the server is up."""
    logo = base64.b64encode(SPLASH_LOGO.read_bytes()).decode()
    return (APP_DIR / "assets" / "splash.html").read_text(encoding="utf-8").replace("{{LOGO}}", f"data:image/png;base64,{logo}")


def set_dock_icon():
    """The running process is Python, so tell macOS to show Arete's name and icon instead."""
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle
        info = NSBundle.mainBundle().infoDictionary()
        if info is not None:
            info["CFBundleName"] = "Arete"      # menu bar / Dock name
        image = NSImage.alloc().initWithContentsOfFile_(str(ICON))
        if image:
            NSApplication.sharedApplication().setApplicationIconImage_(image)
    except Exception:
        pass  # cosmetic only


def free_port():
    """Ask the OS for an unused port so we never clash with another app."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_server(port, timeout=30):
    """Block until Streamlit is accepting connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("Streamlit didn't start in time")


def main():
    port = free_port()

    # Start Streamlit in the background without opening a browser tab.
    server = subprocess.Popen(
        [
            sys.executable, "-m", "streamlit", "run", str(APP_DIR / "app.py"),
            "--server.headless", "true",
            "--server.port", str(port),
            "--server.address", "127.0.0.1",
            "--browser.gatherUsageStats", "false",
        ],
        cwd=APP_DIR,
    )

    def stop_server():
        if server.poll() is None:
            server.terminate()

    # Make sure the server stops however the app exits (closing the window,
    # Cmd+Q, or the process being killed).
    atexit.register(stop_server)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    def show_app(splash):
        """
        Runs once the splash is up. The app loads in a second, hidden window, so
        none of its loading shows: once the page has fully drawn (its
        #arete-ready marker) and the splash has had SPLASH_SECONDS, the app
        window appears and the splash closes.
        """
        shown_at = time.time()
        try:
            wait_for_server(port)
        except RuntimeError:
            return show_error(splash)

        app = webview.create_window(
            "Arete", f"http://127.0.0.1:{port}", width=1200, height=850,
            hidden=True, background_color="#FFFFFF",
        )
        app.events.closed += stop_server
        state["app"] = app

        deadline = time.time() + READY_TIMEOUT
        while time.time() < deadline:
            try:
                if app.evaluate_js("!!document.getElementById('arete-ready')"):
                    break
            except Exception:
                pass            # page not loaded yet
            time.sleep(0.15)
        time.sleep(0.3)         # let the last components finish painting

        time.sleep(max(0.0, SPLASH_SECONDS - (time.time() - shown_at)))
        splash.evaluate_js("document.body.classList.add('out')")    # fade the splash
        time.sleep(0.35)
        app.show()
        app.maximize()          # the timetable's week view wants the room
        state["swapped"] = True
        splash.destroy()

    def show_error(splash):
        splash.evaluate_js(
            "document.getElementById('motto').outerHTML ="
            " '<p class=\"error\">Arete couldn\\'t start. Close this window and try again.</p>'"
        )

    state = {"app": None, "swapped": False}

    def splash_closed():
        # Closing the splash before the app appears means quitting.
        if not state["swapped"]:
            stop_server()
            if state["app"]:
                state["app"].destroy()

    try:
        splash = webview.create_window(
            "Arete", html=splash_html(), width=1200, height=850,
            maximized=True, background_color="#FFFFFF",
        )
        splash.events.closed += splash_closed
        set_dock_icon()
        webview.start(show_app, splash)  # blocks until every window is closed
    finally:
        stop_server()


if __name__ == "__main__":
    main()
