# Thickness Monitoring System — Merged App

This is the single unified thickness monitoring application for Rajdeep Analytics. It runs in two modes (Side-by-Side and Opposite) from one backend and one React frontend.

---

## Architecture Overview

```
CD22 Sensors (LAN: 192.168.1.x)
        │
        │ TCP / custom binary protocol
        ▼
Ubuntu PC (192.168.5.13)          ← You are here (development machine)
  pi_client.py (systemd service)
        │
        │ HTTP POST /ingest/readings (API key: merged-secret-2026)
        ▼
KVM Cloud Server (194.164.148.145)
  Flask backend — port 5002 (CLOUD_MODE=true)
  nginx — port 8082 (HTTP, LAN access)
  Traefik — port 443 (HTTPS, routes 194-164-148-145.sslip.io → :5002)
  PostgreSQL — sensor_db
        ▲
        │ HTTPS WebSocket + REST API
        │
Vercel Frontend (https://[your-vercel-app].vercel.app)
  VITE_SERVER_URL = https://194-164-148-145.sslip.io
```

---

## Repository

**GitHub:** `https://github.com/software-rajdeep/thickness-monitoring-merged`

Clone:
```bash
git clone https://github.com/software-rajdeep/thickness-monitoring-merged.git
cd thickness-monitoring-merged
```

---

## Project Structure

```
thickness-monitoring-merged/
├── backend/
│   ├── merged_server.py       # Main Flask backend (all API routes + WebSocket)
│   ├── user_routes.py         # User CRUD routes (superadmin only)
│   ├── download_routes.py     # Legacy download routes
│   ├── email_alert_routes.py  # Email alert config + Gmail OAuth + SMTP
│   ├── pi_client.py           # Ubuntu sensor client (runs as systemd service)
│   ├── pi_merged.service      # systemd unit for pi_client on Ubuntu
│   ├── requirements.txt       # Python dependencies
│   ├── sensor_config.json     # Sensor hardware settings (generated on first run)
│   ├── sensor_network.json    # Sensor IP/port config (generated on first run)
│   └── cd22_server.py         # (legacy, not used by merged server)
├── src/
│   ├── App.jsx                # Root component — mode selection, socket, routing
│   ├── constants/config.js    # SERVER URL logic (env var or window.location)
│   ├── pages/                 # Side-by-side mode pages
│   │   ├── DashboardPage.jsx
│   │   ├── RunModePage.jsx    # Live streaming + calibration (SBS)
│   │   ├── SensorConfigPage.jsx
│   │   ├── DownloadPage.jsx
│   │   ├── BackendPage.jsx    # Server status, DB stats, network config
│   │   └── LoginPage.jsx
│   └── pages/opposite/        # Opposite mode pages (same structure)
├── deploy.py                  # SSH deploy script: KVM backend + Ubuntu pi_client
├── nginx_merged.conf          # nginx config for port 8082 on KVM
├── thickness-monitor.service  # systemd unit for Flask backend on KVM
├── vite.config.js             # Vite dev proxy → localhost:5002
├── .env.production            # VITE_SERVER_URL for Vercel build
└── package.json
```

---

## Frontend

**Stack:** React 19 + Vite + Socket.IO client

### Sensor Modes

The app starts with a mode selection screen. Once chosen, the user logs in and accesses:

| Mode | Sensors | Measures |
|------|---------|---------|
| **Side-by-Side (SBS)** | A, B, C (3 sensors, same side) | Per-sensor displacement → thickness |
| **Opposite** | A, B (facing each other) | `gap − dist_A − dist_B − 2×35mm` → object thickness |

Mode-specific pages live in `src/pages/` (SBS) and `src/pages/opposite/` (Opposite). The root `App.jsx` dynamically switches between them.

### Backend URL Resolution (`src/constants/config.js`)

```js
export const DEFAULT_SERVER =
  import.meta.env.VITE_SERVER_URL || window.location.origin;
```

- **Production (Vercel):** `VITE_SERVER_URL=https://194-164-148-145.sslip.io` (baked in at build time via `.env.production`)
- **Local dev (`npm run dev`):** `window.location.origin` = `http://localhost:5173`, but `vite.config.js` proxies all API paths to `http://localhost:5002`
- **Override at runtime:** The Backend page lets users save a custom URL to `localStorage` key `thicknessmon.server`

