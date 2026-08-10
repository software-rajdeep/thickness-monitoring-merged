"""
Windows desktop launcher for the thickness-local appliance (no terminal).

This is the entrypoint the WINDOWS .exe runs (see local/thickness-local.spec,
built with console=False). It runs the exact same offline server as the Linux
.deb, but presents like a normal desktop app instead of a console window:

  * all stdout/stderr go to a log file (a windowed .exe has no console, so an
    unguarded print() would otherwise crash the process),
  * the Flask/SocketIO server starts in a background thread,
  * the dashboard opens in the default browser,
  * a system-tray icon offers "Open dashboard", the shareable LAN link, and Quit,
  * the Windows Firewall is opened for the app port so any other device on the
    same router can view the dashboard at http://<this-pc-ip>:<port>.

The Linux .deb build keeps using backend/local_main.py directly (it runs as a
systemd service with a real console) and is completely unaffected by this file.

Run from source to test on Windows:
    python backend/local_gui.py
"""
import io
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser


# --- 1. Make stdout/stderr safe BEFORE importing anything that may print ------
# A PyInstaller windowed (console=False) build has sys.stdout/stderr == None, so
# any print() raises AttributeError. Redirect everything to a log file that sits
# next to the SQLite database.
def _setup_logging():
    if sys.platform.startswith("win"):
        data_dir = os.environ.get("THICKNESS_DATA_DIR") or os.path.join(
            os.environ.get("ProgramData", os.path.expanduser("~")), "ThicknessLocal")
    else:
        data_dir = os.environ.get("THICKNESS_DATA_DIR", "/var/lib/thickness-local")
    try:
        os.makedirs(data_dir, exist_ok=True)
        return open(os.path.join(data_dir, "thickness-local.log"),
                    "a", buffering=1, encoding="utf-8", errors="replace")
    except OSError:
        return io.StringIO()


_LOG = _setup_logging()
sys.stdout = _LOG
sys.stderr = _LOG


# --- 2. Import the appliance --------------------------------------------------
# local_main's module-level code sets LOCAL_MODE, installs the SQLite shim under
# the name psycopg2, and imports merged_server. We reuse all of it verbatim.
import local_main  # noqa: E402

server = local_main.merged_server
PORT = server.SERVER_PORT
LOCAL_URL = f"http://localhost:{PORT}"


def lan_ip():
    """Best-effort primary LAN IPv4. The UDP 'connect' sends no packets; it just
    asks the OS which interface would be used to reach the internet, which is the
    address other devices on the router should use."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        s.close()


LAN_IP = lan_ip()
LAN_URL = f"http://{LAN_IP}:{PORT}"


# --- Single instance guard -----------------------------------------------------
# The CD22 sensors are talked to over a raw request/response TCP protocol with
# no multi-client support: if two processes each open their own connection and
# send commands at overlapping moments, the reply bytes interleave on the wire
# and desync both processes' framing (see CD22Sensor.transact in
# merged_server.py). A double-click, a second install, or Windows reopening the
# app at sign-in must never result in a second process touching the sensors.
_SINGLE_INSTANCE_MUTEX = None  # kept alive for the process lifetime


def _acquire_single_instance():
    if not sys.platform.startswith("win"):
        return True
    import ctypes
    global _SINGLE_INSTANCE_MUTEX
    ERROR_ALREADY_EXISTS = 183
    _SINGLE_INSTANCE_MUTEX = ctypes.windll.kernel32.CreateMutexW(
        None, False, "Global\\ThicknessLocalSingleInstance")
    return ctypes.windll.kernel32.GetLastError() != ERROR_ALREADY_EXISTS


# --- Exit when the dashboard tab is closed --------------------------------------
# Industrial customers get only the .exe, double-click to start, close the tab
# to stop — no separate "quit" step to remember. App.jsx opens one socket.io
# connection for the lifetime of the tab (src/App.jsx:300), so we don't need any
# frontend changes: Flask-SocketIO's own connect/disconnect events tell us
# exactly when the last open dashboard tab (local or LAN-shared) goes away.
# This only runs for the Windows launcher — local_main.py (the .deb, an
# always-on service for many viewers) never imports this file.
_active_clients = set()
_clients_lock = threading.Lock()
_dashboard_ever_opened = threading.Event()
NO_TABS_GRACE = 8  # seconds with zero connected tabs before we treat it as "closed"
                    # (covers a page refresh's brief disconnect/reconnect blip)


@server.socketio.on("connect")
def _on_dashboard_connect():
    from flask import request
    with _clients_lock:
        _active_clients.add(request.sid)
    _dashboard_ever_opened.set()


@server.socketio.on("disconnect")
def _on_dashboard_disconnect():
    from flask import request
    with _clients_lock:
        _active_clients.discard(request.sid)


def _watch_for_all_tabs_closed():
    _dashboard_ever_opened.wait()  # don't arm until the dashboard has opened once
    while True:
        time.sleep(2)
        with _clients_lock:
            empty = len(_active_clients) == 0
        if not empty:
            continue
        time.sleep(NO_TABS_GRACE)
        with _clients_lock:
            still_empty = len(_active_clients) == 0
        if still_empty:
            print("[launcher] dashboard tab closed — shutting down")
            os._exit(0)


FIREWALL_RULE = "Thickness Monitor (LAN)"


def open_firewall():
    """Allow inbound TCP on the app port so other devices on the router can view
    the dashboard. Best-effort silent attempt: succeeds only if the app already
    runs elevated. If it fails (the normal case), the user can grant it once via
    the tray's 'Enable network sharing' item, which raises a single UAC prompt."""
    if not sys.platform.startswith("win"):
        return
    try:
        subprocess.run(["netsh", "advfirewall", "firewall", "delete", "rule",
                        f"name={FIREWALL_RULE}"], capture_output=True)
        r = subprocess.run(["netsh", "advfirewall", "firewall", "add", "rule",
                            f"name={FIREWALL_RULE}", "dir=in", "action=allow",
                            "protocol=TCP", f"localport={PORT}"],
                           capture_output=True, text=True)
        print(f"[launcher] firewall rule rc={r.returncode} {r.stdout.strip()} {r.stderr.strip()}")
    except Exception as e:  # noqa: BLE001
        print(f"[launcher] firewall rule failed (not admin?): {e}")


