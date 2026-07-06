# How the Thickness Monitoring System Works — Creator's Manual

Everything you need to know as the owner of this product: what every piece
does, why it's built that way, the exact data flow, the security model, and
where to extend it. Written against the code as of July 2026 (branch
`golive-lockdown`, which includes the security lockdown).

---

## 1. The product in one paragraph

Factories need to measure the thickness of material moving on a line,
continuously, without touching it. Two laser distance sensors (Optex CD22)
face the material; our software turns their distance readings into a live
thickness number, streams it to a cloud dashboard in real time (5×/second),
alerts when it drifts out of tolerance, and stores history for CSV export.
Each customer installs one small "agent" program in their plant; everything
else — processing, storage, dashboards, users — runs on our cloud and is
fully isolated per customer.

**The 30-second sales pitch:** "Install our small box-side app, point it at
your sensors, and within five minutes your line's thickness is live on a
private web dashboard — with calibration, tolerance alerts, history, and CSV
export. No servers to run, no software to maintain, add as many viewers as
you like."

---

## 2. The big picture

```
CUSTOMER PLANT                          OUR CLOUD                       ANY BROWSER
──────────────                          ─────────                       ───────────
CD22 laser sensors                      KVM server 194.164.148.145      Vercel-hosted React app
 (192.168.1.200/201,                     ├─ Traefik   :443  HTTPS+CORS   https://merged-version.vercel.app
  TCP port 8234,                         ├─ nginx     :8082 → :5002
  binary protocol)                       ├─ Flask     :5002 (merged.service)
      │                                  │   merged_server.py
      │ reads 5×/sec                     │   = ALL logic
      ▼                                  ├─ PostgreSQL :5432  sensor_db
Thickness Agent                          │
 (thickness-agent.deb,                   │
  systemd service,        HTTPS POST     │            WebSocket (socket.io)
  compiled binary)  ────────────────────►│◄──────────────────────────────  browser
                    /ingest/readings     │   live readings, room-per-device
                    + X-Device-Id        │
                    + X-Device-Key       │
```

**One reading's journey (happens 5 times every second):**

1. The **agent** opens TCP connections to each CD22 sensor and reads the
   current distance (mm).
2. It POSTs `{sensor_A: <dist>, sensor_B: <dist>}` to
   `https://<server>/ingest/readings` with two headers: `X-Device-Id` and
   `X-Device-Key` — its identity.
3. The **server** looks the device up in the `devices` table, verifies the
   key against the stored hash, and rejects anything invalid (401) or revoked.
4. It pushes the raw values into that device's own rolling window (last 10
   samples) and computes the **filtered** value (rolling average) — this
   removes sensor noise.
5. Using that device's own saved **calibration**, it computes thickness
   (see §6 for the math).
6. It writes two DB rows tagged with the `device_id` — one raw, one filtered —
   and trims that device's history to its row cap.
7. It emits the reading over the WebSocket **into a room named after the
   device_id** — so only browsers viewing that device receive it.
8. A browser that logged in as that customer, joined that room, sees the
   number move ~200 ms after the laser measured it.

---

## 3. Component by component

### 3.1 CD22 sensors
Optex CD22 laser displacement sensors. Wired Ethernet, TCP port 8234, binary
register protocol (read distance, read/write settings like averaging). They
sit on the customer's LAN; only the agent talks to them. We never expose them
to the internet.

### 3.2 The Thickness Agent (what the customer installs)
- **Source:** `agent/thickness_agent.py` (~470 lines, stdlib + `requests` only).
- **Shipped as:** a compiled single binary inside a Debian package —
  `thickness-agent_<version>_<arch>.deb`, built by `agent/build-deb.sh`
  (PyInstaller → dpkg-deb). The customer never sees Python or source code.
- **Install:** `sudo dpkg -i thickness-agent_*.deb` → registers + starts a
  systemd service `thickness-agent`, auto-starts on boot, auto-restarts on crash.
- **First-run setup:** serves a tiny local web wizard at `http://localhost:7000`.
  The customer enters exactly two things: the **activation key** (device id +
  key from the card we print) and their **sensor IPs**. Everything else
  (company name, mode, labels) comes FROM the server during activation —
  the key IS the identity, so the customer can't misconfigure who they are.
