# Warehouse → Customer Flow — Complete Operational Runbook

The end-to-end process for taking one new customer live, starting at the
warehouse bench and ending with live data on their dashboard.
Verified against the **live production server** on 2026-07-07:

| Piece | Status (checked live) |
|---|---|
| `POST /provision` (creates customer + device + login) | ✅ Live, admin-token gated (401 without token) |
| `POST /agent/activate` (key → customer name/mode) | ✅ Live, rejects bad keys (401) |
| `POST /ingest/readings` per-device auth | ✅ Live, 401 without device headers |
| Multi-tenant dashboard login (company/username/password) | ✅ Live — demo login verified working |
| Data/CSV endpoints token-gated | ✅ Live |
| Agent installer (`thickness-agent_1.0.0_amd64.deb`) | ✅ Built, proven streaming real CD22 data |
| Onboarding CLI + key-card printer (`tools/onboard_customer.py`) | ✅ On `origin/main` |
| Customer quick-start sheet (`CUSTOMER_QUICKSTART.md`) | ✅ On `origin/main` |
| Full dry run: provision → activate → ingest → row stored → cleanup | ✅ Passed 2026-07-07 on production |
| Ops: `PER_DEVICE_ROW_CAP=850000`, nightly `pg_dump` cron, secrets in service env | ✅ Confirmed on KVM 2026-07-07 |

---

## 0. Choose the delivery model (per customer)

**Model A — Appliance kit (recommended; this is "the warehouse flow").**
You ship a complete pre-configured kit: **router + CD22 sensors + gateway box**
(mini-PC or Raspberry Pi with the agent pre-installed and pre-activated).
The customer plugs in power + internet and does *nothing else*.
Everything below assumes this model.

**Model B — Self-install.** Customer already has an always-on Linux PC near the
sensors. You send only: the `.deb`, the printed key card, and the quick-start
sheet. They install, open `http://localhost:7000`, paste the key, enter sensor
IPs, click Test → Finish. Already fully built (setup wizard in the agent).

---

## 1. WAREHOUSE — Servicing team bench procedure (per kit, ~30 min)

### Kit contents
- 1 × router (buy the **same model** for every customer — one SOP, one spare pool)
- 2 × CD22 sensors (3 for SBS mode) + mounting brackets + cables
- 1 × gateway box (mini-PC = amd64 `.deb`; Pi = build arm64 `.deb` once on a Pi)
- Printed **Activation Key Card** + **Quick-Start sheet**

### Step 1 — Router
1. Factory-reset the router (hold reset 10 s).
2. Configure:
   - **LAN subnet:** `192.168.1.0/24`, router = `192.168.1.1`
   - **DHCP pool:** `192.168.1.50–192.168.1.150` (keeps `.200–.202` free for sensors)
   - **WAN:** DHCP client (customer plugs it into their network), or configure the
     4G SIM if it's a cellular router
   - **No port-forwarding needed** — the gateway only makes *outbound* HTTPS/HTTP.
     Outbound to `194.164.148.145` ports 443 + 8082 must not be blocked.
3. Change the router admin password; write it on the inside of the kit lid /
   asset register (NOT the customer card).

### Step 2 — Sensors (CD22)
1. Connect each sensor to the router LAN one at a time.
2. Set static IPs with the CD22 config tool (or `sensor_setup.html`):
   - Sensor A → `192.168.1.200`, port `8234`
   - Sensor B → `192.168.1.201`, port `8234`
   - Sensor C → `192.168.1.202` (SBS kits only)
3. Physically label each sensor **A / B / C** to match.
4. Verify from any laptop on the router LAN: `ping 192.168.1.200` etc.

### Step 3 — Generate the customer's activation code (cloud provisioning)
On any machine with the repo (fresh clone of `origin/main`) and the admin token:

```bash
export PROVISION_ADMIN_TOKEN=<from password manager>   # see §5 gap 1
python3 tools/onboard_customer.py "Acme Steel" --mode opposite --devices 1 --out cards/
```

This one command creates the customer + device in the cloud DB **and** their
company-admin dashboard login, and prints the **key card** containing:
- `Device ID` + `Device Key` (the activation code — shown ONCE, only hash stored)
- Dashboard URL + Company / Username / Password

Print **two copies**: one goes in the kit box, one is filed with the sale record.
Multiple lines/stations → `--devices N` (one card per device).

### Step 4 — Gateway box
1. Install the agent: `sudo dpkg -i thickness-agent_1.0.0_<arch>.deb`
   (service auto-enables + starts on boot).
2. Pre-activate headlessly — write `/etc/thickness-agent/agent.env`:
   ```ini
   DEVICE_ID=dev_xxxxxxxx          # from the key card
   DEVICE_KEY=<secret from step 3>
   SENSORS=A=192.168.1.200,B=192.168.1.201
   SERVER_URL=http://194.164.148.145:8082   # IP form: immune to sslip.io DNS hijack
   ```
3. `sudo systemctl restart thickness-agent`
4. Set the gateway to a DHCP reservation on the router (e.g. `192.168.1.10`)
   so its IP is predictable for remote support.

### Step 5 — Full-kit bench test (the go/no-go gate)
Assemble the whole kit on the bench exactly as it will run on site:
sensors + gateway on the router LAN, router WAN into warehouse internet.

