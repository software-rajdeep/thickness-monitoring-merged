"""
Thickness Agent — the installable customer app.

Runs on a Raspberry Pi, Linux PC, Windows PC, or Mac near the CD22 sensors.
It does three things:

  1. First-run SETUP WIZARD at http://localhost:7000
       - customer enters their Activation Code (device_id + device_key)
       - the agent validates it with the cloud server, which returns the
         customer's name + sensor mode (the customer never types these)
       - customer enters their sensor IPs and tests the connection
       - config is saved and monitoring starts

  2. SENSOR READING — talks to each CD22 over TCP (same protocol as pi_client)

  3. UPLOAD — POSTs readings at 5 Hz to the cloud, authenticated with the
     per-device headers  X-Device-Id  +  X-Device-Key  (unique per customer;
     useless if shared without the matching device_id, and revocable server-side).

Only dependency: `requests`.  Standard-library http.server powers the wizard so
the packaged binary stays small.

Config file location (override with env THICKNESS_AGENT_CONFIG):
  Windows : %ProgramData%\\ThicknessAgent\\config.json
  Linux   : /etc/thickness-agent/config.json  (falls back to ~/.thickness-agent)
  macOS   : ~/Library/Application Support/ThicknessAgent/config.json
"""

import os
import sys
import json
import time
import socket
import threading
import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import math
import requests

# ----------------------------------------------------------------------------
# Defaults / constants
# ----------------------------------------------------------------------------
DEFAULT_SERVER_URL = os.environ.get("SERVER_URL", "https://194-164-148-145.sslip.io")
WIZARD_PORT        = int(os.environ.get("WIZARD_PORT", "7000"))
# Bind address for the setup wizard. 127.0.0.1 = local only (safest). Set to
# 0.0.0.0 to reach the wizard from another machine on a headless Raspberry Pi.
WIZARD_HOST        = os.environ.get("WIZARD_HOST", "127.0.0.1")
# SIMULATE=1 generates synthetic readings instead of talking to real sensors —
# lets you confirm the Pi -> server -> dashboard pipeline with no sensors wired.
SIMULATE           = os.environ.get("SIMULATE", "").lower() in ("1", "true", "yes")
POST_RATE_HZ       = float(os.environ.get("POST_RATE_HZ", "5"))
POST_INTERVAL      = 1.0 / POST_RATE_HZ

SENSOR_PORT_DEFAULT = 8234
CONNECT_TIMEOUT     = 1.0
SENSOR_TIMEOUT      = 2.0
RECONNECT_BACKOFF   = 30.0
RECONNECT_RETRY     = 5.0

STX = 0x02
ETX = 0x03


# ----------------------------------------------------------------------------
# Config storage (cross-platform)
# ----------------------------------------------------------------------------
def config_path():
    override = os.environ.get("THICKNESS_AGENT_CONFIG")
    if override:
        return override
    if sys.platform.startswith("win"):
        base = os.environ.get("ProgramData", os.path.expanduser("~"))
        return os.path.join(base, "ThicknessAgent", "config.json")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/ThicknessAgent/config.json")
    # Linux / Raspberry Pi: prefer /etc, fall back to home if not writable
    etc = "/etc/thickness-agent/config.json"
    try:
        os.makedirs(os.path.dirname(etc), exist_ok=True)
        return etc
    except PermissionError:
        return os.path.expanduser("~/.thickness-agent/config.json")


def load_config():
    p = config_path()
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            return None
    return None


def save_config(cfg):
    p = config_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, p)   # atomic — never leaves a half-written config
    return p


# ----------------------------------------------------------------------------
# CD22 sensor (TCP binary protocol — identical to pi_client.py)
# ----------------------------------------------------------------------------
class CD22Sensor:
    def __init__(self, ip, port, name):
        self.ip = ip
        self.port = int(port)
        self.name = name
        self.sock = None
        self.lock = threading.Lock()
        self.connected = False
        self._last_attempt = 0.0
        self._ever = False

    def connect(self):
        if self.connected:
            return True
        now = time.monotonic()
        backoff = RECONNECT_RETRY if self._ever else RECONNECT_BACKOFF
        if now - self._last_attempt < backoff:
            return False
        self._last_attempt = now
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(CONNECT_TIMEOUT)
            s.connect((self.ip, self.port))
            with self.lock:
                self.sock = s
                self.connected = True
                self._ever = True
            return True
        except Exception:
            self.connected = False
            return False

    def read_mm(self):
        cmd = bytes([STX, 0x43, 0xB0, 0x01, ETX, (0x43 ^ 0xB0 ^ 0x01)])
        if not self.connected and not self.connect():
            return None
        with self.lock:
            try:
                self.sock.settimeout(0.5)
                self.sock.sendall(cmd)
                resp = self.sock.recv(6)
                self.sock.settimeout(SENSOR_TIMEOUT)
                if resp and len(resp) == 6 and resp[1] == 0x06:
                    raw = (resp[2] << 8) | resp[3]
                    if raw > 32767:
                        raw -= 65536
                    return raw * 0.01
            except Exception:
                try:
                    self.sock.close()
                except Exception:
                    pass
                self.sock = None
                self.connected = False
        return None