- **Headless install** (for us / advanced customers): write
  `/etc/thickness-agent/agent.env` with `SERVER_URL`, `DEVICE_ID`,
  `DEVICE_KEY`, `SENSORS="A=192.168.1.200,B=192.168.1.201"` and restart the
  service — no wizard needed.
- **Activation call:** `POST /agent/activate {device_id, device_key}` →
  server validates and returns customer name, sensor mode, labels. The agent
  displays "Acme Steel — Opposite, 2 sensors" so the installer knows it worked.
- **SIMULATE=1** env var makes the agent emit synthetic readings — for demos
  and testing without hardware.
- **Network note:** some plant LANs hijack DNS for `sslip.io`; the fallback
  server URL `http://194.164.148.145:8082` is printed on every key card.
  (A real domain — Phase 2 of the go-live plan — eliminates this.)

### 3.3 The cloud server (the heart)
- **Machine:** KVM at 194.164.148.145 (Ubuntu, 48 GB disk).
- **Process:** `merged_server.py`, a single Flask + Flask-SocketIO app,
  ~2,000 lines, run by systemd unit `merged.service` as `www-data`, port 5002,
  with `CLOUD_MODE=true`. ALL server logic lives in this one file plus three
  helpers (`download_routes.py` CSV exports, `email_alert_routes.py` Gmail
  alerts, `user_routes.py` retired-legacy).
- **Layering, outside → in:**
  - **Traefik** (Docker) owns :443 — TLS termination AND the only CORS
    authority (allowed origins list in `/root/traefik-conf/merged.yml`,
    hot-reloads on save). Never add Flask-CORS — double headers break browsers.
  - **nginx** :8082 — plain HTTP reverse proxy to Flask (used by agents whose
    LANs break HTTPS/DNS).
  - **Flask** :5002 — never exposed directly.
- **Per-device state:** the server keeps an in-memory dict keyed by
  `device_id`: rolling filter windows, latest reading, calibration.
  Calibration is also persisted to `devices.calibration` (JSONB) so it
  survives restarts. This is what makes one server serve N customers with
  zero cross-talk.
- **Legacy path:** requests without device headers (the original single-tenant
  install) still work and are stored as device `dev_legacy` — that's your own
  original rig, nothing customer-facing.

### 3.4 The database (PostgreSQL `sensor_db`)
| Table | What it holds |
|---|---|
| `customers` | id, unique company name |
| `devices` | device_id (PK, `dev_<8 hex>`), customer_id, **device_key_hash** (never plaintext), sensor_mode, label, revoked flag, last_seen, calibration JSONB |
| `users` | id, username, email, password_hash, role, customer_id (NULL ⇒ global Rajdeep account) |
| `sensor_unfiltered_readings` | SBS raw: timestamp, sensor_a/b/c, device_id |
| `sensor_filtered_readings` | SBS filtered (rolling avg of last 10) |
| `opposite_thickness_raw_readings` | Opposite raw distances + thickness |
| `opposite_thickness_readings` | Opposite filtered + thickness |

- Every reading row carries `device_id`; composite indexes
  `(device_id, timestamp)` keep per-customer queries fast.
- **Retention:** after each insert the server trims that device to
  `PER_DEVICE_ROW_CAP` rows (env var; 850,000 ≈ 2 days at 5 Hz; default in
  code is 3M ≈ 7 days). Per-device, so one customer's volume can never evict
  another customer's history.
- **Backups:** nightly `pg_dump` to `/root/db_backups`, 7 kept (installed at
  go-live).

### 3.5 The dashboard (frontend)
- React + Vite, source in `src/`, **auto-deployed by Vercel on every push to
  `main`** (~90 s). The backend URL is baked in at build time from
  `.env.production` (`VITE_SERVER_URL`).
- Two page sets: `src/pages/` (side-by-side mode) and `src/pages/opposite/`
  (opposite mode). Mode is chosen on the start screen.
- Login = **company + username + password** → `POST /auth/login` → a signed
  token stored in localStorage, sent as `Authorization: Bearer <token>` on
  every API call (helper: `src/constants/auth.js`).
