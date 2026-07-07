#!/usr/bin/env python3
"""Warehouse tool for the LOCAL (offline) appliance — issue and track licenses.

This is the local-product counterpart of onboard_customer.py. It never talks
to any server: it signs license codes with our Ed25519 private key, and the
appliance verifies them offline with the public key bundled in the app.

Commands:
    init                        Create the signing keypair + registry (once).
                                Writes the PUBLIC key into backend/license_pubkey.txt
                                (commit that file; it ships inside the app).
    issue "Acme Steel" --machine ABCD-1234-EF56-7890 [options]
                                Sign a license for one appliance and print the
                                activation card. The machine code is shown on
                                the appliance's activation page after the .deb
                                is installed.
    list                        Show every license ever issued (the registry).

Key custody: the private key lives in the key directory (default
~/thickness-license-keys, or %USERPROFILE%\\thickness-license-keys on Windows;
override with --keydir or THICKNESS_LICENSE_KEYDIR). Back that directory up —
losing it means you cannot issue licenses for existing installs, and leaking
it means anyone can mint licenses.

Requires:  pip install cryptography werkzeug
"""
import argparse
import base64
import datetime
import json
import os
import secrets
import sys

CODE_PREFIX = "THICK1"
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PUBKEY_FILE_IN_REPO = os.path.join(REPO_ROOT, "backend", "license_pubkey.txt")


def default_keydir():
    return os.environ.get("THICKNESS_LICENSE_KEYDIR") or os.path.join(
        os.path.expanduser("~"), "thickness-license-keys")


def b64u(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def load_private_key(keydir):
    path = os.path.join(keydir, "license_signing_key.hex")
    if not os.path.exists(path):
        sys.exit(f"No signing key at {path} — run:  python3 local_license_tool.py init")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    with open(path) as f:
        return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(f.read().strip()))


def registry_path(keydir):
    return os.path.join(keydir, "registry.json")


def load_registry(keydir):
    try:
        with open(registry_path(keydir)) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def save_registry(keydir, entries):
    with open(registry_path(keydir), "w") as f:
        json.dump(entries, f, indent=2)


def cmd_init(args):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    keydir = args.keydir
    os.makedirs(keydir, exist_ok=True)
    key_path = os.path.join(keydir, "license_signing_key.hex")
    if os.path.exists(key_path) and not args.force:
        sys.exit(f"Signing key already exists at {key_path}.\n"
                 "Re-running init would orphan every license already issued. "
                 "Use --force only if you really mean it.")
    private = Ed25519PrivateKey.generate()
    priv_hex = private.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
        serialization.NoEncryption()).hex()
    pub_hex = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    with open(key_path, "w") as f:
        f.write(priv_hex + "\n")
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    with open(os.path.join(keydir, "license_pubkey.txt"), "w") as f:
        f.write(pub_hex + "\n")
    with open(PUBKEY_FILE_IN_REPO, "w") as f:
        f.write(pub_hex + "\n")
    if not os.path.exists(registry_path(keydir)):
        save_registry(keydir, [])
    print(f"Keypair created.")
    print(f"  PRIVATE key : {key_path}   <-- back this up, never commit it")
    print(f"  PUBLIC key  : {PUBKEY_FILE_IN_REPO}   <-- commit this; it ships in the app")
    print(f"  Registry    : {registry_path(keydir)}")


def activation_card(payload, code, admin_password):
    exp = payload.get("expires_at") or "perpetual (no expiry)"
    lines = [
        "=" * 62,
        "  THICKNESS MONITORING — LOCAL APPLIANCE ACTIVATION CARD",
        "=" * 62,
        f"  Company      : {payload['customer']}",
        f"  Mode         : {payload['sensor_mode']}",
        f"  License ID   : {payload['license_id']}",
        f"  Machine code : {payload['machine_code']}",
        f"  Valid until  : {exp}",
        "",
        "  LICENSE CODE — paste into the appliance's activation page",
        "  (open http://<appliance-ip> in any browser on the same network):",
        "",
    ]
    # wrap the long code for printing
    for i in range(0, len(code), 58):
        lines.append("    " + code[i:i + 58])
    lines += [
        "",
        "-" * 62,
        "  DASHBOARD LOGIN — http://<appliance-ip>",
        f"    Company  : {payload['customer']}",
        f"    Username : {payload.get('admin_username', 'admin')}",
        f"    Password : {admin_password}",
        "    (administrator — can add more users from the Backend page)",
        "=" * 62,
        "",
    ]
    return "\n".join(lines)


