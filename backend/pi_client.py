"""
Ubuntu/Pi client for the Merged Thickness Monitor app.
Reads CD22 sensors A, B (and C if present) over TCP and POSTs raw distance
readings to the cloud backend's /ingest/readings endpoint.

Uses stateless TCP (connect → read → close) so it works reliably over both
Ethernet and WiFi without persistent-connection drop issues.

Configuration via environment variables:
  SERVER_URL   — cloud backend URL  (default: http://194.164.148.145:8082)
  API_KEY      — must match INGEST_API_KEY on the server
  POST_RATE_HZ — readings per second (default: 5)
"""

import socket
import time
import json
import os
import sys
import datetime
import requests

SERVER_URL   = os.environ.get("SERVER_URL",   "http://194.164.148.145:8082")
API_KEY      = os.environ.get("API_KEY",      "merged-secret-2026")
POST_RATE_HZ = float(os.environ.get("POST_RATE_HZ", "5"))

BASE_DIR            = os.path.dirname(os.path.abspath(__file__))
NETWORK_CONFIG_FILE = os.path.join(BASE_DIR, "sensor_network.json")
INGEST_ENDPOINT     = f"{SERVER_URL}/ingest/readings"
POLL_ENDPOINT       = f"{SERVER_URL}/config/poll"
RESULT_ENDPOINT     = f"{SERVER_URL}/config/result"
POST_INTERVAL       = 1.0 / POST_RATE_HZ
CONFIG_POLL_INTERVAL = 3.0

STX      = 0x02
ETX      = 0x03
CMD_READ  = 0x52
CMD_WRITE = 0x57
WIFI_CMD  = bytes([0x02, 0x43, 0xB0, 0x01, 0x03, 0x43 ^ 0xB0 ^ 0x01])

SENSOR_TIMEOUT = 0.5   # per-read TCP timeout
WRITE_TIMEOUT  = 1.0   # per-write TCP timeout


def query_sensor(ip, port):
    """Open TCP, send measurement command, read response, close. Works over WiFi and Ethernet."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(SENSOR_TIMEOUT)
            s.connect((ip, port))
            s.sendall(WIFI_CMD)
            resp = s.recv(16)
            if len(resp) < 4:
                return None
            raw = (resp[2] << 8) | resp[3]
            if raw > 32767:
                raw -= 65536
            return round(raw * 0.01, 3)
    except Exception:
        return None


def sensor_write(ip, port, addr_h, addr_l, val_h, val_l):
    """Stateless register write via TCP."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(WRITE_TIMEOUT)
            s.connect((ip, port))
            # Flush any stale data
            s.settimeout(0.05)
            try:
                while s.recv(1024): pass
            except Exception:
                pass
            s.settimeout(WRITE_TIMEOUT)
            # Read first (protocol requirement)
            bcc_r = CMD_READ ^ addr_h ^ addr_l
            s.sendall(bytes([STX, CMD_READ, addr_h, addr_l, ETX, bcc_r]))
            time.sleep(0.05)
            try: s.recv(6)
            except Exception: pass
            # Write
            bcc_w = CMD_WRITE ^ val_h ^ val_l
            s.sendall(bytes([STX, CMD_WRITE, val_h, val_l, ETX, bcc_w]))
            resp = s.recv(6)
            return bool(resp and resp[1] == 0x06)
    except Exception:
        return False


def load_network_config():
    if not os.path.exists(NETWORK_CONFIG_FILE):
        print(f"ERROR: {NETWORK_CONFIG_FILE} not found!")
        sys.exit(1)
    try:
        with open(NETWORK_CONFIG_FILE) as f:
            data = json.load(f)
        if not data:
            print(f"ERROR: {NETWORK_CONFIG_FILE} is empty!")
            sys.exit(1)
        configs = {}
        for sid, entry in data.items():
            sid_upper = sid.upper()
            ip = entry.get("ip")
            if not ip:
                print(f"ERROR: Sensor {sid_upper} has no 'ip' field!")
                sys.exit(1)
            configs[sid_upper] = {
                "ip":   str(ip),
                "port": int(entry.get("port", 8234)),
                "name": str(entry.get("name", f"Sensor {sid_upper}")),
            }
        return configs
    except Exception as e:
        print(f"Failed to load {NETWORK_CONFIG_FILE}: {e}")
        sys.exit(1)