def enable_network_sharing_elevated():
    """Add the inbound firewall rule with a single UAC elevation prompt, so other
    LAN devices can reach the dashboard — without forcing the whole app to run as
    admin on every launch."""
    if not sys.platform.startswith("win"):
        return
    import ctypes
    # One elevation, two netsh calls (delete stale rule, then add) via cmd /c.
    params = (
        f'/c netsh advfirewall firewall delete rule name="{FIREWALL_RULE}" & '
        f'netsh advfirewall firewall add rule name="{FIREWALL_RULE}" '
        f'dir=in action=allow protocol=TCP localport={PORT}'
    )
    try:
        ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", params, None, 0)
        print("[launcher] requested elevated firewall rule")
    except Exception as e:  # noqa: BLE001
        print(f"[launcher] elevated firewall request failed: {e}")


def _copy_to_clipboard(text):
    try:
        subprocess.run(["clip"], input=text.encode("utf-8"), check=False)
    except Exception as e:  # noqa: BLE001
        print(f"[launcher] clipboard copy failed: {e}")


def _serve():
    try:
        server.main()  # init_* + start_background_tasks + socketio.run (blocking)
    except Exception as e:  # noqa: BLE001
        print(f"[launcher] server crashed: {e}")


def _wait_until_up(timeout=25):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def _tray_image():
    """Build the tray icon in code so no image asset needs bundling."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (15, 23, 42, 255))  # slate-900
    d = ImageDraw.Draw(img)
    d.rectangle([12, 18, 52, 46], outline=(56, 189, 248, 255), width=4)  # caliper body
    d.line([12, 32, 52, 32], fill=(56, 189, 248, 255), width=3)          # measure line
    return img


def _run_tray():
    import pystray
    from pystray import Menu, MenuItem as Item

    def _open(icon, item):
        webbrowser.open(LOCAL_URL)

    def _copy(icon, item):
        _copy_to_clipboard(LAN_URL)

    def _share(icon, item):
        enable_network_sharing_elevated()

    def _quit(icon, item):
        icon.stop()
        os._exit(0)

    menu = Menu(
        Item("Open dashboard", _open, default=True),
        Menu.SEPARATOR,
        Item(f"Share on your network:  {LAN_URL}", None, enabled=False),
        Item("Copy network link", _copy),
        Item("Enable network sharing (allow through firewall)", _share),
        Menu.SEPARATOR,
        Item("Quit", _quit),
    )
    icon = pystray.Icon("thickness-local", _tray_image(),
                        "Thickness Monitor", menu)
    icon.run()


def main():
    if not _acquire_single_instance():
        # Another instance already owns the sensors — just surface its
        # dashboard instead of starting a second sensor-connecting process.
        print("[launcher] already running — opening existing dashboard")
        webbrowser.open(LOCAL_URL)
        return

    print(f"========== thickness-local launcher ==========")
    print(f"  Local : {LOCAL_URL}")
    print(f"  LAN   : {LAN_URL}")
    open_firewall()

    threading.Thread(target=_serve, daemon=True).start()
    threading.Thread(target=_watch_for_all_tabs_closed, daemon=True).start()
    if _wait_until_up():
        print("[launcher] server is up — opening browser")
        webbrowser.open(LOCAL_URL)
    else:
        print("[launcher] server did not come up in time — opening browser anyway")
        webbrowser.open(LOCAL_URL)

    _run_tray()  # blocks on the tray message loop; Quit calls os._exit


if __name__ == "__main__":
    main()
