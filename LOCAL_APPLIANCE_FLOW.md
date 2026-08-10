# Local Appliance — Warehouse → Customer Runbook (Offline Product)

The fully-offline counterpart of `WAREHOUSE_TO_CUSTOMER_FLOW.md`. For customers
who will NOT connect anything to the internet: sensors + a router + one
appliance box running the `thickness-local` app. Everything — sensor reading,
database, dashboard, logins, CSV export — lives on that one box on their LAN.

**The customer never receives Python code.** They receive a `.deb` installer
(a single compiled binary) and a printed activation card.

| Piece | Where |
|---|---|
| App source (Linux) | `backend/local_main.py` (entry), `backend/local_db.py` (SQLite), `backend/local_license.py` (activation) |
| App source (Windows) | `backend/local_gui.py` (windowed entry → wraps `local_main`) |
| Build script (Linux) | `local/build-local-deb.sh` → `thickness-local_<ver>_<arch>.deb` |
| Build spec (Windows) | `local/thickness-local.spec` → `local/dist/thickness-local.exe` |
| License tool (CLI) | `tools/local_license_tool.py` (warehouse only) |
| License app (GUI) | `tools/license_app.py` / `tools/dist/license-studio.exe` (warehouse only) |
| Signing key + registry | `~/thickness-license-keys/` on the warehouse machine — **NOT in git** |
| Public key (ships in app) | `backend/license_pubkey.txt` (committed) |

---

## Windows appliance (`thickness-local.exe`)

The Windows build is the same offline server as the `.deb`, packaged as a
**no-terminal desktop app** for customers who run the appliance on a Windows box.

- **Entry point:** `backend/local_gui.py` (the `.deb` still uses
  `backend/local_main.py`; the two never interfere). The spec is built
  `console=False`, so **no terminal window ever appears** — all server output
  goes to `%ProgramData%\ThicknessLocal\thickness-local.log`.
- **On double-click it:** starts the server, waits for it to come up, opens the
  dashboard in the default browser (`http://localhost:5002`), and drops a
  **system-tray icon** with: *Open dashboard*, the shareable **LAN link**
  (`http://<this-pc-ip>:5002`), *Copy network link*, *Enable network sharing*,
  and *Quit*.
- **LAN sharing:** the server already binds `0.0.0.0`, so any device on the same
  router can open the LAN link and watch live readings (they are really talking
  to this box, which owns the sensors). The **inbound Windows Firewall rule**
  needs admin once — click **tray → "Enable network sharing"** and accept the
  single UAC prompt. After that, everyone on the router can view it.