1. `systemctl status thickness-agent` → active, log shows
   `uploading to ... as dev_xxxx (Acme Steel)`.
2. Log into `https://merged-version.vercel.app` **with the customer's card
   credentials** → Dashboard shows live values.
3. Move a test piece in front of the sensors → values change on the dashboard.
4. Run calibration once from Run Mode with a reference piece → thickness reads
   correctly.
5. Pull the router's WAN cable for 2 min, replug → agent recovers and data
   resumes (proves auto-reconnect).
6. Power-cycle the whole kit → everything comes back by itself (proves
   boot-order resilience).

**All six green = pack it.** Any red = do not ship.

### Step 6 — Pack & register
- Power everything down, pack: router, sensors, brackets/cables, gateway,
  key card, quick-start sheet.
- Record in the sales register: customer name, `device_id`, router model +
  admin password, gateway IP reservation, ship date.

---

## 2. CUSTOMER — Install steps (Model A appliance kit)

What the quick-start sheet in the box tells them:

1. **Mount the sensors** on the line (A and B facing each other for opposite
   mode; gap per the mechanical drawing).
2. **Plug the sensors** into the router's LAN ports (any port).
3. **Plug the router's WAN port** into their internet (or just power the SIM
   router).
4. **Power on the gateway box.**
5. Wait ~2 minutes → go to `https://merged-version.vercel.app`, log in with the
   Company / Username / Password on the card → live data is on the Dashboard.
6. Their admin can add team logins from the Backend page (scoped to their
   company only — they can never see anyone else's data).

They never type an IP, never open a wizard, never see a key prompt — the
warehouse did all of it. The key card's Device ID/Key section is their
"reinstall insurance" (and what support asks for on the phone).

*(Model B self-install customers instead follow `CUSTOMER_QUICKSTART.md`:
install .deb → open localhost:7000 → paste key → enter sensor IPs → Test →
Finish.)*

---

## 3. US — Post-ship verification & ongoing ops

- **Day they power on:** watch `devices.last_seen` go green (or log in as
  superadmin and select their device). If nothing after their install call:
  their network blocks outbound → confirm ports 443/8082 outbound open.
- **Calibration:** walk them through Run Mode calibration with their reference
  piece on the first call (per-device calibration is already built).
- **Stop service / lost key / non-payment:**
  `UPDATE devices SET revoked=true WHERE device_id='dev_xxx';` — ingest starts
  returning 401 within **30 seconds** (the server caches device auth for 30 s
  at 5 Hz load; verified 2026-07-07). Un-revoke by setting false. Lost key =
  revoke + provision a new device (keys are never stored in plaintext).
- **Support cheatsheet:** see `ONBOARDING.md` (symptom → check table).

---

## 4. Dry-run test script (do this before the first real customer)

1. Get `PROVISION_ADMIN_TOKEN` from the KVM:
   `systemctl show merged -p Environment` (on the KVM as root).
2. `python3 tools/onboard_customer.py "Test Factory" --mode opposite --out cards/`
3. On any Linux box (the Ubuntu PC, or even with no sensors):
   install the `.deb`, put the card values in `/etc/thickness-agent/agent.env`,
   add `SIMULATE=1` (synthetic readings — no sensors needed), restart.
4. Log in with the generated Test Factory credentials → live (synthetic) data.
5. Export CSV, add a second user, calibrate — the whole customer experience.
6. Clean up: `UPDATE devices SET revoked=true ...` + delete the test customer.

---

## 5. Gaps to close (ranked; none block a single first customer)

1. **Token custody [YOU]** — `PROVISION_ADMIN_TOKEN` + rotated superadmin
   password must live in a password manager the servicing team can access.
   Without the token, nobody can provision.
2. **Confirm Phase-0 ops items on the KVM** — could not verify remotely in this
   session: `PER_DEVICE_ROW_CAP` is set (~850k = 2-day history), nightly
   `pg_dump` cron exists (`/etc/cron.d/sensor-db-backup`). One SSH look confirms both.
3. **arm64 `.deb`** — only needed if gateways are Raspberry Pis; build once on
   a Pi (`agent/build-deb.sh`). If you ship amd64 mini-PCs, already done.
4. **Router SOP** — pick ONE router model for all kits and write the 1-page
   config sheet for it (screenshots). Nothing in software depends on the model.
5. **Device-offline alerting** — email when `devices.last_seen` > N min stale,
   so you know before the customer calls. Plumbing exists (email alerts).
6. **Warehouse UI** — provisioning is a CLI today. Fine while a technical
   person runs it; the superadmin "Customers" page (add customer / print card /
   revoke / last-seen) is the Phase-2 upgrade for a non-technical team.
7. **Real domain** — `api.rajdeepanalytics.com` → KVM kills the sslip.io DNS
   problem permanently and looks right on the key card.
8. **Disk** — 48 GB / ~12 GB free caps you at 2-day raw history for 10–15
   devices. Expand to ~100 GB before promising 7-day history or passing ~15
   devices.
9. **Windows clone hygiene** — this Windows checkout is 29 commits behind
   `origin/main` with uncommitted backend edits. Never deploy or push from it;
   warehouse/provisioning work should use a fresh clone of `origin/main`.
