# Thickness Monitoring System — Technical Reference

The definitive engineering reference for the Rajdeep Analytics thickness
monitoring system. This document describes every component of the codebase —
the cloud (SaaS) product, the offline local appliance (the Windows
`thickness-local.exe` and the Linux `thickness-local` `.deb`), the
customer-facing agent, the license infrastructure, and the React frontend —
from the wire protocol up to the build recipes.

Companion documents (read these too):

| Doc | What it is |
|---|---|
| `CLAUDE.md` | Operational memory: machines, services, deploy steps, troubleshooting |
| `HOW_IT_WORKS.md` | Narrative "creator's manual" for the cloud product |
| `LOCAL_APPLIANCE_FLOW.md` | Warehouse → customer runbook for the offline appliance |
| `WAREHOUSE_TO_CUSTOMER_FLOW.md` | Warehouse → customer runbook for the cloud product |
| `ONBOARDING.md` / `CUSTOMER_FLOW.md` / `CUSTOMER_QUICKSTART.md` | Customer-facing install guides |
| `GO_LIVE_PLAN.md` / `PHASE1_PLAN.md` / `PRODUCTION_APP_SPEC.md` | Planning / go-live notes |

---

## 1. What the product is

Factories measure the thickness of material moving on a line, continuously and
contactlessly. Two or three Optex **CD22 laser displacement sensors** face the
material; this software turns their distance readings (mm, 0.01 mm resolution)
into a live **thickness** number, streams it to a dashboard in real time at
**5 Hz**, alerts on tolerance breaches, and stores history for CSV export.

There are **two delivery models**, both produced from this one codebase:

1. **Cloud (SaaS)** — a small agent binary on the customer's plant network reads
   the sensors and streams readings to Rajdeep's cloud (KVM). The dashboard is a
   Vercel-hosted React app. Multi-tenant: one server serves many customers with
   zero cross-talk.
2. **Local appliance (offline)** — a self-contained box runs *everything*
   (sensor reading, SQLite database, dashboard, logins, CSV export) on the
   customer's LAN with **no internet**. Ships as a Windows `.exe`
   (`thickness-local.exe`) or a Linux `.deb` (`thickness-local`). Licensing is
   enforced offline via Ed25519-signed activation codes.

---

## 2. Repository map

```
merged/
├── backend/                      # All server code (cloud + local share it)
│   ├── merged_server.py          #   ~2450 lines. Flask app: all routes, CD22
│   │                             #   protocol, stream loop, auth, multi-tenancy,
│   │                             #   WebSocket. Runs on cloud AND appliance.
│   ├── local_main.py             #   Local entry point (Linux .deb / service):
│   │                             #   sets LOCAL_MODE, installs SQLite shim, secret
│   ├── local_gui.py              #   Windows desktop launcher (tray + browser)
│   ├── local_db.py               #   SQLite drop-in impersonating psycopg2
│   ├── local_license.py          #   Ed25519 offline license gate + activation page
│   ├── pi_client.py              #   In-house sensor reader (pi-merged-client svc)
│   ├── user_routes.py            #   DEAD CODE (legacy /users CRUD, unregistered)
│   ├── download_routes.py        #   CSV export + DB row-count status
│   ├── email_alert_routes.py     #   Gmail OAuth + SMTP alerts, summary reports
│   ├── sensor_config.json        #   CD22 hardware register defaults
│   ├── sensor_network.json       #   Sensor IP/port map {A, B[, C]}
│   ├── license_pubkey.txt        #   Ed25519 public key shipped in the apps
│   └── requirements.txt          #   Cloud deps (Flask, SocketIO, psycopg2)
├── src/                          # React 19 + Vite frontend (no TS)
│   ├── App.jsx                   #   Root: mode selection, socket init, page state
│   ├── pages/                    #   Side-by-Side mode pages
│   ├── pages/opposite/           #   Opposite mode pages
│   ├── layout/ (+ layout/opposite/)   #   Topbar/Sidebar per mode
│   ├── components/               #   Shared (ModeSelection, EmailAlertSettings…)
│   └── constants/                #   config.js / config_opposite.js / auth / roles
├── agent/                        # Customer-facing "Thickness Agent" (cloud product)
│   ├── thickness_agent.py        #   ~470 lines. Setup wizard + 5 Hz uploader
│   ├── build-deb.sh / build.sh / build.ps1
│   ├── packaging/DEBIAN/         #   .deb control/postinst/prerm
│   └── thickness-agent.service
├── local/                        # Offline appliance packaging
│   ├── thickness-local.spec      #   PyInstaller spec → thickness-local.exe (Windows)
│   ├── build-local-deb.sh        #   → thickness-local_<v>_<arch>.deb (Linux)
│   ├── requirements-local.txt    #   Local deps (NO psycopg2 — SQLite shim)
│   ├── packaging/DEBIAN/         #   .deb control/postinst/prerm
│   └── dist/thickness-local.exe  #   Built Windows exe (~25 MB)
├── tools/                        # Warehouse-only tooling (never shipped)
│   ├── local_license_tool.py     #   CLI: init keys, issue/renew licenses
│   ├── license_app.py + .spec    #   "Thickness License Studio" GUI → license-studio.exe
│   ├── onboard_customer.py       #   Cloud provisioning key-card printer
│   └── dist/license-studio.exe   #   Built GUI (~16 MB)
├── electron/                     # Raspberry-Pi kiosk shell for the cloud dashboard
├── public/                       # Static assets (logos, icons, sensor_setup.html)
├── deploy.py                     # KVM backend + Ubuntu pi_client deploy
├── nginx_merged.conf             # KVM nginx (8082 → 5002 only)
├── thickness-monitor.service     # systemd unit template → merged.service
├── .env.production               # VITE_SERVER_URL=https://194-164-148-145.sslip.io
└── .env.localapp                 # VITE_SERVER_URL= (same-origin for appliance)
```