### Dev Commands

```bash
npm install          # Install dependencies
npm run dev          # Start dev server at http://localhost:5173 (proxies to :5002)
npm run build        # Build to dist/ (uses .env.production → sslip.io backend)
npm run preview      # Preview the production build locally
```

### Deploying Frontend to Vercel

Push to `main` — Vercel auto-deploys. The Vercel project uses:
- Build command: `npm run build`
- Output directory: `dist`
- No environment variables needed in Vercel dashboard (`.env.production` is committed)

---

## Backend (`backend/merged_server.py`)

**Stack:** Flask + Flask-SocketIO + psycopg2 + threading

### Key Config (environment variables / top of file)

| Variable | Default | Description |
|----------|---------|-------------|
| `SERVER_PORT` | `5002` | Flask listen port |
| `CLOUD_MODE` | `false` | `true` = skip direct sensor connections, receive data via `/ingest/readings` |
| `INGEST_API_KEY` | `merged-secret-2026` | API key for Pi client authentication |
| `DB_HOST` | `localhost` | PostgreSQL host |
| `DB_NAME` | `sensor_db` | Database name |
| `DB_USER` | `rapl` | DB user |
| `DB_PASS` | `rapl2026` | DB password |

### CLOUD_MODE

When `CLOUD_MODE=true` (KVM production):
- Flask does **not** try to TCP-connect to CD22 sensors
- Sensor data arrives via `POST /ingest/readings` from the Ubuntu pi_client
- The background stream task idles; `/ingest/readings` emits WebSocket events directly

When `CLOUD_MODE=false` (local dev with sensors physically connected):
- Flask connects to sensors via TCP on startup
- A background thread reads sensors and streams via WebSocket

### Database Tables (PostgreSQL `sensor_db`)

| Table | Mode | Columns |
|-------|------|---------|
| `sensor_filtered_readings` | SBS | `id, timestamp, sensor_a, sensor_b, sensor_c` (trimmed averages = thickness) |
| `sensor_unfiltered_readings` | SBS | `id, timestamp, sensor_a, sensor_b, sensor_c` (raw distance readings) |
| `opposite_thickness_readings` | Opposite | `id, timestamp, sensor_a, sensor_b, thickness` (filtered) |
| `opposite_thickness_raw_readings` | Opposite | `id, timestamp, sensor_a, sensor_b, thickness` (raw) |
| `users` | Both | `id, username, password_hash, role` |

Tables use circular-buffer IDs (max rows enforced by `LIMIT_*` constants). Default users are seeded on first run.

