# Thickness Monitoring System — Merged App

Single unified thickness monitoring app for Rajdeep Analytics.
Two sensor modes (Side-by-Side and Opposite) from one Flask backend and one React frontend.

---

## Primary Development Machine

**All editing, committing, and deploying happens on the Ubuntu PC.**

| | |
|-|---|
| **IP** | `192.168.5.13` |
| **SSH user** | `linux` |
| **SSH password / sudo** | `linux` |
| **Repo path** | `~/merged-version` |

This is the machine that also runs the `pi-merged-client` service (sensor reader).
The Windows machine at `192.168.5.10` is where Claude Code runs — it SSHes into
the Ubuntu PC to make changes.

---

## How Changes Go Live

| What you change | How to deploy |
|-----------------|---------------|
| **Frontend** (`src/`, `index.html`, `vite.config.js`, `.env.production`) | `git push origin main` → Vercel auto-deploys in ~60 s. Nothing else needed. |
| **Backend** (`backend/*.py`, `backend/*.json`) | `npm run build && python3 deploy.py` — builds frontend, SSHes into KVM, uploads files, restarts Flask service. |
| **pi_client** (`backend/pi_client.py`) | `python3 deploy.py` — also SSHes into Ubuntu PC itself and restarts `pi-merged-client.service`. |
| **nginx config** (`nginx_merged.conf`) | `python3 deploy.py` — uploads and reloads nginx on KVM. |
| **CORS allowed origins** | Edit `/root/traefik-conf/merged.yml` on KVM directly — Traefik hot-reloads, no restart needed. |

---

## Architecture

```
CD22 Sensors (wired LAN 192.168.1.200-201)
        │  TCP binary protocol port 8234
        ▼  (reached via enp3s0 wired interface — NOT WiFi)
Ubuntu PC — 192.168.5.13  (primary dev + pi_client machine)
  ~/merged-version/          ← repo lives here
  systemd: pi-merged-client  ← runs backend/pi_client.py, always-on
        │
        │  POST /ingest/readings   (API key: merged-secret-2026)
        │  GET  /config/poll       (picks up sensor write commands)
        ▼
KVM Cloud Server — 194.164.148.145
  Flask (merged_server.py)   port 5002   CLOUD_MODE=true
  nginx                      port 8082   HTTP reverse proxy → :5002
  Traefik (Docker)           port 443    HTTPS + CORS → :5002
  PostgreSQL                 port 5432   database: sensor_db
        ▲
        │  HTTPS + WebSocket   wss://194-164-148-145.sslip.io
        ▼
Vercel — https://merged-version.vercel.app
  Auto-built from GitHub main branch
  VITE_SERVER_URL = https://194-164-148-145.sslip.io  (baked in at build time)
```

---

## Repository

**GitHub:** `https://github.com/software-rajdeep/thickness-monitoring-merged`
**Branch:** `main`
**Credentials in remote URL** — push works directly without separate auth.

```bash
# On Ubuntu PC:
cd ~/merged-version
git pull origin main
git add src/some_file.jsx
git commit -m "describe what changed and why"
git push origin main          # triggers Vercel redeploy automatically
```

> If `git pull` fails with SSL/redirect error, the router is intercepting GitHub.
> Fix: `git config --global http.sslVerify false` then retry, or use phone hotspot.

---

## KVM Cloud Server

### Access

| | |
|-|---|
| **IP** | `194.164.148.145` |
| **SSH user** | `root` |
| **SSH password** | `Federer7roger@` |
| **HTTP (direct)** | `http://194.164.148.145:8082` |
| **HTTPS (public)** | `https://194-164-148-145.sslip.io` |

```bash
ssh root@194.164.148.145
```

### Flask Backend Service

| | |
|-|---|
| **systemd unit** | `merged.service` |
| **source** | `/opt/merged/backend/` |
| **frontend dist** | `/opt/merged/dist/` |
| **Python venv** | `/opt/merged/venv/` |
| **runs as** | `www-data` |
| **port** | `5002` |
| **CLOUD_MODE** | `true` |
| **INGEST_API_KEY** | `merged-secret-2026` |