---

## 3. Architecture

### 3.1 Cloud (SaaS) topology

```
CUSTOMER PLANT                      OUR CLOUD                       ANY BROWSER
──────────────                      ─────────                       ───────────
CD22 laser sensors                  KVM server 194.164.148.145      Vercel React app
 (192.168.1.200/201,                 ├─ Traefik :443  HTTPS + CORS  https://merged-version.vercel.app
  TCP :8234,                         ├─ nginx   :8082 → :5002
  binary protocol)                   ├─ Flask   :5002 (merged.service)
     │                               │   merged_server.py (CLOUD_MODE=true)
     │  reads 5 Hz                   ├─ PostgreSQL :5432  sensor_db
     ▼                               │
Thickness Agent                      │          WebSocket (socket.io)
 (thickness-agent binary)  HTTPS ───►│◄─────────── room-per-device
 /ingest/readings + X-Device-Id/Key  │
```

Also in the data path for Rajdeep's own rig: the **Ubuntu PC** (`192.168.5.13`)
runs `pi_client.py` as the `pi-merged-client` service, which reads the same CD22
sensors over the wired LAN and POSTs to the same `/ingest/readings` endpoint
using the legacy shared API key.

### 3.2 Local appliance topology (no internet)

```
CUSTOMER LAN (isolated router, e.g. 192.168.1.0/24)
├─ CD22 Sensor A   192.168.1.200:8234
├─ CD22 Sensor B   192.168.1.201:8234
└─ Appliance box (thickness-local)
    ├─ Windows: thickness-local.exe  → local_gui.py → Flask :5002
    ├─ Linux:   thickness-local.deb  → local_main.py → Flask :80 (service)
    ├─ SQLite:  <data_dir>/thickness_local.db
    └─ Dashboard served same-origin (VITE_SERVER_URL empty)
Any PC/phone on the LAN → http://<box-ip>:<port>/  (or :80)
```

No port 443, no Traefik, no CORS (same-origin), no psycopg2 (SQLite shim), and
a license gate in front of everything until an Ed25519 code is pasted in.

### 3.3 The shared backend core

Both products run the **identical** `merged_server.py`. The only differences are
injected at runtime by the entry point:

| | Cloud | Local appliance |
|---|---|---|
| `CLOUD_MODE` | `true` (KVM) | `false` |
| `LOCAL_MODE` | never set | `true` |
| Data source | pi_client/agent HTTP `POST /ingest/readings` | server polls CD22 directly (`_stream_ingest_loop_local`) |
| Database | PostgreSQL `sensor_db` | SQLite via `local_db` shim (installed as `psycopg2` in `sys.modules`) |
| Sensor writes | queued; pi_client drains `/config/poll` | executed inline on the socket |
| License gate | none | `local_license.register_license(...)` before every request |
| Port | `5002` | `5002` (Windows exe) / `80` (.deb env) |
| Data dir | repo dir / env | `C:\ProgramData\ThicknessLocal` / `/var/lib/thickness-local` |

---

## 4. The built executables

### 4.1 `thickness-local.exe` — the Windows offline appliance (~25 MB)

- **Entry point:** `backend/local_gui.py` (PyInstaller spec `local/thickness-local.spec`,
  `console=False`, **onefile**, `upx=True`). No terminal window appears.