### Key API Routes

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/login` | Username + password → `{username, role}` |
| `GET` | `/thickness/state` | Current calibration state |
| `POST` | `/thickness/setup-ready` | Capture reference readings |
| `POST` | `/thickness/calibration` | Set calibration with reference thickness |
| `POST` | `/thickness/calibration/reset` | Reset calibration |
| `POST` | `/thickness/gap` | Set gap distance (Opposite mode) |
| `POST` | `/thickness/auto-gap` | Auto-calculate gap from known object |
| `GET` | `/sensors/status` | Which sensors are online |
| `GET` | `/server/network` | Current sensor IP/port config |
| `POST` | `/server/network` | Update sensor IP/port config |
| `GET` | `/config/file` | Read sensor_config.json |
| `POST` | `/config/file` | Update sensor_config.json |
| `POST` | `/config/write` | Write register to sensor (queued in CLOUD_MODE) |
| `POST` | `/ingest/readings` | Receive data from pi_client (CLOUD_MODE) |
| `GET` | `/config/poll` | pi_client polls for pending config writes |
| `POST` | `/config/result` | pi_client reports write result |
| `POST` | `/download/filtered` | CSV export (SBS filtered) |
| `POST` | `/download/raw` | CSV export (SBS raw) |
| `GET` | `/download/thickness` | CSV export (Opposite filtered) |
| `GET` | `/download/thickness/raw` | CSV export (Opposite raw) |
| `GET` | `/db/status` | Row counts for all tables |
| `GET/POST` | `/email-alerts/config` | Email alert settings |
| `POST` | `/email-alerts/test` | Send test email |
| WebSocket | `sensor_reading` event | Emitted at `target_rate_hz` with all sensor fields |

### WebSocket Payload (`sensor_reading` event)

```json
{
  "timestamp": "2026-06-16T10:00:00.000",
  "sensor_A": 12.34,   // SBS thickness for A
  "sensor_B": 11.22,   // SBS thickness for B
  "sensor_C": 10.55,   // SBS thickness for C
  "distance_A": 5.12,  // Raw distance from Sensor A (Opposite mode uses this)
  "distance_B": 4.88,  // Raw distance from Sensor B
  "distance_C": null,  // null if not present
  "thickness": 3.45    // Opposite mode computed thickness
}
```

Frontend reads `sensor_A/B/C` in SBS mode and `distance_A/B + thickness` in Opposite mode.

### Thickness Calculations

**SBS mode (per sensor):**
```
thickness = calibration_reference_thickness + (baseline_reading − current_reading)
```
Without calibration: `thickness = reference_reading − current_reading` (clamped ≥ 0)

**Opposite mode:**
```
ZERO_OFFSET = 35.0 mm  (sensor face to measurement zero)
actual_dist_A = ZERO_OFFSET + dist_A
actual_dist_B = ZERO_OFFSET + dist_B
thickness = gap_distance − actual_dist_A − actual_dist_B
```

### Running Backend Locally (with sensors)

```bash
cd backend
pip install -r requirements.txt
# Also install: pip install psycopg2-binary Flask Flask-Cors Flask-SocketIO Werkzeug
python merged_server.py
# Server starts on port 5002
```

PostgreSQL must be running with database `sensor_db`, user `rapl`, password `rapl2026`:
```sql
CREATE USER rapl WITH PASSWORD 'rapl2026';
CREATE DATABASE sensor_db OWNER rapl;
```

---

## Pi Client (`backend/pi_client.py`)

Runs on the Ubuntu PC. Reads CD22 sensors over TCP and POSTs to the backend.

### Configuration

| Env Variable | Default | Description |
|--------------|---------|-------------|
| `SERVER_URL` | `http://194.164.148.145:8082` | Backend URL to POST to |
| `API_KEY` | `merged-secret-2026` | Must match `INGEST_API_KEY` on server |
| `POST_RATE_HZ` | `5` | Readings per second |

Sensor IPs are read from `sensor_network.json` in the same directory. Default: A=192.168.1.7, B=192.168.1.8, C=192.168.1.9, all on port 8234.

### POST Payload to `/ingest/readings`

```json
{
  "timestamp": "2026-06-16T10:00:00.000",
  "sensor_A": 5.12,
  "sensor_B": 4.88,
  "sensor_C": null
}
```

Values are raw distance readings in mm. `null` means that sensor was unreachable this cycle.

The client also polls `GET /config/poll` every 3 seconds to receive pending sensor register write commands (queued when the Backend page sends a write in CLOUD_MODE), executes them on the physical sensors, and reports results to `POST /config/result`.

### Running pi_client Manually

```bash
cd /home/linux/merged-client
python3 pi_client.py
```

### Systemd Service (on Ubuntu)

Service file: `/etc/systemd/system/pi-merged-client.service`

```bash
sudo systemctl status pi-merged-client    # Check status
sudo systemctl restart pi-merged-client   # Restart
sudo systemctl stop pi-merged-client      # Stop
sudo journalctl -u pi-merged-client -f    # Live logs
```

---

## CD22 Sensor Protocol

Sensors communicate over TCP (default port 8234) using a 6-byte binary frame:
```
[STX=0x02] [CMD] [DATA_H] [DATA_L] [ETX=0x03] [BCC]
BCC = CMD ^ DATA_H ^ DATA_L
```

Read measurement command: `[0x02, 0x43, 0xB0, 0x01, 0x03, BCC]`