```bash
# On KVM:
systemctl status merged
systemctl restart merged
journalctl -u merged -f
journalctl -u merged -n 50
ss -tlnp | grep 5002
```

### Database (PostgreSQL on KVM)

| | |
|-|---|
| **database** | `sensor_db` |
| **user** | `rapl` |
| **password** | `rapl2026` |

```bash
psql -U rapl -d sensor_db   # then \dt to list tables
```

Tables:
- `sensor_filtered_readings` — SBS mode filtered (sensor_a, sensor_b, sensor_c)
- `sensor_unfiltered_readings` — SBS mode raw distances
- `opposite_thickness_readings` — Opposite mode filtered (sensor_a, sensor_b, thickness)
- `opposite_thickness_raw_readings` — Opposite mode raw
- `users` — login accounts

### nginx on KVM

| | |
|-|---|
| **config** | `/etc/nginx/sites-available/merged` |
| **port** | `8082` |
| **proxies to** | `http://127.0.0.1:5002` |

```bash
nginx -t && systemctl reload nginx
```

> **Do not add HTTPS or CORS headers to nginx_merged.conf.**
> Traefik handles both. nginx only needs to proxy port 8082 → 5002.

### Traefik (Docker) — HTTPS and CORS

Traefik owns ports 80 and 443, terminates TLS, and is the **sole CORS authority**.
Config file on KVM: `/root/traefik-conf/merged.yml`

```yaml
middlewares:
  cors-headers:
    headers:
      accessControlAllowOriginList:
        - "https://merged-version.vercel.app"
        - "https://finalwebapp.vercel.app"
        - "https://194-164-148-145.sslip.io"
        - "http://localhost:5173"
        - "http://localhost:5002"
      accessControlAllowMethods: ["GET","POST","PUT","DELETE","OPTIONS","PATCH"]
      accessControlAllowHeaders: ["*"]
      accessControlAllowCredentials: true
```

**To add a new allowed origin** (e.g. a new Vercel preview URL):
```bash
# On KVM — edit the list then save. Traefik hot-reloads immediately, no restart.
nano /root/traefik-conf/merged.yml
```

**Why Flask-CORS was removed:** Both Flask-CORS and Traefik were setting
`Access-Control-Allow-Origin` independently. Any origin in both lists got the
header twice, which browsers hard-reject. Flask-CORS is gone; Traefik is the
only CORS layer. Do not re-add `Flask-CORS` or `CORS(app)` to `merged_server.py`.

### Files Deployed to KVM by deploy.py

Uploaded to `/opt/merged/backend/`:
- `merged_server.py`, `user_routes.py`, `download_routes.py`, `email_alert_routes.py`
- `sensor_config.json`, `sensor_network.json`

Uploaded to `/opt/merged/dist/`: built frontend from `dist/`

Uploaded to `/etc/systemd/system/merged.service`: `thickness-monitor.service`

---

## Ubuntu PC — pi_client and Networking

### pi_client Service

| | |
|-|---|
| **systemd unit** | `pi-merged-client.service` |
| **script** | `/home/linux/merged-version/backend/pi_client.py` |
| **sensor config** | `/home/linux/merged-version/backend/sensor_network.json` |
| **posts to** | `http://194.164.148.145:8082/ingest/readings` |
| **API key** | `merged-secret-2026` |
| **rate** | 5 Hz |

```bash
sudo systemctl status pi-merged-client
sudo systemctl restart pi-merged-client
sudo journalctl -u pi-merged-client -f
```

The service file (`/etc/systemd/system/pi-merged-client.service`) runs
`/home/linux/add-kvm-route.sh` as root via `ExecStartPre` before starting.
That script **both brings up the wired sensor interface AND ensures the KVM route
is in place** on every start.

### Ubuntu PC Network Setup (Important)

The Ubuntu PC has two network interfaces:

| Interface | Network | Purpose |
|-----------|---------|---------|
| `enp3s0` (wired) | `192.168.5.x` (DHCP, gets ~192.168.5.7) | **Sensor LAN** — CD22 sensors are on this physical switch |
| `wlx002e2d1034b9` (WiFi) | `192.168.5.x` (static 192.168.5.13) | Internet + KVM access |

