# Thickness Monitoring System — Merged App

This is the single unified thickness monitoring application for Rajdeep Analytics.
It runs in two sensor modes (Side-by-Side and Opposite) from one backend and one React frontend.

---

## How Changes Work — The Golden Rule

| What you change | How it goes live |
|-----------------|-----------------|
| **Frontend** (`src/`, `index.html`, `vite.config.js`, `.env.production`) | `git push` to GitHub → Vercel auto-deploys. Nothing else needed. |
| **Backend** (`backend/*.py`) | Run `python deploy.py` from this machine. It SSHes into the KVM, uploads files, and restarts the Flask service. |
| **pi_client** (`backend/pi_client.py`) | Run `python deploy.py` — it also SSHes into this Ubuntu PC and restarts `pi-merged-client.service`. |

---

## Architecture

```
CD22 Sensors (LAN 192.168.1.x)
        │  TCP binary protocol port 8234
        ▼
Ubuntu PC — 192.168.5.13  (YOU ARE HERE — development machine)
  ~/merged-version/          ← the repo, work here
  systemd: pi-merged-client  ← runs backend/pi_client.py, always-on
        │
        │  POST /ingest/readings   API key: merged-secret-2026
        │  GET  /config/poll       (picks up sensor write commands)
        ▼
KVM Cloud Server — 194.164.148.145
  Flask backend (merged_server.py)  port 5002   CLOUD_MODE=true
  nginx                             port 8082   HTTP reverse proxy → :5002
  Traefik (Docker)                  port 443    HTTPS → :5002
  PostgreSQL                        port 5432   database: sensor_db
        ▲
        │  HTTPS + WebSocket   wss://194-164-148-145.sslip.io
        ▼
Vercel — https://[app].vercel.app
  Built from GitHub main branch automatically
  VITE_SERVER_URL = https://194-164-148-145.sslip.io  (in .env.production)
```

---

## Repository

**GitHub:** `https://github.com/software-rajdeep/thickness-monitoring-merged`
**Branch:** `main`

```bash
cd ~/merged-version
git pull origin main          # get latest (may need VPN if router blocks GitHub)
git add -p                    # stage changes
git commit -m "your message"
git push origin main          # triggers Vercel redeploy automatically
```

> If `git pull` fails with a redirect/SSL error, the router is intercepting GitHub.
> Workaround: `git config --global http.sslVerify false` then try again,
> or hotspot from a phone and pull then.

---

## KVM Cloud Server — Full Details

### Access

| | |
|-|---|
| **IP** | `194.164.148.145` |
| **SSH user** | `root` |
| **SSH password** | `Federer7roger@` |
| **HTTP (LAN/direct)** | `http://194.164.148.145:8082` |
| **HTTPS (public/Vercel)** | `https://194-164-148-145.sslip.io` |

```bash
ssh root@194.164.148.145   # password: Federer7roger@
```

### Flask Backend Service

| | |
|-|---|
| **systemd unit** | `merged.service` |
| **source on KVM** | `/opt/merged/backend/` |
| **built frontend on KVM** | `/opt/merged/dist/` |
| **Python venv** | `/opt/merged/venv/` |
| **runs as** | `www-data` |
| **port** | `5002` |
| **env: CLOUD_MODE** | `true` |
| **env: INGEST_API_KEY** | `merged-secret-2026` |
| **env: SERVER_PORT** | `5002` |

```bash
# On KVM:
systemctl status merged          # check status
systemctl restart merged         # restart
journalctl -u merged -f          # live logs
journalctl -u merged -n 50       # last 50 lines
ss -tlnp | grep 5002             # confirm port is listening
```

### Database (PostgreSQL on KVM)

| | |
|-|---|
| **host** | `localhost` (on KVM) |
| **database** | `sensor_db` |
| **user** | `rapl` |
| **password** | `rapl2026` |

```bash
# On KVM:
psql -U rapl -d sensor_db
# then: \dt   to list tables
```

