"""
local_license — offline activation for the local appliance build (thickness-local).

How it works (no internet involved at any point):

  1. Every install computes a stable MACHINE CODE from /etc/machine-id
     (hash + product salt, formatted XXXX-XXXX-XXXX-XXXX). It is shown on the
     activation page the appliance serves before it is licensed.
  2. The warehouse runs tools/local_license_tool.py, which signs a license
     payload (customer, sensor mode, machine code, optional expiry, initial
     admin password hash) with our Ed25519 PRIVATE key. The private key never
     leaves the warehouse machine.
  3. The customer (or the bench technician) pastes the license code into the
     activation page. This module verifies the signature with the PUBLIC key
     bundled in the app, checks the machine code + expiry, stores the license,
     and seeds the local tenant (company + admin login + dev_legacy device).

Revocation reality: an offline box cannot be revoked remotely. The enforcement
lever is `expires_at` — issue time-limited licenses (e.g. yearly) and hand out
renewal codes. Perpetual licenses are supported for outright sales.

Public key resolution order:
  $THICKNESS_LICENSE_PUBKEY (hex) → license_pubkey.txt next to this file.
Only registered when merged_server runs with LOCAL_MODE=true.
"""
import base64
import datetime
import hashlib
import json
import os
import threading
import uuid

_PRODUCT_SALT = "rajdeep-thickness-local-v1"
_CODE_PREFIX = "THICK1"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_state_lock = threading.Lock()
_cached = {"loaded": False, "payload": None, "error": None}
_data_dir = BASE_DIR
_on_activated = None


# ------------------------------------------------------------------ utilities
def _b64d(s):
    s = s.strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _windows_machine_id():
    """Windows counterpart of /etc/machine-id: the MachineGuid the OS writes at
    install time. Stable across reboots, driver updates and NIC changes.

    This must NOT fall back to uuid.getnode(): on a box with no adapter Windows
    considers 'suitable' (common on WiFi-only mini-PCs), getnode() returns a
    synthetic local-only node with the multicast bit set rather than a real MAC,
    and that value is not guaranteed to survive a reboot. Binding a license to
    it would silently lock the customer out of their own appliance.
    """
    try:
        import winreg
    except ImportError:
        return None
    for access in (winreg.KEY_READ | winreg.KEY_WOW64_64KEY, winreg.KEY_READ):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Cryptography", 0, access) as k:
                guid = winreg.QueryValueEx(k, "MachineGuid")[0]
            guid = (guid or "").strip()
            if guid:
                return guid
        except OSError:
            continue
    return None


def machine_code():
    """Stable per-machine code derived from the OS machine id."""
    mid = None
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(p) as f:
                mid = f.read().strip()
            if mid:
                break
        except OSError:
            continue
    if not mid and os.name == "nt":
        mid = _windows_machine_id()
    if not mid:
        mid = f"mac-{uuid.getnode():012x}"   # last resort (non-Windows dev boxes)
    digest = hashlib.sha256(f"{_PRODUCT_SALT}::{mid}".encode()).hexdigest().upper()[:16]
    return "-".join(digest[i:i + 4] for i in range(0, 16, 4))


def _public_key_hex():
    env = os.environ.get("THICKNESS_LICENSE_PUBKEY", "").strip()
    if env:
        return env
    try:
        with open(os.path.join(BASE_DIR, "license_pubkey.txt")) as f:
            return f.read().strip()
    except OSError:
        return ""


def verify_code(code):
    """Verify a pasted license code. Returns the payload dict or raises ValueError."""
    code = "".join((code or "").split())          # tolerate wrapped/pasted whitespace
    parts = code.split(".")
    if len(parts) != 3 or parts[0] != _CODE_PREFIX:
        raise ValueError("That does not look like a valid license code.")
    try:
        payload_bytes = _b64d(parts[1])
        signature = _b64d(parts[2])
        payload = json.loads(payload_bytes)
    except Exception:
        raise ValueError("License code is corrupted — re-copy it from the activation card.")

    pub_hex = _public_key_hex()
    if not pub_hex:
        raise ValueError("This build has no license public key installed — contact Rajdeep Analytics.")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
    except ImportError:
        raise ValueError("License verification library missing from this build.")
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex)).verify(signature, payload_bytes)
    except (InvalidSignature, ValueError):
        raise ValueError("License signature is invalid. Use the exact code issued for this machine.")

    bound = (payload.get("machine_code") or "").strip().upper()
    if bound and bound != "*" and bound != machine_code():
        raise ValueError(
            f"This license was issued for a different machine "
            f"(this machine's code is {machine_code()}).")
    return payload