**Critical:** Both interfaces share the `192.168.5.x` IP range, but they are on
**different physical switch segments**. The sensors (192.168.1.200, 192.168.1.201)
are only reachable via `enp3s0` (wired). WiFi cannot reach them even though the
IPs are in the same subnet. **If `enp3s0` has no IP, all sensors show "offline".**

Three permanent fixes are in place:

1. **NetworkManager profile** — `Wired connection 1` has `ipv4.method auto`,
   `ipv4.never-default yes` (so the wired interface never hijacks the default
   route away from WiFi), and `connection.autoconnect yes`.

2. **Service ExecStartPre** — `/home/linux/add-kvm-route.sh` checks whether
   `Wired connection 1` is active and brings it up if not, then adds the KVM
   route via WiFi. Runs as root before pi_client starts.

3. **KVM route** — `add-kvm-route.sh` explicitly adds `194.164.148.145/32 via
   192.168.5.1 dev wlx002e2d1034b9` so KVM traffic never accidentally flows via
   the wired interface.

If the KVM becomes unreachable from the Ubuntu PC, check:
```bash
ip route show                          # should show 194.164.148.145 via 192.168.5.1
ping 194.164.148.145                   # should respond
sudo ip route add 194.164.148.145/32 via 192.168.5.1 dev wlx002e2d1034b9
```

If sensors show offline, check:
```bash
ip addr show enp3s0                    # must have a 192.168.5.x inet address
nmcli connection up "Wired connection 1"   # bring it up if missing
```

### email_alert_config.json

This file stores Gmail OAuth tokens and is **gitignored** — it lives only on disk
at `/home/linux/merged-version/backend/email_alert_config.json`.
It is NOT in the repo. Do not commit it. deploy.py does not touch it on the KVM
(the KVM has its own copy managed via the email alerts UI).

---

## Vercel Frontend

| | |
|-|---|
| **URL** | `https://merged-version.vercel.app` |
| **Repo / branch** | `main` |
| **Build command** | `npm run build` |
| **Output dir** | `dist` |
| **Backend URL** | `https://194-164-148-145.sslip.io` (from `.env.production`) |

Vercel redeploys automatically on every push to `main`. No manual steps needed
for frontend-only changes.

The backend URL is baked into the bundle at build time by Vite via `VITE_SERVER_URL`.
To change the backend URL, edit `.env.production` and push.

---

## Project Structure

```
~/merged-version/
├── backend/
│   ├── merged_server.py       # Flask backend — all API routes + WebSocket
│   ├── user_routes.py         # User CRUD (superadmin only)
│   ├── download_routes.py     # CSV export routes
│   ├── email_alert_routes.py  # Email alert config + Gmail OAuth
│   ├── pi_client.py           # Sensor reader — runs as pi-merged-client service
│   ├── pi_merged.service      # systemd unit template for pi_client
│   ├── requirements.txt       # pip deps (Flask, Flask-SocketIO, psycopg2, Werkzeug)
│   ├── sensor_config.json     # Sensor hardware settings (range, offset, etc.)
│   └── sensor_network.json    # Sensor IP/port map (2 sensors = Opposite, 3 = SBS)
├── src/                       # React frontend source
│   ├── App.jsx                # Root: mode selection, socket init, page routing
│   ├── constants/config.js    # SERVER_URL resolution (env var → localStorage fallback)
│   ├── pages/                 # Side-by-side mode pages
│   └── pages/opposite/        # Opposite mode pages
├── .env.production            # VITE_SERVER_URL=https://194-164-148-145.sslip.io
├── vite.config.js             # Dev proxy: all API paths → localhost:5002
├── deploy.py                  # Full deploy script (KVM backend + Ubuntu pi_client)
├── nginx_merged.conf          # nginx config for KVM (port 8082 → 5002 only)
├── thickness-monitor.service  # systemd unit for Flask on KVM (deployed as merged.service)
└── CLAUDE.md                  # This file
```