- **What it contains:** the bundled Python runtime, the whole backend, the
  SQLite shim, the license module, the Ed25519 public key
  (`license_pubkey.txt`), and the **built React frontend** (`../dist` bundled
  as `dist/` inside the archive — resolved at runtime via
  `sys._MEIPASS`).
- **Behaviour on double-click** (`local_gui.py`):
  1. Redirects all output to `%ProgramData%\ThicknessLocal\thickness-local.log`
     (a windowed exe has `sys.stdout is None`).
  2. Re-imports `local_main` (env defaults, data dir, per-install `AUTH_SECRET`,
     SQLite shim).
  3. Acquires the **single-instance mutex** `Global\ThicknessLocalSingleInstance`
     — two processes would corrupt the CD22 request/response framing. A second
     launch just re-opens the dashboard.
  4. Starts Flask/SocketIO in a daemon thread on `0.0.0.0:5002`, opens the
     default browser at `http://localhost:5002`, and shows a **system-tray
     icon** (pystray + PIL, caliper glyph drawn in code) with: Open dashboard,
     the LAN link `http://<this-pc-ip>:5002`, Copy network link, Enable network
     sharing (single UAC prompt → `netsh advfirewall` inbound rule), Quit.
  5. **Auto-shuts-down when the last dashboard tab closes** — via SocketIO
     connect/disconnect tracking with an 8 s grace period. Gives
     "double-click to start, close the tab to stop" UX.
- **Build:**
  ```bash
  cd local && ../local/.build-venv-win/Scripts/pyinstaller --noconfirm thickness-local.spec
  # → local/dist/thickness-local.exe   (run `npx vite build --mode localapp` first if src/ changed)
  ```

### 4.2 `thickness-local` `.deb` — the Linux offline appliance

- **Entry point:** `backend/local_main.py` (console service, runs forever).
- Built by `local/build-local-deb.sh [version]` → `thickness-local_<v>_<arch>.deb`.
  Must run on the target architecture (PyInstaller cannot cross-compile).
- **postinst** writes `/etc/thickness-local/env` (once): `LOCAL_MODE=true`,
  `SERVER_PORT=80`, `THICKNESS_DATA_DIR=/var/lib/thickness-local`, and a random
  per-box `AUTH_SECRET`; then `enable --now thickness-local.service`.
- **Data:** binary `/opt/thickness-local/`, data+DB+license
  `/var/lib/thickness-local/`, logs `journalctl -u thickness-local`.

### 4.3 `license-studio.exe` — the "Thickness License Studio" (~16 MB, internal)

- `tools/license_app.py` + `tools/license_app.spec`. A **tkinter** desktop GUI
  (no console) for the warehouse to generate activation cards. Two tabs:
  **Generate Card** (signing-key folder, company, sensor mode, machine code or
  "Any machine", perpetual vs `--days` term, admin user/pass, note → renders a
  printable card → Copy / Save / Print) and **Registry** (every license issued).
- It calls the *same* `local_license_tool.issue()` as the CLI, so GUI and CLI
  produce byte-identical cards and registry entries.
- **The Ed25519 private key is read from `~/thickness-license-keys/` at
  runtime and is NEVER embedded in the exe.** `collect_all('cryptography')`
  bundles the signing library only.
- **Build:** `cd tools && ../local/.build-venv-win/Scripts/pyinstaller --noconfirm license_app.spec`

### 4.4 `thickness-agent` binary (cloud product, customer side)

- `agent/thickness_agent.py` compiled with PyInstaller (`.deb` via
  `agent/build-deb.sh`, or bare binary via `build.sh`/`build.ps1`). Runs as the
  `thickness-agent` service; auto-starts on install; serves the setup wizard on
  `http://localhost:7000`. See §9.

---

## 5. CD22 sensor protocol (wire-level)

Both `pi_client.py`, `agent/thickness_agent.py`, and the local appliance talk
the same binary protocol to Optex CD22 sensors over TCP port `8234`.

**Framing** — 6-byte frames:

```
STX(1) opcode(1) data_hi(1) data_lo(1) ETX(1) BCC(1)
STX = 0x02  ETX = 0x03
BCC = opcode XOR data_hi XOR data_lo      (STX/ETX excluded)
```

**Read distance** (the measurement command):

```
bytes([0x02, 0x43, 0xB0, 0x01, 0x03, 0x43 ^ 0xB0 ^ 0x01])
```

opcode `0x43`, register `0xB001`. Response is read as `[STX, 0x06, hi, lo, ...]`.