Tables:
- `sensor_filtered_readings` — SBS mode, trimmed thickness (sensor_a, sensor_b, sensor_c)
- `sensor_unfiltered_readings` — SBS mode, raw distances
- `opposite_thickness_readings` — Opposite mode, filtered (sensor_a, sensor_b, thickness)
- `opposite_thickness_raw_readings` — Opposite mode, raw
- `users` — login accounts

### nginx on KVM

| | |
|-|---|
| **config file** | `/etc/nginx/sites-available/merged` |
| **port** | `8082` |
| **proxies to** | `http://127.0.0.1:5002` |

```bash
# On KVM:
nginx -t                          # test config
systemctl reload nginx            # apply config changes
```

Config is uploaded by `deploy.py` from `nginx_merged.conf` in this repo.

### Traefik (Docker) on KVM — handles HTTPS

Traefik owns ports 80 and 443. It automatically gets a Let's Encrypt certificate
for `194-164-148-145.sslip.io` and forwards HTTPS traffic to the Flask backend.

| | |
|-|---|
| **config file on KVM** | `/root/traefik-conf/merged.yml` |
| **docker-compose** | `/root/docker-compose.yml` |
| **routes** | `https://194-164-148-145.sslip.io` → `http://host.docker.internal:5002` |

```yaml
# /root/traefik-conf/merged.yml  (already configured — do not delete)
http:
  routers:
    merged-backend:
      rule: "Host(\"194-164-148-145.sslip.io\")"
      entryPoints: [websecure]
      tls:
        certResolver: mytlschallenge
      service: merged-backend
  services:
    merged-backend:
      loadBalancer:
        servers:
          - url: "http://host.docker.internal:5002"
```

> **Do not add HTTPS to nginx_merged.conf.** Traefik handles all HTTPS. nginx only needs port 8082.

### Files Deployed to KVM

`deploy.py` uploads these files to `/opt/merged/backend/` on the KVM:
- `merged_server.py`
- `user_routes.py`
- `download_routes.py`
- `email_alert_routes.py`
- `sensor_config.json`
- `sensor_network.json`

And uploads the built frontend `dist/` to `/opt/merged/dist/`.
And uploads `thickness-monitor.service` to `/etc/systemd/system/merged.service`.

---

## Ubuntu PC — pi_client Setup

### This Machine

| | |
|-|---|
| **IP** | `192.168.5.13` |
| **SSH user** | `linux` |
| **SSH password** | `linux` |
| **sudo password** | `linux` |

### pi_client Service

| | |
|-|---|
| **systemd unit** | `pi-merged-client.service` |
| **script** | `/home/linux/merged-client/pi_client.py` |
| **sensor config** | `/home/linux/merged-client/sensor_network.json` |
| **posts to** | `http://194.164.148.145:8082/ingest/readings` |
| **API key** | `merged-secret-2026` |
| **rate** | 5 Hz |

```bash
sudo systemctl status pi-merged-client    # check
sudo systemctl restart pi-merged-client   # restart
sudo journalctl -u pi-merged-client -f    # live logs
```

---

## Vercel Frontend — Full Details

| | |
|-|---|
| **Repo** | `github.com/software-rajdeep/thickness-monitoring-merged` |
| **Branch** | `main` |
| **Build command** | `npm run build` |
| **Output dir** | `dist` |
| **Backend URL (baked in)** | `https://194-164-148-145.sslip.io` (from `.env.production`) |

**Vercel redeploys automatically on every push to `main`.**
No manual steps needed for frontend changes — just `git push`.

The frontend connects to the backend at `https://194-164-148-145.sslip.io` for all API calls and WebSocket. This is set in `.env.production` as `VITE_SERVER_URL` and baked into the bundle at build time by Vite.

---

## Project Structure

