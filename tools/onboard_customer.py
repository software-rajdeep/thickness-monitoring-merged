#!/usr/bin/env python3
"""Onboard a new customer: provision device(s) + company login, print the key card.

The single command we run when a customer buys the product. Talks to the live
cloud server's /provision endpoint (admin-token protected) and prints everything
the customer needs: activation key card(s) and their dashboard login.

Usage:
    python3 onboard_customer.py "Acme Steel"                      # 1 opposite-mode device
    python3 onboard_customer.py "Acme Steel" --devices 2 --label "Line {n}"
    python3 onboard_customer.py "Acme Steel" --mode sbs
    python3 onboard_customer.py "Acme Steel" --out cards/

The admin token comes from --token or the PROVISION_ADMIN_TOKEN env var
(it lives in the merged.service unit on the KVM: systemctl show merged -p Environment).

The device_key and admin password are shown ONCE — the server stores only
hashes. Save the key card immediately; a lost key means revoke + re-provision.
"""
import argparse
import json
import os
import sys
import urllib.request

DEFAULT_SERVER = "https://194-164-148-145.sslip.io"
DASHBOARD_URL = "https://merged-version.vercel.app"


def provision(server, token, customer, mode, label):
    req = urllib.request.Request(
        server.rstrip("/") + "/provision",
        data=json.dumps({"customer": customer, "sensor_mode": mode, "label": label}).encode(),
        headers={"Content-Type": "application/json", "X-Admin-Token": token},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def key_card(d, server):
    lines = [
        "=" * 62,
        "  THICKNESS MONITORING — DEVICE ACTIVATION CARD",
        "=" * 62,
        f"  Company     : {d['customer']}",
        f"  Device      : {d['device_id']}   ({d.get('label') or 'unlabelled'})",
        f"  Mode        : {d['sensor_mode']}",
        "",
        "  Enter these in the Thickness Agent setup wizard:",
        f"    Device ID  : {d['device_id']}",
        f"    Device Key : {d['device_key']}",
        "",
        f"  Server URL  : {server}",
        "    (if HTTPS fails on your network, use http://194.164.148.145:8082)",
    ]
    if d.get("admin_password"):
        lines += [
            "-" * 62,
            f"  DASHBOARD LOGIN — {DASHBOARD_URL}",
            f"    Company  : {d['company']}",
            f"    Username : {d['admin_username']}",
            f"    Password : {d['admin_password']}",
            "    (company administrator — can add more users in Backend page)",
        ]
    lines += ["=" * 62, ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Provision a customer + device(s) and print key cards.")
    ap.add_argument("customer", help="Company name, e.g. 'Acme Steel' (also their dashboard company field)")
    ap.add_argument("--mode", choices=["opposite", "sbs"], default="opposite")
    ap.add_argument("--devices", type=int, default=1, help="How many devices/stations (default 1)")
    ap.add_argument("--label", default="Line {n}", help="Device label template, {n} = 1..N")
    ap.add_argument("--server", default=os.environ.get("THICKNESS_SERVER", DEFAULT_SERVER))
    ap.add_argument("--token", default=os.environ.get("PROVISION_ADMIN_TOKEN"))
    ap.add_argument("--out", default=None, help="Directory to also save the key card as a text file")
    args = ap.parse_args()

    if not args.token:
        sys.exit("No admin token. Pass --token or set PROVISION_ADMIN_TOKEN.\n"
                 "It is in the merged.service env on the KVM: systemctl show merged -p Environment")

    cards = []
    for n in range(1, args.devices + 1):
        label = args.label.replace("{n}", str(n))
        try:
            d = provision(args.server, args.token, args.customer, args.mode, label)
        except urllib.error.HTTPError as e:
            sys.exit(f"Provision failed ({e.code}): {e.read().decode()[:300]}")
        cards.append(key_card(d, args.server))

    text = "\n".join(cards)
    print(text)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        slug = "".join(c if c.isalnum() else "_" for c in args.customer.lower())
        path = os.path.join(args.out, f"keycard_{slug}.txt")
        with open(path, "w") as f:
            f.write(text)
        print(f"[saved to {path}]")


if __name__ == "__main__":
    main()
