"""
Ubuntu/Pi client for the Merged Thickness Monitor app.
Reads CD22 sensors A, B (and C if present) over TCP and POSTs raw distance
readings to the cloud backend's /ingest/readings endpoint.

Configuration via environment variables:
  SERVER_URL   — cloud backend URL  (default: http://194.164.148.145:8082)
  API_KEY      — must match INGEST_API_KEY on the server
  POST_RATE_HZ — readings per second (default: 5)
"""

import socket
import threading
import time
import json
import os
import datetime
import requests

SERVER_URL   = os.environ.get("SERVER_URL",   "http://194.164.148.145:8082")
API_KEY      = os.environ.get("API_KEY",      "merged-secret-2026")
POST_RATE_HZ = float(os.environ.get("POST_RATE_HZ", "5"))

BASE_DIR            = os.path.dirname(os.path.abspath(__file__))
NETWORK_CONFIG_FILE = os.path.join(BASE_DIR, "sensor_network.json")
SENSOR_TIMEOUT      = 2.0
INGEST_ENDPOINT     = f"{SERVER_URL}/ingest/readings"
POLL_ENDPOINT       = f"{SERVER_URL}/config/poll"
RESULT_ENDPOINT     = f"{SERVER_URL}/config/result"
POST_INTERVAL       = 1.0 / POST_RATE_HZ
CONFIG_POLL_INTERVAL = 3.0

STX       = 0x02
ETX       = 0x03
CMD_READ  = 0x52
CMD_WRITE = 0x57

DEFAULT_SENSOR_CONFIGS = {
    "A": {"ip": "192.168.1.7", "port": 8234, "name": "Sensor A"},
    "B": {"ip": "192.168.1.8", "port": 8234, "name": "Sensor B"},
    "C": {"ip": "192.168.1.9", "port": 8234, "name": "Sensor C"},
}

CONNECT_TIMEOUT         = 1.0
RECONNECT_BACKOFF       = 30.0
RECONNECT_BACKOFF_RETRY = 5.0


class CD22Sensor:
    def __init__(self, ip, port, name):
        self.ip        = ip
        self.port      = port
        self.name      = name
        self.sock      = None
        self.lock      = threading.Lock()
        self.connected = False
        self._last_connect_attempt = 0.0
        self._ever_connected       = False

    def connect(self):
        if self.connected:
            return True
        now = time.monotonic()
        backoff = RECONNECT_BACKOFF_RETRY if self._ever_connected else RECONNECT_BACKOFF
        if now - self._last_connect_attempt < backoff:
            return False
        self._last_connect_attempt = now
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(CONNECT_TIMEOUT)
            s.connect((self.ip, self.port))
            with self.lock:
                self.sock            = s
                self.connected       = True
                self._ever_connected = True
            print(f"[{self.name}] Connected.", flush=True)
            return True
        except Exception:
            with self.lock:
                self.connected = False
            return False

    def disconnect(self):
        with self.lock:
            if self.sock:
                try:
                    self.sock.close()
                except Exception:
                    pass
                self.sock      = None
                self.connected = False

    def get_latest(self):
        cmd_bytes = bytes([STX, 0x43, 0xB0, 0x01, ETX, (0x43 ^ 0xB0 ^ 0x01)])
        if not self.connected:
            if not self.connect():
                return None
        with self.lock:
            try:
                self.sock.settimeout(0.5)
                self.sock.sendall(cmd_bytes)
                resp = self.sock.recv(6)
                self.sock.settimeout(SENSOR_TIMEOUT)
                if resp and len(resp) == 6 and resp[1] == 0x06:
                    raw = (resp[2] << 8) | resp[3]
                    if raw > 32767:
                        raw -= 65536
                    return raw * 0.01
            except Exception:
                if self.sock:
                    try:
                        self.sock.close()
                    except Exception:
                        pass
                    self.sock = None
                self.connected = False
                self._last_connect_attempt = 0.0
                return None
        return None

    def generic_write(self, addr_h, addr_l, val_h, val_l):
        bcc_r = CMD_READ  ^ addr_h ^ addr_l
        bcc_w = CMD_WRITE ^ val_h  ^ val_l
        with self.lock:
            if not self.connect():
                return False
            try:
                self.sock.sendall(bytes([STX, CMD_READ,  addr_h, addr_l, ETX, bcc_r]))
                time.sleep(0.05)
                self.sock.recv(6)
                self.sock.sendall(bytes([STX, CMD_WRITE, val_h,  val_l,  ETX, bcc_w]))
                resp = self.sock.recv(6)
                return bool(resp and resp[1] == 0x06)
            except Exception:
                self.connected = False
                return False