- **Build (on Windows, with the repo's build venv):**
  ```bash
  cd local && ../local/.build-venv-win/Scripts/pyinstaller --noconfirm thickness-local.spec
  # → local/dist/thickness-local.exe
  ```
  (Run `npx vite build --mode localapp` first if the frontend changed — the spec
  bundles `../dist`.)
- Licensing/activation is identical to the `.deb`: open the dashboard, read the
  machine code off the activation page, issue a card, paste it in.

## License app (`license-studio.exe`) — internal

`tools/license_app.py` is a no-terminal desktop GUI (the counterpart of item #1
for our own team) that generates the **activation card we hand each customer** —
company, machine code, mode, term → signs the license and shows a printable card
with the license code + dashboard login, ready to Copy / Save / Print. It shares
the exact signing logic with the CLI via `local_license_tool.issue()`, so both
produce identical cards and registry entries.

- **Runs on OUR machine only** — it reads the private signing key from
  `~/thickness-license-keys` (default) at runtime; the key is **never** embedded
  in the exe.
- **Build:** `cd tools && ../local/.build-venv-win/Scripts/pyinstaller --noconfirm license_app.spec` → `tools/dist/license-studio.exe`.

---

## How licensing works (30-second version)

1. Install the `.deb` on the appliance box → it serves an **activation page**
   at `http://<box-ip>/` showing a **machine code** (derived from the box's
   `/etc/machine-id`).
2. Warehouse runs `local_license_tool.py issue "Customer" --machine <code>` →
   it signs a license with our **Ed25519 private key** and prints the
   **activation card** (license code + dashboard login).
3. Paste the code into the activation page. The app verifies the signature
   with the **public key baked into the binary** — no internet, ever.

The license carries: customer name, sensor mode (opposite/sbs), the machine
code it is locked to, optional expiry date, and the hash of the initial admin
password. Activating seeds the company + admin login + device locally.

**Revocation reality:** an offline box can never be switched off remotely.
The business lever is **expiry** — sell subscriptions as time-limited licenses
(`--days 365`) and give a renewal code on payment; sell outright as perpetual
(no `--days`). A renewal is just a new code pasted at `http://<box-ip>/license`.

**Key custody (critical):** back up `~/thickness-license-keys/` (private key +
`registry.json`). Losing it = you cannot issue/renew any license. Leaking it =
anyone can mint licenses. Never commit it; keep a copy in the password
manager / on the KVM.

---

## 1. WAREHOUSE — build the installer (once per release)

On the Ubuntu dev PC (or any Linux box matching the target arch):

```bash
cd ~/merged-version
git pull origin main
./local/build-local-deb.sh 1.0.0        # → local/thickness-local_1.0.0_amd64.deb
```

Prereqs on the build machine: `nodejs/npm`, `python3-venv`, `dpkg-dev`.
The script builds the frontend in same-origin mode, compiles everything with
PyInstaller into one binary, and wraps it in a `.deb` with a systemd service.

> The build overwrites `dist/` with the local-mode frontend. Run a plain
> `npm run build` again before any **cloud** deploy (`deploy.py`).

---

## 2. WAREHOUSE — bench procedure (per kit, ~30 min)

### Kit contents
- 1 × router (same model every kit; LAN `192.168.1.0/24`, DHCP `.50–.150`,
  sensors static at `.200/.201(/.202)`) — **WAN port stays empty at the
  customer: this kit needs no internet**
- 2 × CD22 sensors (3 for SBS) + brackets + cables
- 1 × appliance box (mini-PC, Ubuntu/Debian, amd64)
- Printed **Activation Card** + quick-start sheet

### Steps
1. **Router + sensors** — identical to the cloud kit (see
   `WAREHOUSE_TO_CUSTOMER_FLOW.md` §1 steps 1–2): static sensor IPs
   `192.168.1.200/.201(/.202)`, port 8234, label sensors A/B/C.
2. **Appliance box** — connect to the router LAN, give it a DHCP reservation
   (e.g. `192.168.1.10`), then:
   ```bash
   sudo dpkg -i thickness-local_1.0.0_amd64.deb
   ```
   Service starts automatically and prints the URL.
3. **Read the machine code** — open `http://192.168.1.10/` from any laptop on
   the router LAN. The activation page shows `XXXX-XXXX-XXXX-XXXX`.
4. **Issue the license** (on the warehouse machine that has the key dir):
   ```bash
   python3 tools/local_license_tool.py issue "Acme Steel" \
       --mode opposite --machine XXXX-XXXX-XXXX-XXXX \
       --days 365 --out cards/          # omit --days for a perpetual license
   ```
   Print the card **twice** (one in the box, one filed with the sale).
   The registry (`registry.json`) records every license automatically.
5. **Activate on the bench** — paste the license code into the activation
   page → "Licensed to Acme Steel".
6. **Bench test (go/no-go):**
   - Log in at `http://192.168.1.10/` with the card's Company/Username/Password.
   - Dashboard shows live values; move a test piece → values change.
   - Run one calibration from Run Mode → thickness reads correctly.
   - Download a CSV.
   - Power-cycle the whole kit → everything comes back by itself.
   All green = pack it. **Note: no internet is attached at any point.**

---

## 3. CUSTOMER — install steps (on the quick-start sheet)

1. Mount the sensors on the line, plug them into the router LAN ports.
2. Plug in and power on the appliance box (any LAN port).
3. From any PC/phone on the same router network, open
   `http://192.168.1.10/` (the address written on the card).
4. Log in with the Company / Username / Password on the card.
5. Their admin adds team logins from the Backend page. Done — no internet,
   no wizard, no IPs to type (the warehouse pre-activated everything).

Self-install variant (customer supplies their own box): send the `.deb` + the
quick-start; they install, read the machine code off the activation page,
phone/WhatsApp it to us, we send back the license code from the tool.

---

## 4. US — ongoing ops

| Situation | What to do |
|---|---|
| **Renewal (subscription)** | `issue` a new code with fresh `--days` for the same machine code; customer pastes it at `http://<box-ip>/license`. Existing users, data and calibration are untouched. |
| **License expired** | The app blocks with a renewal screen (shows machine code) — data is NOT deleted; it resumes on renewal. |
| **Lost card / password** | Log in as the service account (below) and reset their admin password from the Backend page; or issue a fresh license code (re-activation re-creates a missing admin user, never overwrites an existing one). |
| **Box replaced / motherboard swap** | Machine code changes → issue a new license for the new code (registry note the old one as dead). |
| **Support login** | Every install seeds a global service account: username `superadmin`, company blank, password in `/var/lib/thickness-local/service_login.txt` on the box (root-only). Read it over SSH/TeamViewer during support. |
| **Where is everything on the box** | Binary `/opt/thickness-local/`, data + SQLite DB + license `/var/lib/thickness-local/`, env `/etc/thickness-local/env`, logs `journalctl -u thickness-local`. |
| **Backup at customer** | Copy `/var/lib/thickness-local/` (contains DB, license, calibration). Restoring it onto the same box restores everything. |
| **Audit trail** | `python3 tools/local_license_tool.py list` — every license ever issued. |

### Troubleshooting on site
```bash
systemctl status thickness-local          # should be active
journalctl -u thickness-local -n 30       # startup + sensor log
# Sensors offline? From the box:
ping 192.168.1.200 && ping 192.168.1.201
# Port 80 taken by something else? Edit /etc/thickness-local/env → SERVER_PORT=8080
sudo systemctl restart thickness-local
```

---

## 5. Dry run (do this before the first real customer)

1. Build the `.deb` (§1) and install it on any spare Linux box/VM.
2. Open `http://<box>/`, note the machine code.
3. `python3 tools/local_license_tool.py issue "Dry Run Co" --machine <code> --days 30 --note "internal test"`
4. Activate, log in, feed data (real sensors, or POST synthetic readings to
   `http://<box>/ingest/readings` from the box itself:
   `curl -X POST localhost/ingest/readings -H 'Content-Type: application/json' -d '{"sensor_A":12.0,"sensor_B":8.5}'`).
5. Calibrate, watch live dashboard, export CSV, add a user, reboot the box,
   confirm everything persists.

---

## 6. Design notes (for future maintainers)

- **One codebase.** The appliance runs the same `merged_server.py` as the
  cloud. `local_main.py` sets `LOCAL_MODE=true` and installs
  `local_db.py` (SQLite) under the name `psycopg2` before anything imports —
  zero forked server logic. Cloud behaviour is untouched (everything is
  gated on `LOCAL_MODE`, which the KVM never sets).
- **Sensor reading** uses the server's existing direct-poll path (the same
  CD22 TCP protocol as `pi_client.py`) — no separate agent process.
- **The frontend is identical** to the cloud dashboard; it is built with
  `--mode localapp` so API calls go to the serving box (same origin).
- **License verification** is Ed25519 (`cryptography` lib) over the exact
  payload bytes; machine binding via `/etc/machine-id` hash. `--any-machine`
  licenses exist for loaner/spare boxes.
- **Multi-station customers:** one appliance per line (each gets its own
  license), or point extra browsers at the same box — the box handles many
  viewers.