def main():
    print("=" * 50)
    print("  CD22 Pi Client — Merged Thickness Monitor")
    print(f"  Target: {INGEST_ENDPOINT}")
    print(f"  Rate:   {POST_RATE_HZ} Hz")
    print("=" * 50)

    configs = load_network_config()
    for sid, cfg in configs.items():
        print(f"  Sensor {sid}: {cfg['ip']}:{cfg['port']}")

    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-Api-Key"] = API_KEY

    session = requests.Session()
    session.headers.update(headers)

    print("\nTesting sensor connections...")
    for sid, cfg in configs.items():
        val = query_sensor(cfg["ip"], cfg["port"])
        print(f"  Sensor {sid} ({cfg['ip']}:{cfg['port']}): {'ONLINE' if val is not None else 'OFFLINE'}")

    print("\nStarting reads... (Ctrl+C to stop)\n", flush=True)

    last_config_poll = time.monotonic() - CONFIG_POLL_INTERVAL

    while True:
        loop_start  = time.monotonic()
        timestamp   = datetime.datetime.now().isoformat()
        payload     = {"timestamp": timestamp}
        any_reading = False

        for sid, cfg in configs.items():
            val = query_sensor(cfg["ip"], cfg["port"])
            payload[f"sensor_{sid}"] = val
            if val is not None:
                any_reading = True

        if any_reading:
            try:
                resp = session.post(INGEST_ENDPOINT, json=payload, timeout=5)
                print(f"[{timestamp}] Posted {payload} -> HTTP {resp.status_code}", flush=True)
            except requests.exceptions.ConnectionError:
                print(f"[{timestamp}] KVM unreachable — will retry.", flush=True)
            except requests.exceptions.Timeout:
                print(f"[{timestamp}] POST timed out.", flush=True)
            except Exception as e:
                print(f"[{timestamp}] POST failed: {e}", flush=True)
        else:
            print(f"[{timestamp}] All sensors offline — skipping POST.", flush=True)

        # Poll KVM for sensor write commands (remote hardware config)
        now = time.monotonic()
        if now - last_config_poll >= CONFIG_POLL_INTERVAL:
            last_config_poll = now
            try:
                poll_resp = session.get(POLL_ENDPOINT, timeout=5)
                cmds = poll_resp.json().get("commands", [])
                for cmd in cmds:
                    sid = str(cmd.get("sensor", "")).upper()
                    if sid not in configs:
                        print(f"[CONFIG] Unknown sensor '{sid}' — skipping.", flush=True)
                        continue
                    cfg = configs[sid]
                    try:
                        addr_h = int(str(cmd["addr_h"]), 16)
                        addr_l = int(str(cmd["addr_l"]), 16)
                        val_h  = int(str(cmd.get("val_h", "0x00")), 16)
                        val_l  = int(str(cmd["val_l"]), 16)
                        ok = sensor_write(cfg["ip"], cfg["port"], addr_h, addr_l, val_h, val_l)
                        print(f"[CONFIG] Sensor {sid} write {'OK' if ok else 'FAILED'}", flush=True)
                        session.post(RESULT_ENDPOINT, json={
                            "id": cmd.get("id"), "sensor": sid, "success": ok,
                        }, timeout=5)
                    except Exception as e:
                        print(f"[CONFIG] Error for sensor {sid}: {e}", flush=True)
                        session.post(RESULT_ENDPOINT, json={
                            "id": cmd.get("id"), "sensor": sid, "success": False, "error": str(e),
                        }, timeout=5)
            except Exception:
                pass  # KVM poll failure is non-fatal; just skip this cycle

        elapsed = time.monotonic() - loop_start
        time.sleep(max(0.0, POST_INTERVAL - elapsed))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