def load_network_config():
    if not os.path.exists(NETWORK_CONFIG_FILE):
        return DEFAULT_SENSOR_CONFIGS.copy()
    try:
        with open(NETWORK_CONFIG_FILE) as f:
            data = json.load(f)
        configs = {}
        for sid, defaults in DEFAULT_SENSOR_CONFIGS.items():
            entry = data.get(sid, {})
            configs[sid] = {
                "ip":   str(entry.get("ip",   defaults["ip"])),
                "port": int(entry.get("port", defaults["port"])),
                "name": str(entry.get("name", defaults["name"])),
            }
        return configs
    except Exception as e:
        print(f"Failed to load sensor_network.json: {e}. Using defaults.")
        return DEFAULT_SENSOR_CONFIGS.copy()


def main():
    print("=" * 50)
    print("  CD22 Pi Client — Merged Thickness Monitor")
    print(f"  Target: {INGEST_ENDPOINT}")
    print(f"  Rate:   {POST_RATE_HZ} Hz")
    print("=" * 50)

    configs = load_network_config()
    sensors = {}
    for sid, cfg in configs.items():
        sensors[sid] = CD22Sensor(cfg["ip"], cfg["port"], cfg["name"])
        print(f"  Sensor {sid}: {cfg['ip']}:{cfg['port']}")

    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-Api-Key"] = API_KEY

    session = requests.Session()
    session.headers.update(headers)

    print("\nTesting sensor connections...", flush=True)
    for sid, sensor in sensors.items():
        print(f"  Trying {sensor.name} ({sensor.ip}:{sensor.port})... ", end="", flush=True)
        print("CONNECTED" if sensor.connect() else "UNREACHABLE", flush=True)

    print("\nStarting reads... (Ctrl+C to stop)\n", flush=True)

    last_config_poll = time.monotonic() - CONFIG_POLL_INTERVAL

    while True:
        loop_start = time.monotonic()
        payload    = {"timestamp": datetime.datetime.now().isoformat()}
        any_reading = False

        for sid, sensor in sensors.items():
            val = sensor.get_latest()
            if val is not None:
                payload[f"sensor_{sid}"] = round(val, 3)
                any_reading = True
            else:
                payload[f"sensor_{sid}"] = None

        if any_reading:
            try:
                resp = session.post(INGEST_ENDPOINT, json=payload, timeout=5)
                print(f"[{payload['timestamp']}] Posted {payload} -> HTTP {resp.status_code}", flush=True)
            except requests.exceptions.ConnectionError:
                print(f"[{payload['timestamp']}] Connection error — server unreachable. Retrying...", flush=True)
            except requests.exceptions.Timeout:
                print(f"[{payload['timestamp']}] POST timed out.", flush=True)
            except Exception as e:
                print(f"[{payload['timestamp']}] POST failed: {e}", flush=True)
        else:
            print(f"[{payload['timestamp']}] All sensors offline — skipping POST.", flush=True)

        now = time.monotonic()
        if now - last_config_poll >= CONFIG_POLL_INTERVAL:
            last_config_poll = now
            try:
                poll_resp = session.get(POLL_ENDPOINT, timeout=5)
                cmds = poll_resp.json().get("commands", [])
                for cmd in cmds:
                    sid = str(cmd.get("sensor", "")).upper()
                    if sid not in sensors:
                        print(f"[CONFIG] Unknown sensor '{sid}' — skipping.", flush=True)
                        continue
                    sensor = sensors[sid]
                    try:
                        addr_h = int(str(cmd["addr_h"]), 16)
                        addr_l = int(str(cmd["addr_l"]), 16)
                        val_h  = int(str(cmd.get("val_h", "0x00")), 16)
                        val_l  = int(str(cmd["val_l"]), 16)
                        ok = sensor.generic_write(addr_h, addr_l, val_h, val_l)
                        print(f"[CONFIG] Sensor {sid} write {'OK' if ok else 'FAILED'}", flush=True)
                        session.post(RESULT_ENDPOINT, json={
                            "id": cmd.get("id"), "sensor": sid, "success": ok,
                        }, timeout=5)
                    except Exception as e:
                        print(f"[CONFIG] Error for sensor {sid}: {e}", flush=True)
                        session.post(RESULT_ENDPOINT, json={
                            "id": cmd.get("id"), "sensor": sid, "success": False, "error": str(e),
                        }, timeout=5)
            except Exception as e:
                print(f"[CONFIG] Poll error: {e}", flush=True)

        elapsed = time.monotonic() - loop_start
        time.sleep(max(0.0, POST_INTERVAL - elapsed))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