---

## Dev Workflow

### 1. Frontend change → Vercel

```bash
cd ~/merged-version
# edit files in src/
git add src/
git commit -m "feat: describe the change"
git push origin main
# Vercel rebuilds and redeploys automatically in ~60 seconds
```

### 2. Backend change → KVM

```bash
cd ~/merged-version
# edit files in backend/
npm run build              # must build frontend first — deploy.py uploads dist/
python3 deploy.py          # uploads backend + frontend to KVM, restarts merged.service
                           # also re-deploys pi_client.py to this machine
```

### 3. Local frontend dev server

```bash
cd ~/merged-version
npm run dev
# Opens at http://localhost:5173
# All API calls proxy to localhost:5002 (vite.config.js)
# To point at live KVM instead: localStorage.setItem('thicknessmon.server', 'https://194-164-148-145.sslip.io')
```

### 4. Local backend dev server

```bash
cd ~/merged-version/backend
# Run in CLOUD_MODE (receives data from pi_client via /ingest/readings)
CLOUD_MODE=true SERVER_PORT=5002 python3 merged_server.py

# Run without CLOUD_MODE (connects directly to sensors on 192.168.1.x)
python3 merged_server.py
```

---

## Sensor Modes

| Mode | Sensors used | Measurement |
|------|-------------|-------------|
| **Side-by-Side (SBS)** | A, B, C (same side) | Per-sensor displacement → thickness per sensor |
| **Opposite** | A + B (facing each other) | `gap − (35 + dist_A) − (35 + dist_B)` = object thickness |

Mode is chosen on the frontend start screen; it is not a server config.
The backend infers mode from `sensor_network.json`: 2 sensors = Opposite, 3 = SBS.

Current sensor IPs (in `backend/sensor_network.json`, also configurable from the Backend page):
- Sensor A: `192.168.1.200:8234`
- Sensor B: `192.168.1.201:8234`

Both sensors are on the **wired LAN** — reachable only via `enp3s0` on the Ubuntu PC.
If you add a third sensor for SBS mode, add it as `"C"` in `sensor_network.json` and
run `python3 deploy.py` to push the updated config to both the KVM and the Ubuntu PC.

---

## User Roles

| Username | Password | Access |
|----------|----------|--------|
| superadmin | superadmin123 | Everything incl. user management + email alerts |
| admin | admin123 | All pages except user management |
| supervisor | super123 | Dashboard, Run Mode, Download |
| worker | worker123 | Dashboard only |

---

## Troubleshooting

**Frontend shows "pi client no readings"**
→ pi_client is not posting data. On Ubuntu PC:
```bash
sudo systemctl status pi-merged-client
sudo journalctl -u pi-merged-client -n 20
# If "Network is unreachable":
ip route show   # check 194.164.148.145 route exists
sudo ip route add 194.164.148.145/32 via 192.168.5.1 dev wlx002e2d1034b9
sudo systemctl restart pi-merged-client
```

**CORS errors in browser console**
→ The frontend origin is not in Traefik's allowlist.
→ On KVM: edit `/root/traefik-conf/merged.yml`, add the origin to
  `accessControlAllowOriginList`. Save — Traefik reloads automatically.
→ **Do NOT add Flask-CORS back.** It was removed because it caused duplicate
  headers when combined with Traefik, which browsers reject.

**API calls failing / backend unreachable**
```bash
# On KVM:
systemctl status merged
journalctl -u merged -n 30
ss -tlnp | grep 5002
curl https://194-164-148-145.sslip.io/sensors/status
```

**Backend crash after deploy**
→ `journalctl -u merged -n 30` on KVM — look for `ImportError` or missing file.
→ Check `backend_files` list in `deploy.py` — a needed file may not be listed.

**Backend files wiped to 0 bytes — `ImportError` on every restart**
→ Caused by running a one-off upload/deploy script that opens a remote file for
  write and then crashes before finishing. The file gets truncated to empty.
→ Symptoms: `merged.service` restarts in a loop, log shows `ImportError: cannot
  import name 'X' from 'Y'` even though the import looks correct.