def tcp_reachable(ip, port, timeout=2.0):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        rc = s.connect_ex((str(ip), int(port)))
        s.close()
        return rc == 0
    except Exception:
        return False


# ----------------------------------------------------------------------------
# Cloud calls
# ----------------------------------------------------------------------------
def activate(server_url, device_id, device_key):
    """Validate the activation code; returns the customer-details dict or raises."""
    r = requests.post(f"{server_url}/agent/activate",
                      json={"device_id": device_id, "device_key": device_key},
                      timeout=10)
    if r.status_code == 200:
        return r.json()
    raise RuntimeError(r.json().get("error", f"HTTP {r.status_code}"))


# ----------------------------------------------------------------------------
# Upload loop
# ----------------------------------------------------------------------------
class Uploader(threading.Thread):
    def __init__(self, cfg):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.stop_flag = threading.Event()
        self.status = {"running": False, "last_post": None, "last_code": None, "sensors": {}}
        self.sensors = {}
        for sid, s in cfg["sensors"].items():
            self.sensors[sid] = CD22Sensor(s["ip"], s.get("port", SENSOR_PORT_DEFAULT), f"Sensor {sid}")

    def run(self):
        cfg = self.cfg
        url = cfg["server_url"].rstrip("/") + "/ingest/readings"
        sess = requests.Session()
        sess.headers.update({
            "Content-Type": "application/json",
            "X-Device-Id":  cfg["device_id"],
            "X-Device-Key": cfg["device_key"],
        })
        self.status["running"] = True
        print(f"[agent] uploading to {url} as {cfg['device_id']} ({cfg.get('customer_name','?')})", flush=True)
        while not self.stop_flag.is_set():
            t0 = time.monotonic()
            payload = {"timestamp": datetime.datetime.now().isoformat()}
            any_val = False
            for idx, (sid, sensor) in enumerate(self.sensors.items()):
                if SIMULATE:
                    # Slowly varying synthetic distance (~18-22 mm) per sensor.
                    v = 20.0 + 2.0 * math.sin(time.monotonic() / 3.0 + idx)
                else:
                    v = sensor.read_mm()
                payload[f"sensor_{sid}"] = round(v, 3) if v is not None else None
                self.status["sensors"][sid] = v is not None
                any_val = any_val or v is not None
            if any_val:
                try:
                    r = sess.post(url, json=payload, timeout=5)
                    self.status["last_post"] = payload["timestamp"]
                    self.status["last_code"] = r.status_code
                except Exception as e:
                    self.status["last_code"] = f"err: {e}"
            time.sleep(max(0.0, POST_INTERVAL - (time.monotonic() - t0)))
        self.status["running"] = False

    def stop(self):
        self.stop_flag.set()