Response: `[0x02, 0x06, VAL_H, VAL_L, 0x03, BCC]`
- Raw value = `(VAL_H << 8) | VAL_L`; if > 32767, subtract 65536 (two's complement)
- Distance in mm = `raw * 0.01`

---

## Deployment (`deploy.py`)

SSH-based deploy script that:
1. Uploads backend files to KVM (`/opt/merged/backend/`)
2. Uploads built frontend dist to KVM (`/opt/merged/dist/`)
3. Sets up Python venv and installs dependencies
4. Installs and restarts `merged.service` (Flask backend systemd unit)
5. Uploads nginx config and reloads nginx
6. Uploads `pi_client.py` + service file to Ubuntu, restarts `pi-merged-client.service`

```bash
# Build frontend first
npm run build

# Then deploy everything
python deploy.py
```

**KVM credentials:** root @ 194.164.148.145, password: Federer7roger@
**Ubuntu credentials:** linux @ 192.168.5.13, password: linux

### Production Services on KVM

| Service | Port | Description |
|---------|------|-------------|
| `merged.service` (systemd) | 5002 | Flask backend (CLOUD_MODE=true) |
| nginx | 8082 | HTTP reverse proxy → :5002 (LAN access) |
| Traefik (Docker) | 80, 443 | HTTPS → :5002 via `194-164-148-145.sslip.io` |
| PostgreSQL | 5432 | Database (sensor_db) |

Traefik config: `/root/traefik-conf/merged.yml` on KVM.
The HTTPS endpoint `https://194-164-148-145.sslip.io` is what the Vercel frontend connects to.

---

## User Roles

| Role | Access |
|------|--------|
| `superadmin` | Everything including user management and email alerts |
| `admin` | All pages except user management |
| `supervisor` | Dashboard, Run Mode, Download |
| `worker` | Dashboard only |

Default credentials (seeded on first DB init):
- superadmin / superadmin123
- admin / admin123
- supervisor / super123
- worker / worker123

---

## Email Alerts (`email_alert_routes.py`)

Superadmin-only feature. Supports:
- Gmail OAuth (recommended) — client ID: `676970971720-mdce53i1i4agalvvrn72psnmnvvroer0.apps.googleusercontent.com`
- Gmail SMTP app password
- Outlook / Yahoo / SendGrid / Custom SMTP

Config stored at `backend/email_alert_config.json`. Gmail token at `backend/gmail_token.json`.

Alert types: `threshold_below_min`, `threshold_above_max`, `threshold_out_of_tolerance`, `sensor_disconnected`, `run_session_start`, `run_session_end`.

Cooldown prevents spam (default 5 min between same alert type). Background thread flushes queued grouped alerts every 30s.

---

## Common Tasks

### Run the full stack locally (Ubuntu PC with sensors)

```bash
# Terminal 1: Backend
cd backend
python3 merged_server.py
# Runs on :5002, connects to sensors at 192.168.1.x

# Terminal 2: Frontend
npm run dev
# Open http://localhost:5173
```

### Deploy to production (KVM + Vercel)

```bash
npm run build          # Build frontend (uses .env.production → sslip.io backend)
python deploy.py       # SSH push to KVM + Ubuntu
# Vercel auto-deploys from GitHub push
```

### Check backend is running on KVM

```bash
# From any machine with SSH access to KVM:
ssh root@194.164.148.145
systemctl status merged
journalctl -u merged -n 50
```

### Test the HTTPS endpoint

```bash
curl https://194-164-148-145.sslip.io/sensors/status
```

### Rebuild Vercel deployment

Push any commit to `main` on GitHub — Vercel picks it up automatically.
Or trigger manually via Vercel dashboard.

---

## Known Architecture Decisions

- **CLOUD_MODE:** The backend runs without any sensor hardware on the KVM. All sensor data flows through the pi_client POST endpoint. This means sensor writes from the Backend page are queued server-side and retrieved by pi_client on its next `/config/poll`.
- **Circular DB buffer:** Tables wrap around at their row limits instead of growing indefinitely. The oldest row is overwritten.
- **Dual mode from one server:** Mode (SBS vs Opposite) is not a server-side setting — it's determined by the frontend at runtime and the number of sensors in `sensor_network.json` (2 sensors = Opposite, 3 = SBS).
- **Traefik for HTTPS:** The KVM already runs Traefik for other services. Our Flask app is registered in `/root/traefik-conf/merged.yml` (not as a Docker container) using `host.docker.internal` to reach the host-side process.