```
~/merged-version/
├── backend/
│   ├── merged_server.py       # Flask backend (all API + WebSocket)
│   ├── user_routes.py         # User CRUD (superadmin only)
│   ├── download_routes.py     # CSV export routes
│   ├── email_alert_routes.py  # Email alert config + Gmail OAuth
│   ├── pi_client.py           # This machine's sensor reader service
│   ├── pi_merged.service      # systemd unit for pi_client
│   ├── requirements.txt       # pip deps for backend
│   ├── sensor_config.json     # Sensor hardware settings
│   └── sensor_network.json    # Sensor IP/port map
├── src/                       # React frontend source
│   ├── App.jsx                # Root: mode selection, socket, page routing
│   ├── constants/config.js    # SERVER URL resolution
│   ├── pages/                 # Side-by-side mode pages
│   └── pages/opposite/        # Opposite mode pages
├── .env.production            # VITE_SERVER_URL=https://194-164-148-145.sslip.io
├── vite.config.js             # Dev proxy: all API paths → localhost:5002
├── deploy.py                  # Deploy backend to KVM + pi_client to this machine
├── nginx_merged.conf          # nginx config for KVM (port 8082 only)
├── thickness-monitor.service  # systemd unit for Flask on KVM
└── CLAUDE.md                  # This file
```

---

## Dev Workflow on This Machine

### Frontend development

```bash
cd ~/merged-version

# Start dev server — opens at http://localhost:5173
# All API calls proxy to localhost:5002 (vite.config.js)
npm run dev

# To test against the live KVM backend instead of local:
# In browser localStorage: thicknessmon.server = https://194-164-148-145.sslip.io
```

### Backend development (run locally)

```bash
cd ~/merged-version/backend
pip install -r requirements.txt

# Run without CLOUD_MODE (connects directly to sensors on LAN)
python3 merged_server.py

# Run in CLOUD_MODE (receives data from pi_client, no direct sensor connection)
CLOUD_MODE=true SERVER_PORT=5002 python3 merged_server.py
```

PostgreSQL must be running locally with `sensor_db` / `rapl` / `rapl2026`.

### Deploy frontend to Vercel

```bash
cd ~/merged-version
git add src/           # or whatever changed
git commit -m "..."
git push origin main   # Vercel picks this up in ~60 seconds
```

### Deploy backend to KVM

```bash
cd ~/merged-version
npm run build          # build frontend first (deploy.py uploads dist/)
python3 deploy.py      # SSH into KVM + upload + restart service
```

`deploy.py` will also re-deploy `pi_client.py` to this machine and restart `pi-merged-client.service`.

---

## Sensor Modes

| Mode | Sensors | What's measured |
|------|---------|----------------|
| **Side-by-Side (SBS)** | A, B, C from same side | Per-sensor displacement → thickness per sensor |
| **Opposite** | A + B facing each other | `gap − (35 + dist_A) − (35 + dist_B)` = object thickness |

Mode is chosen by the user on the frontend start screen and is not a server setting.
The backend detects mode from how many sensors are in `sensor_network.json` (2 = Opposite, 3 = SBS).

Default sensor IPs (configurable from the Backend page in the app):
- Sensor A: `192.168.1.7:8234`
- Sensor B: `192.168.1.8:8234`
- Sensor C: `192.168.1.9:8234`

---

## User Roles & Default Credentials

| Username | Password | Role |
|----------|----------|------|
| superadmin | superadmin123 | Full access incl. user management + email alerts |
| admin | admin123 | All pages except user management |
| supervisor | super123 | Dashboard, Run Mode, Download |
| worker | worker123 | Dashboard only |

---

## Troubleshooting

**Vercel frontend has no backend / API calls failing**
→ Check `systemctl status merged` on KVM. Check `journalctl -u merged -n 30` for errors.
→ Confirm port 5002 is listening: `ss -tlnp | grep 5002` on KVM.
→ Test directly: `curl https://194-164-148-145.sslip.io/sensors/status`

**pi_client not posting / sensors not showing live on dashboard**
→ `sudo systemctl status pi-merged-client` on this machine.
→ `sudo journalctl -u pi-merged-client -f` to watch live.
→ Confirm sensors are powered and on LAN 192.168.1.x.

**git pull blocked by router**
→ `git config --global http.sslVerify false` then retry.
→ Or use phone hotspot temporarily to pull/push.

**Backend crash on KVM after deploy**
→ `journalctl -u merged -n 30` — look for ImportError or missing module.
→ Most likely a missing file in the `backend_files` list in `deploy.py`.