- After login the app fetches `/auth/devices` (only that customer's devices),
  shows a **device picker** if there's more than one, joins that device's
  WebSocket room, and scopes every history/CSV/calibration call with
  `?device_id=`.
- Pages: Dashboard (live values + graph), Run Mode (calibrate, tolerance
  limits), Download (CSV export), Backend (user management, DB stats — admin
  only). Role-based page access is enforced in the UI *and* on the server.

---

## 4. Multi-tenancy — how customers stay isolated

Three identity layers, all independent:

1. **Machine identity (agents):** each installed agent has a `device_id` +
   secret `device_key`. The server stores only a **hash** of the key (same
   scheme as passwords). Stolen database ⇒ no usable keys. Every ingest POST
   is verified; `revoked=true` kills an install instantly (support/billing
   lever).
2. **Human identity (dashboard):** users belong to a customer
   (`users.customer_id`). Login issues a **signed token** (itsdangerous,
   `AUTH_SECRET` env, 7-day expiry) embedding `{user id, customer id, role}`.
   Tokens are tamper-proof — the customer id inside is what every endpoint
   trusts.
3. **Data tagging:** every reading row, socket room, calibration blob and CSV
   is keyed by `device_id`, and devices belong to customers.

**THE invariant (memorize this):** *"sees everything" is decided by
`customer_id IS NULL`, never by role name.* A customer's own superadmin has
role `superadmin` but a non-NULL customer_id → sees only their company. Your
global Rajdeep account has customer_id NULL → sees all. The helper
`_is_global(auth)` implements this; every list/create/delete endpoint uses it.
If you ever add an endpoint, use it too — checking `role == 'superadmin'`
would leak all customers to every company admin.

**Roles** (within a company): `superadmin` → `admin` → `supervisor` →
`worker`, mapping to page access (user management / config / download /
dashboard-only).

**What's protected how (after the lockdown):**
- Public: `/auth/login`, `/sensors/status`, `/server/config`, `/agent/activate`
  (has its own key check), the static frontend, `/email-alerts/oauth-callback`
  (Google redirects there).
- Device-key gated: `/ingest/readings`.
- Admin-token gated (header `X-Admin-Token` = `PROVISION_ADMIN_TOKEN` env):
  `/provision`.
- Login-token gated: everything else — `/thickness/*`, `/download/*`,
  `/config/*`, `/stream/*`, `/db/status`, `/auth/users*`, `/auth/devices`,
  `/email-alerts/*`. Cross-tenant `device_id` in any of these → 403/empty.

---

## 5. Provisioning — what happens when you sell

```
python3 tools/onboard_customer.py "Acme Steel" --mode opposite --devices 1
```
calls `POST /provision`, which atomically:
1. Creates the customer row (or finds it).
2. Mints `dev_<random>` + a random 24-char key, stores only the hash.
3. If it's the customer's **first** device, also creates their company
   superadmin login (username `admin`, random password).
4. Returns everything **once** — the script prints it as a key card:

```
  Company     : Acme Steel
  Device ID   : dev_a1b2c3d4        ← agent wizard field 1
  Device Key  : xxxxxxxxxxxxxxxxxx  ← agent wizard field 2 (shown ONCE)
  DASHBOARD   : https://merged-version.vercel.app
  Company/Username/Password : Acme Steel / admin / <random>
```

Lost key = revoke the device, provision a new one (keys are unrecoverable by
design). More lines/machines = more devices under the same customer
(`--devices N`). More logins = their admin creates them in the dashboard's
Backend page.

---

## 6. The measurement itself (the domain logic)

### Opposite mode (2 sensors facing each other across the material)
The sensors are mounted a fixed **gap** apart. Each measures the distance to
its side of the material. `ZERO_OFFSET_MM = 35.0` is the CD22's reference
offset (the sensor reports distance relative to the middle of its range):

```
thickness = gap − (35 + dist_A) − (35 + dist_B)
```

Two ways to establish the gap:
- **Set gap** directly (`/thickness/gap`) if the mechanical gap is known.
- **Auto-gap** (`/thickness/auto-gap`): put a piece of KNOWN thickness in the
  line, tell the server that number — it back-solves the gap. This is the
  normal calibration a customer does.
- **Baseline fallback:** plain "Calibrate" captures baseline distances with a
  reference piece; thickness = reference + (baseline_A − current_A) +
  (baseline_B − current_B). Used when no gap is set.

### Side-by-side mode (up to 3 sensors on the same side)
Each sensor measures displacement of the material surface vs a calibrated
zero → per-sensor thickness. Used for wide lines where you want profile
across the width (sensors A, B, C).

### Filtering
Raw readings are noisy (vibration, surface texture). The server keeps the
last **10 samples per sensor per device** and stores/streams their rolling
average alongside the raw value. Raw goes to the `*_raw`/unfiltered tables
(debugging), filtered to the main tables (display/analysis). The CD22 also
has hardware averaging registers configurable via `/config/write`.

### Tolerance limits
Per-installation min/max (`/thickness/limit`); the dashboard colors
out-of-range values and the email-alert module (Gmail OAuth,
`email_alert_routes.py`) can send threshold alerts.

---

## 7. Deploys & operations (how you change things)

| You changed | Do this |
|---|---|
| Frontend (`src/`) | push to `main` → Vercel live in ~90 s. Nothing else. |
| Backend (`backend/*.py`) | copy to KVM `/opt/merged/backend/` + `systemctl restart merged` (deploy.py automates; always deploy ALL files in one shot — a half-upload once truncated files to 0 bytes) |
| Agent | rebuild `.deb` (`agent/build-deb.sh`), customers reinstall (auto-update is a Phase-3 item) |
| CORS origins | edit `/root/traefik-conf/merged.yml` on KVM — hot reload |
| Retention | `PER_DEVICE_ROW_CAP` in `/etc/systemd/system/merged.service.d/retention.conf` + restart |

**Golden rules learned the hard way:**
- All git work from a **fresh clone of `origin/main`** — the Windows clone and
  the Ubuntu checkout have both been stale/divergent before, and a concurrent
  session once overwrote main (recovered). `git fetch` before trusting anything.
- The KVM is production. Back up the DB/files before schema or auth changes
  (`/root/db_backups`, `.bak-*` files exist for every major change).
- Logs: `journalctl -u merged -f` (KVM), `journalctl -u thickness-agent -f`
  (customer box).

**Secrets inventory (keep in a password manager, rotate if leaked):**
KVM root password · DB password (`rapl`/`rapl2026`) · `PROVISION_ADMIN_TOKEN`
(merged.service env — mints customers!) · `AUTH_SECRET` (signs all login
tokens — rotating it logs everyone out) · global superadmin login ·
`INGEST_API_KEY` (legacy path only) · GitHub repo credentials · Gmail OAuth
tokens (`email_alert_config.json`, gitignored, lives only on disk).

---

## 8. What to tell people (FAQ ammunition)

- **"Where is my data?"** On our cloud server, tagged with your private device
  ID; no other customer can query, stream, or export it — enforced
  cryptographically (signed tokens) on every request, not just hidden in the UI.
- **"What if internet drops?"** The agent keeps trying; readings during the
  outage are lost (local buffering is a roadmap item). The dashboard shows
  the line as offline rather than showing stale numbers as live.
- **"What do we need to provide?"** An always-on Linux PC or Raspberry Pi on
  the same network as the sensors, with outbound internet. Install is one
  file and a 10-minute wizard.
- **"Can we get the raw data?"** Yes — CSV export of raw and filtered series,
  any time, from the dashboard.
- **"How accurate?"** That's the CD22's spec (µm-class) plus our 10-sample
  noise filter; accuracy is calibrated against your own reference piece.
- **"How fast do we see problems?"** Readings update 5×/second on the
  dashboard; email alerts fire on tolerance breach.

---

## 9. Where to extend (future you)

| Want to add | Touch |
|---|---|
| New sensor model | agent only — add a reader class; server is sensor-agnostic (it just gets numbers) |
| Provisioning UI | new superadmin page calling `/provision` + a devices table page; endpoint already exists |
| Long history / trends | rollup job: aggregate 5 Hz rows older than N days into 1/min tables; read endpoints pick table by time range |
| Billing | `customers` table is the anchor; `devices.revoked` is the enforcement lever; add plan/paid_until columns |
| Alerts beyond email | `check_thresholds_and_alert()` in email_alert_routes.py is the hook — add SMS/webhook senders there |
| Mobile app | it's all HTTP+WebSocket with token auth — any client can implement the same calls (see ANDROID_APP_SPEC.md) |
| Another sensor mode | add mode string at provisioning, a processing branch in the ingest path, and a page set in `src/pages/<mode>/` |

The single most important design decision to preserve as you extend:
**everything is keyed by `device_id`, and "global" means `customer_id IS
NULL`.** Keep those two rules and tenancy stays airtight no matter what you
bolt on.