# ----------------------------------------------------------------------------
# Setup wizard (served from the agent itself at localhost:7000)
# ----------------------------------------------------------------------------
WIZARD_HTML = """<!doctype html><html><head><meta charset=utf-8>
<title>Thickness Agent - Setup</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>
 body{font-family:system-ui,Arial,sans-serif;max-width:520px;margin:32px auto;padding:0 16px;color:#1a2330}
 h1{font-size:20px} .card{border:1px solid #d7dee8;border-radius:12px;padding:20px;margin:16px 0;box-shadow:0 1px 3px #0001}
 label{display:block;font-size:13px;font-weight:600;margin:12px 0 4px} input{width:100%;padding:9px;border:1px solid #c3ccd9;border-radius:8px;font-size:14px;box-sizing:border-box}
 button{background:#1769ff;color:#fff;border:0;border-radius:8px;padding:11px 18px;font-size:14px;font-weight:600;cursor:pointer;margin-top:16px}
 button:disabled{opacity:.5;cursor:default} .muted{color:#6b7787;font-size:13px} .ok{color:#188a42;font-weight:600} .err{color:#c0291c;font-weight:600}
 .row{display:flex;gap:10px} .row>div{flex:1} .hide{display:none}
 .pill{display:inline-block;background:#eef3fb;border-radius:20px;padding:4px 12px;font-size:13px;margin:2px}
</style></head><body>
<h1>Thickness Agent &mdash; Setup</h1>

<div class=card id=step1>
 <div class=muted>Step 1 of 3 &mdash; Activate this device</div>
 <label>Activation Device ID</label><input id=did placeholder="dev_xxxxxxxx">
 <label>Activation Key</label><input id=key placeholder="paste your key">
 <button id=actBtn onclick=activate()>Activate</button>
 <div id=actMsg class=muted style=margin-top:10px></div>
</div>

<div class="card hide" id=step2>
 <div class=muted>Step 2 of 3 &mdash; Confirm</div>
 <p>Customer: <span class=pill id=custName></span></p>
 <p>Mode: <span class=pill id=mode></span></p>
 <label>Sensor IPs</label>
 <div id=sensorInputs></div>
 <button onclick=testConn()>Test connection</button>
 <div id=testMsg class=muted style=margin-top:10px></div>
</div>

<div class="card hide" id=step3>
 <div class=muted>Step 3 of 3 &mdash; Done</div>
 <p class=ok id=doneMsg></p>
 <p class=muted>You can close this page. Monitoring runs in the background and starts on boot.</p>
</div>

<script>
let A={};
async function post(p,b){let r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});return {ok:r.ok,data:await r.json()};}
async function activate(){
 let did=document.getElementById('did').value.trim(), key=document.getElementById('key').value.trim();
 let m=document.getElementById('actMsg'); m.textContent='Checking...'; m.className='muted';
 let r=await post('/api/activate',{device_id:did,device_key:key});
 if(!r.ok){m.textContent=r.data.error||'Activation failed';m.className='err';return;}
 A=r.data; A.device_id=did; A.device_key=key;
 document.getElementById('custName').textContent=A.customer_name||'(unnamed)';
 document.getElementById('mode').textContent=A.sensor_mode+' ('+A.sensor_count+' sensors)';
 let html=''; A.sensor_labels.forEach(function(s,i){
   let ip=(s=='A')?'192.168.1.200':(s=='B')?'192.168.1.201':'192.168.1.202';
   html+='<div class=row><div><label>Sensor '+s+' IP</label><input id=ip_'+s+' value="'+ip+'"></div>'+
         '<div><label>Port</label><input id=port_'+s+' value="8234"></div></div>';});
 document.getElementById('sensorInputs').innerHTML=html;
 document.getElementById('step1').classList.add('hide');
 document.getElementById('step2').classList.remove('hide');
}
function gatherSensors(){let s={};A.sensor_labels.forEach(function(l){
  s[l]={ip:document.getElementById('ip_'+l).value.trim(),port:parseInt(document.getElementById('port_'+l).value.trim())||8234};});return s;}
async function testConn(){
 let m=document.getElementById('testMsg'); m.textContent='Testing...'; m.className='muted';
 let r=await post('/api/test',{sensors:gatherSensors()});
 if(!r.ok){m.textContent=r.data.error||'Test failed';m.className='err';return;}
 let parts=Object.entries(r.data.sensors).map(function(e){return e[0]+': '+(e[1]?'OK':'unreachable');});
 parts.push('server: '+(r.data.server?'OK':'unreachable'));
 let allok=Object.values(r.data.sensors).every(Boolean)&&r.data.server;
 m.innerHTML=parts.join(' &nbsp; '); m.className=allok?'ok':'err';
 if(allok) save();
}
async function save(){
 let r=await post('/api/save',{device_id:A.device_id,device_key:A.device_key,
   server_url:A.server_url,customer_name:A.customer_name,sensor_mode:A.sensor_mode,sensors:gatherSensors()});
 if(r.ok){document.getElementById('step2').classList.add('hide');
   document.getElementById('step3').classList.remove('hide');
   document.getElementById('doneMsg').textContent='Monitoring started for '+(A.customer_name||'this device')+'.';}
}
</script></body></html>"""


