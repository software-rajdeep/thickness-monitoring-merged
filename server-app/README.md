# SERVER-APP — the cloud / server version

**What this is:** the hosted version. It is NOT a single program — it is four
pieces that talk over the internet:

1. **KVM cloud server** — `backend/merged_server.py` running with
   `CLOUD_MODE=true`, backed by **PostgreSQL**. Sits behind nginx (port 8082)
   and Traefik (HTTPS + CORS, port 443).
2. **pi_client** — `backend/pi_client.py`, a systemd service on the Ubuntu PC
   (`pi-merged-client`) that reads the CD22 sensors and POSTs readings to the
   KVM at 5 Hz.
3. **Vercel frontend** — the React app in `src/` built with the cloud URL
   baked in (`.env.production`), auto-deployed from the git `main` branch.
4. **Thickness Agent** (optional, customer SaaS) — `agent/thickness_agent.py`,
   an installable reader for customers on a Raspberry Pi / PC. Also
   `electron/` (kiosk shell that just displays the Vercel site).

---

## Real files for this product

| File | Why it matters |
|------|----------------|
| `backend/merged_server.py` | The Flask + SocketIO server (shared with the local apps!) |
| `backend/user_routes.py` | User CRUD (superadmin only) |
| `backend/download_routes.py` | CSV export endpoints |
| `backend/email_alert_routes.py` | Email alerts + Gmail OAuth |
| `backend/pi_client.py` | Sensor reader on the Ubuntu PC |
| `deploy.py` | The one deploy script: backend→KVM + frontend→KVM + pi_client→Ubuntu |
| `nginx_merged.conf` | nginx reverse-proxy config for the KVM |
| `thickness-monitor.service` | systemd unit (deployed to KVM as `merged.service`) |
| `.env.production` | Bakes `VITE_SERVER_URL` (the cloud URL) into the frontend build |
| `agent/` | The customer-side cloud agent (builds `thickness-agent` / `.deb`) |
| `electron/` | Raspberry Pi kiosk shell that shows the Vercel dashboard |

---

## How it is run / deployed

**Cloud backend** (runs 24/7 on the KVM `194.164.148.145`):
```bash
systemctl status merged            # on the KVM
systemctl restart merged
```

**Deploy after a change** (from the repo root, after `git push`):
```bash
npm run build        # cloud build — .env.production gives it the cloud URL
python3 deploy.py    # uploads backend + dist to KVM, restarts merged.service,
                     # and updates pi_client on the Ubuntu PC
```

**Frontend-only change:** push to `main` → Vercel rebuilds automatically.

**pi_client on the Ubuntu PC** (`192.168.5.13`):
```bash
sudo systemctl status pi-merged-client
sudo journalctl -u pi-merged-client -f
```

---

## Edit map — "where do I go to change X?"

| I want to change… | Edit here |
|-------------------|-----------|
| Any dashboard screen / chart / page (React) | `src/` at the repo root |
| Backend logic, stream loop, thickness math, DB | `backend/merged_server.py` (+ the `*_routes.py` files) |
| Sensor reader behaviour (Ubuntu PC) | `backend/pi_client.py` |
| Deploy steps / files uploaded | `deploy.py` |
| nginx proxying on the KVM | `nginx_merged.conf` |
| HTTPS / CORS allow-list | `/root/traefik-conf/merged.yml` **on the KVM** (not in this repo) |
| Customer agent | `agent/` |

---

## Why the code is shared (not copied per product)

`backend/merged_server.py` and the whole React frontend in `src/` are the
**same source** used by the local offline apps too. The server decides its
mode from env vars — `CLOUD_MODE=true` (this product) vs
`LOCAL_MODE=true` (the `local-exe-app` / `local-backend-app` products), which
swap PostgreSQL for the local SQLite shim. That is intentional: a fix or
feature lands once and applies to every version, instead of drifting apart in
three separate copies.