**Decode:** `raw = (resp[2] << 8) | resp[3]`; if `raw > 32767`, `raw -= 65536`
(signed 16-bit two's complement); distance **mm** = `raw * 0.01`
(i.e. each unit = 10 µm).

**Register write** (`pi_client.py` / server `sensor.write_register`): the CD22
requires a **read-then-write** sequence — send `[STX, 0x52, addr_hi, addr_lo, ETX, bcc]`,
drain the response, then send `[STX, 0x57, val_hi, val_lo, ETX, bcc]`; success =
response byte `0x06` (ACK). `0x52`/`0x57` are ASCII `'R'`/`'W'`.

**Robustness notes (learned the hard way):**
- The server's `CD22Sensor.transact` does exact-length reads with a deadline and
  **drains stale bytes** to stay frame-synchronized; connect timeout is 0.3 s and
  reconnect backoff 3 s so a phantom sensor never stalls the poll loop.
- The agent keeps a **persistent socket per sensor** and requires `len==6` +
  ACK byte; `pi_client.py` uses a stateless connect→read→close per sample with
  no ACK check (reads up to 16 bytes). Both work; the agent is stricter.
- **Single-client only:** CD22 has no multi-client support. Two readers
  interleave bytes and desync framing — hence the local exe's single-instance
  mutex, and never run two processes against the same sensor.

---

## 6. Backend deep dive (`backend/merged_server.py`)

### 6.1 Runtime modes and paths

- `CLOUD_MODE` / `LOCAL_MODE` env flags gate behaviour (see §3.3).
- `main()` (line ~2439) does: init config/network/thickness-state/limit files →
  `init_db()` → `start_background_tasks()` → `socketio.run(...)` on
  `SERVER_PORT` (default 5002). The stream loop dispatches to
  `_stream_ingest_loop_local` (direct sensor poll, 5 Hz trimmed-mean) when
  `LOCAL_MODE and not CLOUD_MODE`, otherwise `stream_ingest_loop` (ingest-driven).

### 6.2 Complete route inventory

**Unauthenticated / public:**

| Method | Path | Purpose |
|---|---|---|
| POST | `/login` | Legacy login — validates only, no token (superseded by `/auth/login`) |
| POST | `/auth/login` | Company + username/password → signed token |
| GET | `/server/config` | Server config; `?mode=opposite` filters sensor configs to A/B |
| GET | `/sensors/status` | Per-sensor freshness (online/offline) |
| POST | `/ingest/readings` `/ingest/data` | Sensor data from pi_client/agent; device-key OR API-key OR localhost |
| GET | `/config/poll` | pi_client drains queued sensor write commands (every 3 s) |
| POST | `/config/result` | pi_client reports write result |
| POST | `/provision` | Mint customer + device + admin (header `X-Admin-Token`) |
| POST | `/agent/activate` | Agent wizard: device key → customer/mode/sensor labels |
| GET | `/license/status`, `/license`, POST `/license/activate` | Local license gate (LOCAL_MODE only) |

**Authenticated (any role):**

| Method | Path | Purpose |
|---|---|---|
| GET | `/auth/me` | Token payload |
| GET | `/auth/devices` | Devices visible to the caller |
| GET/POST | `/thickness/state`, `/thickness/limit` | Calibration state / global thickness limit |
| POST | `/thickness/setup-ready`, `/thickness/calibration`, `/thickness/calibration/reset` | Calibration flow |
| POST | `/thickness/gap`, `/thickness/auto-gap` | Opposite-mode gap setup |
| POST | `/download/filtered`, `/download/raw`, GET `/download/thickness`, `/download/thickness/raw` | CSV exports |
| GET | `/db/status` | Table row counts |

**Admin-only:**

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/config/network` | Read/write `sensor_network.json` |
| GET/POST | `/config/file` | Read/write `sensor_config.json` |
| GET/POST | `/config/read`, `/config/write` | Direct CD22 register read/write |
| POST | `/stream/trim`, `/stream/config` | Trim % and stream rate |
| GET/POST | `/auth/users` … `/auth/users/<uid>/password` | User management |
| * | `/email-alerts/*` | Email config, OAuth, thresholds, trigger (gate: admin/superadmin) |

**Frontend:** `GET /` and `GET /<path>` serve the built React bundle (SPA
fallback to `index.html`).

### 6.3 WebSocket (SocketIO, `async_mode='threading'`)

- **Client → server:** `join_device {device_id}` — joins the per-device room.
- **Server → client:**
  - `sensor_reading` → `{timestamp, distance_A, distance_B, distance_C,
    thickness, device_id}` — emitted from the single stream thread to each
    per-device room (and to the legacy `dev_legacy` room) at 5 Hz.
  - `sensor_status` → `{online}` — ~1 Hz (immediate on transitions).
- `cors_allowed_origins='*'` on the SocketIO call is intentional and separate
  from HTTP CORS (Traefik owns HTTP CORS; Flask-CORS must NOT be re-added).

### 6.4 Thickness math

**Opposite mode (sensors A+B facing each other across the material):**
```
thickness = gap_distance − (ZERO_OFFSET_MM + dist_A) − (ZERO_OFFSET_MM + dist_B)
ZERO_OFFSET_MM = 35.0   (CD22 reference offset)
```
Requires `gap_distance > 0`. Two ways to set the gap:
- `/thickness/gap` — mechanical gap entered directly.
- `/thickness/auto-gap` — operator puts a piece of *known* thickness in the
  line and tells the server; the server back-solves the gap.
- **Baseline fallback** (`_compute_thickness`): when no gap is set but
  calibration is active, `thickness = reference_thickness + (baseline_A − a) +
  (baseline_B − b)`.

**Side-by-Side mode (sensors A,B,C on the same side):** per-sensor displacement
vs a calibrated zero; thickness is surfaced as raw `distance_A/B/C`. The backend
always computes thickness from **A and B only** — sensor C populates the `C`
columns but never participates in thickness.

**Filtering:** rolling average of the last **10 samples** per sensor (FILTER_WINDOW)
for the "filtered" tables/stream; raw goes to `*_raw` tables. The local
fast-poll loop uses a **trimmed mean** (drops `trim_pct` from each end of the
batch) at 5 Hz.

### 6.5 Calibration state

Global state dict persisted to `thickness_state.json` (legacy) or the
`devices.calibration` column (multi-tenant). Key fields: `setup_ready`,
`reference_readings`, `calibration_completed`, `calibration_active`,
`calibration_reference_thickness`, `calibration_baseline_readings {A,B}`,
`gap_distance`, `auto_gap_active`, `object_thickness`,
`thickness_tolerance_min/max`.

Flow: **setup-ready** (capture reference) → **calibration** (user supplies a
reference thickness; captures baselines, sets `calibration_active`) → **gap** or
**auto-gap** (establishes `gap_distance`; without it thickness shows "—" unless
the baseline fallback applies). **reset** wipes calibration but keeps
setup/reference.

### 6.6 Threading & state

- One daemon **stream thread** (ingest loop) + one email thread.
- All `sensor_reading`/`sensor_status` emissions come from the stream thread so
  the 5 Hz cadence is smooth regardless of HTTP worker load.
- Global module-level dicts: `thickness_state`, `thickness_limit` (JSON-backed,
  shared across users — a limit change affects everyone, intentional),
  `last_ingest_reading` freshness, `device_state` (per-device windows,
  thickness, latest), `_device_cache` (30 s TTL), `active_sensors_map`.
- **Per-device row caps** `PER_DEVICE_ROW_CAP = 3,000,000` (~7 days at 5 Hz)
  and global table caps (10M filtered/thickness, 1M raw). Trimming runs every
  10k inserts (legacy loop) / 2k inserts (per-device).

### 6.7 Email alerts

`email_alert_routes.py` (823 lines): Gmail OAuth (`gmail.send` scope) or SMTP
(gmail/outlook/yahoo/sendgrid/custom). Background thread flushes a queue every
30 s and sends daily/weekly summary reports. Threshold breaches fire
`threshold_out_of_tolerance`. **Gotcha:** `SUMMARY_ONLY_MODE = True` currently
suppresses ALL event-driven alerts — only summaries and test emails go out.

---

## 7. Databases

### 7.1 Cloud — PostgreSQL `sensor_db` (`rapl`/`rapl2026` @ localhost)

| Table | Columns |
|---|---|
| `customers` | `id`, `name` (unique), `created_at` |
| `devices` | `id`, `device_id` (unique, `dev_<8hex>`), `customer_id`, `device_key_hash`, `sensor_mode` (`opposite`/`sbs`), `label`, `revoked`, `last_seen`, `calibration`, `created_at` |
| `users` | `id`, `username`, `email`, `password_hash`, `role`, `customer_id` (NULL = global Rajdeep account), `created_at` |
| `user_calibrations` | `id`, `username`, `calibration_json`, `updated_at` — created but unused |
| `sensor_filtered_readings` | `id`, `timestamp`, `sensor_a`, `sensor_b`, `sensor_c`, `device_id` |
| `sensor_unfiltered_readings` | same |
| `opposite_thickness_readings` | `id`, `timestamp`, `sensor_a`, `sensor_b`, `thickness`, `device_id` |
| `opposite_thickness_raw_readings` | same |

Indexes on `timestamp DESC` for all four reading tables.

### 7.2 Local — SQLite `thickness_local.db`

`local_db.py` is a **psycopg2-compatible shim** installed into `sys.modules` as
`psycopg2`/`psycopg2.extras` before the app imports, so zero server code changes
are needed. It translates: `SERIAL PRIMARY KEY`→`INTEGER PRIMARY KEY
AUTOINCREMENT`, `TIMESTAMPTZ`→`TIMESTAMP`, `DOUBLE PRECISION`→`REAL`,
`JSONB`→`TEXT`, `NOW()`→`datetime('now','localtime')`, `IS NOT DISTINCT
FROM`→`IS`, `%s`→`?`, and no-ops sequences/`information_schema` probes. The
schema is the **full multi-tenant superset** (device_id on readings, email/
customer_id on users). DB file:
`<THICKNESS_DATA_DIR>/thickness_local.db` with `WAL`, `busy_timeout=5000`.

### 7.3 Known schema drift (maintenance warning)

`init_db()` in `merged_server.py` creates the tables with the *legacy* names
(`sensor_A_distance`, `computed_thickness`) and does **not** create
`customers`/`devices`/`device_id`/`email`/`customer_id`. The live cloud DB and
the SQLite shim use the *current* lowercase names with the extra columns — the
cloud DB was migrated by hand. A fresh `init_db()`-only DB would fail the
INSERTs (errors are printed, not raised). **When rebuilding a DB, apply the
current schema from `local_db.py` or the running KVM, not from `init_db()`.**

---

## 8. Authentication & multi-tenancy

- **Logins:** `POST /auth/login` (company + username + password) → signed
  token via `itsdangerous.URLSafeTimedSerializer(AUTH_SECRET, salt="auth-token")`,
  7-day TTL, payload `{uid, cid, role}`. **Stateless** — rotating `AUTH_SECRET`
  logs everyone out.
- **Roles:** `superadmin` > `admin` > `supervisor` > `worker`. The `require_auth`
  decorator enforces them; `superadmin` always passes.
- **THE invariant:** *"sees everything" is decided by `customer_id IS NULL`,
  never by role name.* A customer's own superadmin has a non-NULL `customer_id`
  and sees only their company; the global Rajdeep account (`customer_id NULL`)
  sees all. Helper `_is_global(auth)` — use it in every new endpoint.
- **Ingest auth:** multi-tenant via `X-Device-Id` + `X-Device-Key` (verified
  against `device_key_hash`, 30 s cache, `revoked` kills instantly); legacy via
  Bearer `INGEST_API_KEY`; localhost exempt.
- **Provisioning:** `/provision` requires `X-Admin-Token`
  (`PROVISION_ADMIN_TOKEN` env). Returns device_id/device_key/admin password
  **once** — only hashes are stored.
- **Devices & isolation:** every list/detail/export endpoint filters by the
  caller's `cid`; cross-tenant `device_id` → 403/empty. Socket rooms are per-device.

---

## 9. The agent (`agent/thickness_agent.py`)

Customer-installed reader for the cloud product (~470 lines, stdlib + `requests`).

- **Three jobs:** (1) setup wizard at `http://localhost:7000` (enter activation
  code → server returns company + mode; then sensor IPs + test), (2) read CD22
  at 5 Hz (persistent sockets, ACK-verified, 30 s/5 s reconnect backoff),
  (3) upload to `/ingest/readings` with `X-Device-Id`/`X-Device-Key`.
- **Headless:** `/etc/thickness-agent/agent.env` with `DEVICE_ID`, `DEVICE_KEY`,
  `SENSORS="A=192.168.1.200,B=192.168.1.201"`, `SERVER_URL`.
- **`SIMULATE=1`** emits synthetic readings (`20.0 + 2.0*sin(...)`) for demos.
- **No sensor-write capability** (unlike pi_client).
- Config locations: `/etc/thickness-agent/config.json` (Linux),
  `%ProgramData%\ThicknessAgent\config.json` (Windows),
  `~/Library/Application Support/ThicknessAgent/config.json` (macOS).
- Ships as `thickness-agent_<v>_<arch>.deb` (`agent/build-deb.sh` — must run on
  target arch) or a bare binary (`build.sh`/`build.ps1`, NSSM for Windows service).

`pi_client.py` is the in-house equivalent: stateless per-sample TCP, shared API
key, plus a 3 s `/config/poll` loop that executes remote register writes on the
sensors. Runs as `pi-merged-client` on the Ubuntu PC.

---

## 10. License system (offline activation)

**Flow:** install computes a **machine code** → warehouse signs a payload with
the Ed25519 private key → customer pastes the code into the activation page →
the app verifies with the public key bundled in the binary. No internet, ever.

- **Machine code:** `SHA256("rajdeep-thickness-local-v1" + "::" + machine_id)`,
  uppercase hex, first 16 chars, formatted `XXXX-XXXX-XXXX-XXXX`. Machine id
  from `/etc/machine-id` → `/var/lib/dbus/machine-id` → Windows `MachineGuid`
  (NOT `uuid.getnode()` — a synthetic MAC may not survive reboot).
- **Code format:** `THICK1.<b64url(payload)>.<b64url(signature)>`.
- **Payload:** `{v, license_id, customer, sensor_mode, machine_code ("*" =
  any-machine/loaner), issued_at, expires_at, admin_username,
  admin_password_hash}`. Password hash pinned to **`pbkdf2:sha256`** so an
  Android appliance could verify it natively (Werkzeug's scrypt is impractical
  there).
- **Enforcement:** payload `machine_code` must equal the box's code; `expires_at`
  blocks after that date (data is NOT deleted; resumes on renewal).
- **Keys:** private `~/thickness-license-keys/license_signing_key.hex` +
  `registry.json` — **back these up; never commit**. Public key
  `backend/license_pubkey.txt` (committed, ships in the apps).
- **CLI:** `tools/local_license_tool.py init | issue "Co" --machine XXXX --days N | list`.
- **Activation seeding:** activating creates the customer + company superadmin
  (from the payload) + `dev_legacy` device, and writes a default 2-sensor
  `sensor_network.json` only if none exists. Renewals never clobber users/data.
- **Service account:** every local install seeds a global `superadmin` with a
  random password written to `<data_dir>/service_login.txt` (root/0600) for
  Rajdeep support access.

---

## 11. Frontend (`src/`)

React 19 + Vite, **no TypeScript**, no router (page state in `App.jsx`), no
axios (native `fetch`), no chart library (hand-drawn `<canvas>` graphs).

- **Two parallel UI stacks:** `src/pages/` (Side-by-Side) and
  `src/pages/opposite/` (+ `layout/` vs `layout/opposite/`). Mode is chosen on
  the start screen (`ModeSelection`) and held **only in React state** — it does
  not survive a refresh.
- **`SERVER` resolution** (`constants/config.js`):
  `localStorage["thicknessmon.server"]` → `import.meta.env.VITE_SERVER_URL` →
  `window.location.origin`. Computed once at module load.
- **Sockets:** `io(SERVER, {transports:["polling","websocket"]})`; on connect
  emits `join_device {device_id}`. Live rows kept in a 100-row ring buffer that
  feeds Dashboard, Run Mode, and history tables. Stream rate derived from
  inter-reading deltas.
- **Auth:** token in `localStorage["thicknessmon.token"]`; `authHeaders()` adds
  the Bearer header. `user` state is **not** restored on refresh (back to login).
- **Roles:** nav filtered by `ROLE_ACCESS[role]`; every gated page also renders
  `<AccessDenied />`. `superadmin` all pages; `admin` all but backend; `supervisor`
  dashboard/run/download; `worker` dashboard/run.
- **Run Mode (SBS):** calibration dialog, global thickness limit (min/max,
  color-coded), per-sensor cards, canvas graphs with dashed limit lines.
- **Run Mode (Opposite):** gap/auto-gap dialog (auto-opens when not calibrated),
  auto-syncs tolerance limits into the global thickness limit, combined thickness
  graph, per-sensor distance cards (`calcDistance(read) = read + 35`).
- **SensorConfigPage:** CD22 hardware register config via `/config/write` (sampling
  period, averaging, polarity, alarm output), stream rate/trim, activity log.
- **EmailAlertSettings:** superadmin-only flyout — Gmail OAuth popup flow,
  thresholds, alert toggles, cooldown, daily/weekly summaries, test email.
- **Build modes:** `.env.production` (cloud, baked KVM URL) vs `.env.localapp`
  (empty → same-origin). ⚠️ Running the appliance build (`vite build --mode
  localapp`) overwrites `dist/` — re-run plain `npm run build` before any cloud
  deploy.

---

## 12. Build & deploy recipes

| Artifact | Command | Output |
|---|---|---|
| Cloud frontend | `npm run build` → `git push origin main` | Vercel auto-deploy (~60–90 s) |
| Cloud backend | `npm run build && python3 deploy.py` | KVM backend + Ubuntu pi_client |
| Windows appliance | `cd local && ../local/.build-venv-win/Scripts/pyinstaller --noconfirm thickness-local.spec` (after `npx vite build --mode localapp`) | `local/dist/thickness-local.exe` |
| Linux appliance | `./local/build-local-deb.sh 1.0.0` | `local/thickness-local_1.0.0_amd64.deb` |
| License Studio | `cd tools && ../local/.build-venv-win/Scripts/pyinstaller --noconfirm license_app.spec` | `tools/dist/license-studio.exe` |
| Agent .deb | `./agent/build-deb.sh` (on target arch) | `agent/thickness-agent_<v>_<arch>.deb` |
| Agent binary | `agent/build.sh` (Linux/mac) / `agent/build.ps1` (Win) | `dist/thickness-agent` |

**PyInstaller environment:** the Windows builds use the venv
`local/.build-venv-win` (Python 3.14) containing `pyinstaller`, `pystray`,
`Pillow`, `cryptography`, etc. (`local/requirements-local.txt` lists the runtime
deps; it deliberately has **no psycopg2**).

---

## 13. Environment variables

| Var | Cloud | Local | Purpose |
|---|---|---|---|
| `CLOUD_MODE` | `true` | `false` | Enable cloud ingest path |
| `LOCAL_MODE` | — | `true` | License gate + local poll loop |
| `SERVER_PORT` | `5002` | `5002` (exe) / `80` (.deb) | HTTP port |
| `THICKNESS_DATA_DIR` | — | `C:\ProgramData\ThicknessLocal` / `/var/lib/thickness-local` | Runtime data, DB, license |
| `AUTH_SECRET` | env (systemd unit) | generated per install (`auth_secret.txt`) | Token signing |
| `INGEST_API_KEY` | `merged-secret-2026` | — | Legacy ingest auth |
| `PROVISION_ADMIN_TOKEN` | `rajdeep-admin-2026` | — | `/provision` gate |
| `THICKNESS_LICENSE_KEYDIR` | — | `~/thickness-license-keys` | Warehouse signing key dir |
| `THICKNESS_LICENSE_PUBKEY` | — | hex override | Public key (else `license_pubkey.txt`) |
| `VITE_SERVER_URL` (build) | `https://194-164-148-145.sslip.io` | empty | Frontend API base |
| `FRONTEND_DIST` | `../dist` | `sys._MEIPASS/dist` | React bundle location |

---

## 14. Data flows (happy paths)

### 14.1 Cloud live reading (5×/sec)
1. Agent/pi_client reads CD22 → mm.
2. POSTs `{sensor_A, sensor_B}` + `X-Device-Id`/`X-Device-Key` to
   `/ingest/readings`.
3. Server verifies device → pushes into that device's 10-sample window →
   computes filtered value → computes thickness from calibration/gap.
4. Writes raw + filtered rows tagged `device_id`, trims to per-device cap.
5. Emits `sensor_reading` to the device's socket room; the subscribed browser
   updates ~200 ms after the laser measured.

### 14.2 Offline appliance boot
1. `.deb`/exe starts → `local_main` → SQLite shim installed → config files
   initialized → license gate armed.
2. Browser hits `http://<box>/` → license gate serves the activation page if no
   valid license (machine code shown).
3. Warehouse issues a code → pasted in → verified → tenant seeded → default
   `sensor_network.json` written → dashboard opens.
4. Local stream loop polls sensors at 5 Hz, batches, emits trimmed-mean filtered
   readings + SQLite rows.

---

## 15. Known issues, dead code & warnings

- **`user_routes.py` is dead** (never registered). `calculate_thickness`,
  `capture_*` helpers, `get_next_db_id` are unused.
- **Schema drift** between `init_db()` DDL and the real cloud/SQLite schema —
  see §7.3.
- **`SUMMARY_ONLY_MODE = True`** disables event-driven alert emails.
- **Hard-coded secrets in source:** Google OAuth client_secret
  (`email_alert_routes.py`), default `AUTH_SECRET`, `PROVISION_ADMIN_TOKEN`,
  DB creds, `INGEST_API_KEY`. All overridable by env for cloud; local generates
  its own secret.
- **CSV exports** build the whole result in memory (`fetchall()` → `StringIO`)
  with no date filtering — a multi-million-row export can exhaust KVM RAM.
- **Frontend duplication:** `config.js`/`config_opposite.js` are ~95% identical
  with separate `SENSOR_CONFIGS` exports (latent bug source); unused deps
  `axios`, `react-router-dom`; unused `Ic.jsx` icon set; `DEMO_ACCOUNTS`,
  `fetchWithTimeout`, and the RunMode `onToggle` prop are unused.
- **SPA catch-all** means an unknown API path returns HTML (index.html), not a
  JSON 404.
- **`pending_config_lock` is declared but never acquired** around
  `pending_config_commands` append/clear — benign in practice (GIL) but a real
  race in theory.
- **Always deploy all backend files in one shot** (deploy.py does this). A
  partial upload once truncated files to 0 bytes → ImportError loop. Never leave
  ad-hoc upload scripts in the repo root.