class WizardHandler(BaseHTTPRequestHandler):
    server_url = DEFAULT_SERVER_URL
    on_saved = None   # callback(cfg)

    def log_message(self, *a):
        pass  # quiet

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        body = WIZARD_HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._json(400, {"error": "bad json"})

        if self.path == "/api/activate":
            try:
                info = activate(WizardHandler.server_url, data.get("device_id", ""), data.get("device_key", ""))
                info["server_url"] = WizardHandler.server_url
                return self._json(200, info)
            except Exception as e:
                return self._json(401, {"error": str(e)})

        if self.path == "/api/test":
            sensors = data.get("sensors", {})
            result = {sid: tcp_reachable(s["ip"], s.get("port", SENSOR_PORT_DEFAULT)) for sid, s in sensors.items()}
            server_ok = False
            try:
                server_ok = requests.get(WizardHandler.server_url + "/sensors/status", timeout=6).status_code == 200
            except Exception:
                server_ok = False
            return self._json(200, {"sensors": result, "server": server_ok})

        if self.path == "/api/save":
            cfg = {
                "server_url":    data.get("server_url", WizardHandler.server_url),
                "device_id":     data["device_id"],
                "device_key":    data["device_key"],
                "customer_name": data.get("customer_name"),
                "sensor_mode":   data.get("sensor_mode"),
                "sensors":       data["sensors"],
            }
            path = save_config(cfg)
            print(f"[agent] config saved to {path}", flush=True)
            if WizardHandler.on_saved:
                WizardHandler.on_saved(cfg)
            return self._json(200, {"saved": True})

        return self._json(404, {"error": "not found"})


def config_from_env():
    """Headless activation: build a config from DEVICE_ID + DEVICE_KEY (+ optional
    SENSORS="A=192.168.1.200,B=192.168.1.201") env vars, validating with the server.
    Lets a Raspberry Pi be configured with no browser. Returns a config dict or None."""
    did = os.environ.get("DEVICE_ID")
    key = os.environ.get("DEVICE_KEY")
    if not (did and key):
        return None
    server = DEFAULT_SERVER_URL
    try:
        info = activate(server, did, key)
    except Exception as e:
        print(f"[agent] env activation failed: {e}", flush=True)
        return None
    sensors = {}
    raw = os.environ.get("SENSORS", "")
    if raw:
        for part in raw.split(","):
            if "=" in part:
                sid, addr = part.split("=", 1)
                bits = addr.strip().split(":")
                sensors[sid.strip().upper()] = {
                    "ip": bits[0].strip(),
                    "port": int(bits[1]) if len(bits) > 1 else SENSOR_PORT_DEFAULT,
                }
    if not sensors:
        defaults = {"A": "192.168.1.200", "B": "192.168.1.201", "C": "192.168.1.202"}
        for lbl in info.get("sensor_labels", ["A", "B"]):
            sensors[lbl] = {"ip": defaults.get(lbl, "192.168.1.200"), "port": SENSOR_PORT_DEFAULT}
    return {
        "server_url": server, "device_id": did, "device_key": key,
        "customer_name": info.get("customer_name"), "sensor_mode": info.get("sensor_mode"),
        "sensors": sensors,
    }


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    cfg = load_config()
    if not cfg:
        # Headless path: configure from environment variables if provided.
        env_cfg = config_from_env()
        if env_cfg:
            save_config(env_cfg)
            cfg = env_cfg
            print(f"[agent] configured from environment for {cfg.get('customer_name','?')}", flush=True)
    uploader_holder = {"u": None}

    def start_uploader(c):
        if uploader_holder["u"]:
            uploader_holder["u"].stop()
        u = Uploader(c)
        u.start()
        uploader_holder["u"] = u

    WizardHandler.server_url = (cfg or {}).get("server_url", DEFAULT_SERVER_URL)
    WizardHandler.on_saved = start_uploader

    # Always serve the wizard (also used for re-configuration).
    httpd = ThreadingHTTPServer((WIZARD_HOST, WIZARD_PORT), WizardHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"[agent] setup wizard at http://{WIZARD_HOST}:{WIZARD_PORT}"
          + ("  (SIMULATE mode)" if SIMULATE else ""), flush=True)

    if cfg and cfg.get("device_id") and cfg.get("sensors"):
        print(f"[agent] configured for {cfg.get('customer_name','?')} ({cfg['device_id']}); starting.", flush=True)
        start_uploader(cfg)
    else:
        print("[agent] NOT configured yet — open the setup wizard to activate.", flush=True)

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n[agent] stopped.")


if __name__ == "__main__":
    main()