def _expiry_check(payload):
    """Returns (valid, reason). reason is None when valid."""
    exp = payload.get("expires_at")
    if not exp:
        return True, None
    try:
        exp_date = datetime.date.fromisoformat(str(exp)[:10])
    except ValueError:
        return False, "License has an invalid expiry date."
    if datetime.date.today() > exp_date:
        return False, f"License expired on {exp_date.isoformat()}."
    return True, None


def _license_file():
    return os.path.join(_data_dir, "license.key")


def _load_stored():
    with _state_lock:
        if _cached["loaded"]:
            return
        _cached["loaded"] = True
        try:
            with open(_license_file()) as f:
                code = f.read()
        except OSError:
            return
        try:
            _cached["payload"] = verify_code(code)
        except ValueError as e:
            _cached["error"] = str(e)


def current_status():
    _load_stored()
    payload = _cached["payload"]
    status = {
        "activated": payload is not None,
        "valid": False,
        "reason": _cached["error"],
        "machine_code": machine_code(),
    }
    if payload:
        ok, reason = _expiry_check(payload)
        status["valid"] = ok
        status["reason"] = reason
        status.update({
            "customer": payload.get("customer"),
            "sensor_mode": payload.get("sensor_mode"),
            "license_id": payload.get("license_id"),
            "issued_at": payload.get("issued_at"),
            "expires_at": payload.get("expires_at"),
        })
        if payload.get("expires_at") and ok:
            try:
                exp = datetime.date.fromisoformat(str(payload["expires_at"])[:10])
                status["days_left"] = (exp - datetime.date.today()).days
            except ValueError:
                pass
    if not status["activated"] and not status["reason"]:
        status["reason"] = "Not activated yet."
    return status


# ------------------------------------------------------------- tenant seeding
def _seed_tenant(payload):
    """Create the local company, its admin login, and the dev_legacy device row
    so the existing multi-tenant frontend works unchanged. Idempotent — renewal
    codes re-run this without clobbering changed passwords or extra users."""
    import psycopg2   # the local build installs the SQLite shim under this name
    customer = payload["customer"]
    mode = payload.get("sensor_mode", "opposite")
    conn = psycopg2.connect()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO customers (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (customer,))
        cur.execute("SELECT id FROM customers WHERE name=%s", (customer,))
        cid = cur.fetchone()[0]

        cur.execute("SELECT 1 FROM devices WHERE device_id='dev_legacy'")
        if cur.fetchone():
            cur.execute(
                "UPDATE devices SET customer_id=%s, sensor_mode=%s, label=%s, revoked=0 "
                "WHERE device_id='dev_legacy'",
                (cid, mode, f"{customer} — Local"))
        else:
            cur.execute(
                "INSERT INTO devices (device_id, customer_id, device_key_hash, sensor_mode, label, revoked) "
                "VALUES ('dev_legacy', %s, '!', %s, %s, 0)",
                (cid, mode, f"{customer} — Local"))

        admin_user = payload.get("admin_username") or "admin"
        admin_hash = payload.get("admin_password_hash")
        if admin_hash:
            cur.execute("SELECT 1 FROM users WHERE username=%s AND customer_id=%s", (admin_user, cid))
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO users (username, email, password_hash, role, customer_id) "
                    "VALUES (%s, NULL, %s, 'superadmin', %s)",
                    (admin_user, admin_hash, cid))
        conn.commit()
    finally:
        conn.close()