→ Fix: upload all four Python files from the Windows repo in one shot, then restart:
```python
# Run from the Windows machine (py -3 inline or as a script):
import paramiko, json
kvm = paramiko.SSHClient(); kvm.set_missing_host_key_policy(paramiko.AutoAddPolicy())
kvm.connect('194.164.148.145', username='root', password='Federer7roger@', timeout=15)
sftp = kvm.open_sftp()
for fname in ['merged_server.py','user_routes.py','download_routes.py','email_alert_routes.py']:
    sftp.put(rf'C:\Users\admin\Documents\merged\backend\{fname}', f'/opt/merged/backend/{fname}')
sftp.close()
kvm.exec_command('systemctl restart merged')
kvm.close()
```
→ **Prevention: never leave ad-hoc upload/fix scripts (`upload_backend.py`,
  `deploy_fix.py`, `run_deploy.py`, etc.) in the repo root.** Use only
  `deploy.py` for all KVM deploys.

**SBS live readings show "—" / no data despite sensor being connected**
→ The socket.io `sensor_reading` event always carries fields named `distance_A`,
  `distance_B`, `distance_C` (and `thickness`). The SBS socket handler in
  `App.jsx` must read those exact names. If it reads `sensor_A`/`sensor_B`/
  `sensor_C` instead, every value is `null` and the UI shows dashes silently.
→ Check `src/App.jsx` in the `socket.on("sensor_reading", ...)` handler — the
  `else` branch (SBS mode) must use `data.distance_A`, `data.distance_B`,
  `data.distance_C`, not `data.sensor_A` etc.

**Opposite mode thickness shows "—" after calibration**
→ The stream loop only calculates thickness when `gap_distance > 0`. The
  `/thickness/calibration` endpoint (used by SBS calibration and the opposite
  mode "Calibrate" button) captures baseline readings but does NOT set
  `gap_distance`. Result: thickness is always null even after calibration.
→ The stream loop now has a fallback: when `gap_distance = 0` but
  `calibration_active = true` with baselines captured, it computes:
  `thickness = reference_thickness + (baseline_A − current_A) + (baseline_B − current_B)`
→ If thickness still shows "—", check `calibration_active` is `true` in
  `GET /thickness/state`. If not, the user needs to press Calibrate again.

**WebSocket not connecting**
→ Traefik proxies WebSocket automatically. Check the browser console for the
  exact error. Confirm `cors_allowed_origins='*'` is still set in the
  `SocketIO(...)` call in `merged_server.py`.

**git pull blocked**
→ `git config --global http.sslVerify false` then retry, or use phone hotspot.

**pi_client logs say "All sensors offline — skipping POST"**
→ The Ubuntu PC cannot reach the sensors. Root cause is almost always that the
  wired interface (`enp3s0`) has no IP address and is therefore on a different
  network segment from the sensors. Check and fix:
```bash
ip addr show enp3s0          # must show "inet 192.168.5.x" — if blank, run next line
nmcli connection up "Wired connection 1"
# Verify sensors now reachable:
ping -c 2 192.168.1.200
ping -c 2 192.168.1.201
# Then restart pi_client to clear the 30 s reconnect backoff:
sudo systemctl restart pi-merged-client
```
→ To prevent this on reboot: ensure `connection.autoconnect yes` is set:
```bash
nmcli -f connection.autoconnect connection show "Wired connection 1"
# If "no", fix with:
nmcli connection modify "Wired connection 1" connection.autoconnect yes
```
→ The `add-kvm-route.sh` (run by ExecStartPre) also brings up the wired
  connection automatically. If the service is not running, start it:
```bash
sudo systemctl start pi-merged-client
```

**After Ubuntu PC reboot — sensors stop showing**
→ `add-kvm-route.sh` (ExecStartPre) brings up `Wired connection 1` and adds the
  KVM route automatically on every service start. This should be self-healing.
→ If sensors are still offline after reboot: `sudo systemctl restart pi-merged-client`
  and check `ip addr show enp3s0` shows a 192.168.5.x address.