def cmd_issue(args):
    from werkzeug.security import generate_password_hash
    private = load_private_key(args.keydir)

    machine = (args.machine or "").strip().upper()
    if not machine and not args.any_machine:
        sys.exit("Provide --machine XXXX-XXXX-XXXX-XXXX (shown on the appliance's "
                 "activation page) or --any-machine to skip machine binding.")
    expires = None
    if args.days:
        expires = (datetime.date.today() + datetime.timedelta(days=args.days)).isoformat()
    if args.expires:
        expires = args.expires

    admin_password = args.admin_password or secrets.token_urlsafe(9)
    payload = {
        "v": 1,
        "license_id": "lic_" + secrets.token_hex(4),
        "customer": args.customer.strip(),
        "sensor_mode": args.mode,
        "machine_code": machine or "*",
        "issued_at": datetime.date.today().isoformat(),
        "expires_at": expires,
        "admin_username": args.admin_username,
        "admin_password_hash": generate_password_hash(admin_password),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    code = f"{CODE_PREFIX}.{b64u(payload_bytes)}.{b64u(private.sign(payload_bytes))}"

    card = activation_card(payload, code, admin_password)
    print(card)

    entries = load_registry(args.keydir)
    entries.append({
        "license_id": payload["license_id"],
        "customer": payload["customer"],
        "sensor_mode": payload["sensor_mode"],
        "machine_code": payload["machine_code"],
        "issued_at": payload["issued_at"],
        "expires_at": payload["expires_at"],
        "note": args.note or "",
    })
    save_registry(args.keydir, entries)

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        slug = "".join(c if c.isalnum() else "_" for c in args.customer.lower())
        path = os.path.join(args.out, f"license_{slug}_{payload['license_id']}.txt")
        with open(path, "w") as f:
            f.write(card)
        print(f"[card saved to {path}]")
    print(f"[registry updated: {registry_path(args.keydir)} — "
          f"{len(entries)} license(s) issued to date]")


def cmd_list(args):
    entries = load_registry(args.keydir)
    if not entries:
        print("No licenses issued yet.")
        return
    fmt = "{:<12} {:<24} {:<9} {:<20} {:<11} {:<11} {}"
    print(fmt.format("LICENSE", "CUSTOMER", "MODE", "MACHINE", "ISSUED", "EXPIRES", "NOTE"))
    for e in entries:
        print(fmt.format(e["license_id"], e["customer"][:24], e["sensor_mode"],
                         e["machine_code"], e["issued_at"], e.get("expires_at") or "never",
                         e.get("note", "")))


def main():
    ap = argparse.ArgumentParser(description="Issue offline licenses for the thickness-local appliance.")
    ap.add_argument("--keydir", default=default_keydir(),
                    help="Directory holding the signing key + registry (default: ~/thickness-license-keys)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="Create the signing keypair (run once)")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("issue", help="Sign a license and print the activation card")
    p.add_argument("customer", help="Company name — also their dashboard login company")
    p.add_argument("--mode", choices=["opposite", "sbs"], default="opposite")
    p.add_argument("--machine", help="Machine code from the appliance's activation page")
    p.add_argument("--any-machine", action="store_true",
                   help="Do not bind to a machine (spare/loaner licenses)")
    p.add_argument("--days", type=int, help="License term in days from today (e.g. 365)")
    p.add_argument("--expires", help="Explicit expiry date YYYY-MM-DD (overrides --days)")
    p.add_argument("--admin-username", default="admin")
    p.add_argument("--admin-password", help="Initial dashboard admin password (generated if omitted)")
    p.add_argument("--note", help="Free-text note stored in the registry")
    p.add_argument("--out", help="Directory to also save the card as a text file")
    p.set_defaults(fn=cmd_issue)

    p = sub.add_parser("list", help="Show the license registry")
    p.set_defaults(fn=cmd_list)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