# -------------------------------------------------------------- activation UI
_PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>Thickness Monitor — Activation</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>
 body{font-family:system-ui,Arial,sans-serif;max-width:560px;margin:40px auto;padding:0 16px;color:#1a2330;background:#f4f7fb}
 .card{background:#fff;border:1px solid #d7dee8;border-radius:14px;padding:26px;box-shadow:0 2px 8px #0001}
 h1{font-size:20px;margin:0 0 4px} .muted{color:#6b7787;font-size:13px}
 .mc{font-size:26px;font-weight:700;letter-spacing:2px;background:#eef3fb;border-radius:10px;
     padding:14px;text-align:center;margin:14px 0;user-select:all}
 textarea{width:100%;height:110px;border:1px solid #c3ccd9;border-radius:8px;padding:10px;
     font-family:ui-monospace,Consolas,monospace;font-size:12px;box-sizing:border-box}
 button{background:#1769ff;color:#fff;border:0;border-radius:8px;padding:12px 20px;font-size:14px;
     font-weight:600;cursor:pointer;margin-top:14px;width:100%}
 .ok{color:#188a42;font-weight:600} .err{color:#c0291c;font-weight:600}
 .banner{border-radius:8px;padding:10px 14px;margin:0 0 14px;font-size:14px}
 .banner.err{background:#fdecea} .banner.ok{background:#e8f6ee}
 .row{margin:6px 0;font-size:14px} .row b{display:inline-block;min-width:110px}
</style></head><body>
<div class=card>
 <h1>Thickness Monitoring System</h1>
 <div class=muted>Rajdeep Analytics — Local Appliance</div>
 <div id=banner></div>
 <p class=muted style="margin-top:18px">Machine code (give this to Rajdeep Analytics to receive your license):</p>
 <div class=mc id=mc>…</div>
 <div id=form>
  <p class=muted>Paste the license code from your activation card:</p>
  <textarea id=code placeholder="THICK1.…"></textarea>
  <button onclick="activate()">Activate</button>
  <p id=msg class=muted></p>
 </div>
 <div id=done style="display:none">
  <div class=row><b>Customer</b><span id=cust></span></div>
  <div class=row><b>Mode</b><span id=mode></span></div>
  <div class=row><b>Valid until</b><span id=exp></span></div>
  <p class=muted>Log in with the Company / Username / Password printed on your activation card.</p>
  <button onclick="location.href='/'">Open dashboard</button>
 </div>
</div>
<script>
async function refresh(){
 const s = await (await fetch('/license/status')).json();
 document.getElementById('mc').textContent = s.machine_code;
 const b = document.getElementById('banner');
 if(s.activated && !s.valid){ b.className='banner err'; b.textContent=s.reason+' Contact Rajdeep Analytics with the machine code below for a renewal code.'; }
 else if(!s.activated){ b.className='banner'; b.textContent=''; }
 else { b.className='banner ok'; b.textContent='Licensed to '+s.customer+'. Paste a new code below only to renew.'; }
}
async function activate(){
 const m = document.getElementById('msg'); m.textContent='Checking…'; m.className='muted';
 const r = await fetch('/license/activate',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({code:document.getElementById('code').value})});
 const d = await r.json();
 if(!r.ok){ m.textContent=d.error||'Activation failed'; m.className='err'; return; }
 document.getElementById('form').style.display='none';
 document.getElementById('done').style.display='block';
 document.getElementById('cust').textContent=d.customer;
 document.getElementById('mode').textContent=d.sensor_mode;
 document.getElementById('exp').textContent=d.expires_at||'perpetual';
}
refresh();
</script></body></html>"""


# ----------------------------------------------------------------- Flask glue
def register_license(app, data_dir, on_activated=None):
    """Wire the license gate + endpoints into the Flask app (LOCAL_MODE only).
    on_activated(payload) runs after a successful activation — merged_server
    uses it to write the default sensor_network.json for the licensed mode."""
    global _data_dir, _on_activated
    _data_dir = data_dir
    _on_activated = on_activated

    from flask import request, jsonify, Response

    @app.route("/license/status", methods=["GET"])
    def license_status():
        return jsonify(current_status()), 200

    @app.route("/license", methods=["GET"])
    def license_page():
        return Response(_PAGE, mimetype="text/html")

    @app.route("/license/activate", methods=["POST"])
    def license_activate():
        code = ((request.get_json(silent=True) or {}).get("code") or "")
        try:
            payload = verify_code(code)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        ok, reason = _expiry_check(payload)
        if not ok:
            return jsonify({"error": reason}), 400
        os.makedirs(_data_dir, exist_ok=True)
        with open(_license_file(), "w") as f:
            f.write("".join(code.split()))
        with _state_lock:
            _cached.update({"loaded": True, "payload": payload, "error": None})
        try:
            _seed_tenant(payload)
        except Exception as e:
            return jsonify({"error": f"License accepted but local setup failed: {e}"}), 500
        if _on_activated:
            try:
                _on_activated(payload)
            except Exception as e:
                print(f"[license] on_activated hook failed: {e}", flush=True)
        print(f"[license] activated for {payload.get('customer')} "
              f"({payload.get('license_id')}, expires {payload.get('expires_at') or 'never'})", flush=True)
        return jsonify({
            "activated": True,
            "customer": payload.get("customer"),
            "sensor_mode": payload.get("sensor_mode"),
            "expires_at": payload.get("expires_at"),
        }), 200

    @app.before_request
    def _license_gate():
        status = current_status()
        if status["valid"]:
            return None
        path = request.path or "/"
        if path.startswith("/license") or path.startswith("/socket.io"):
            return None
        if request.method == "GET":
            return Response(_PAGE, mimetype="text/html")
        return jsonify({"error": "License required", "license_required": True,
                        "reason": status["reason"]}), 403
