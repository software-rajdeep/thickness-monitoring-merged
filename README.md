# Thickness Monitoring System

## How it works

Three pieces run together:

1. **Ubuntu PC (this machine)** — reads the CD22 sensors over LAN and sends readings to the server
2. **KVM server** — receives the sensor data, stores it in the database, streams it via WebSocket
3. **Vercel website** — the browser UI, talks to the KVM server to show live data and controls

---

## To use the system

**On the Ubuntu PC**, make sure the sensor service is running:

```bash
sudo systemctl status pi-merged-client
```

If it says `active (running)` — you're done, everything is working.
It auto-starts on boot so normally you don't need to do anything.

If it's stopped for some reason:
```bash
sudo systemctl start pi-merged-client
```

**Then open the Vercel website in your browser.** Log in, pick a mode (Side-by-Side or Opposite), and the live data will appear.

That's it. The KVM backend and Vercel frontend run 24/7 with no action needed.

---

## To check if sensor data is flowing

```bash
sudo journalctl -u pi-merged-client -f
```

You should see lines like:
```
[2026-06-17T10:19:54] Posted {'sensor_A': -2.89, 'sensor_B': -0.81 ...} -> HTTP 200
```

`HTTP 200` means the KVM is receiving data. If you see `Connection error`, the KVM is unreachable or the internet is down.

---

## To make changes

### Frontend change (anything in `src/`)

```bash
cd ~/merged-version
git add .
git commit -m "describe your change"
git push origin main
```

Vercel automatically rebuilds and redeploys in about 60 seconds. No other steps.

> If git push fails due to SSL/redirect: `git config --global http.sslVerify false` then try again.
> Or use your phone as a hotspot temporarily.

### Backend change (anything in `backend/`)

```bash
cd ~/merged-version
npm run build
python3 deploy.py
```

`deploy.py` will SSH into the KVM, upload the new files, and restart the Flask service.
It will also update the pi_client on this machine if you changed `pi_client.py`.

---

## What runs where

| What | Where | How it runs |
|------|-------|-------------|
| Sensor reader | Ubuntu PC (this machine) | `pi-merged-client` systemd service |
| Backend API + database | KVM `194.164.148.145` | `merged` systemd service, port 5002 |
| HTTPS proxy | KVM (Traefik, Docker) | Always on, port 443 |
| Frontend website | Vercel | Always on, auto-deploys from GitHub |

---

## KVM server details

| | |
|-|---|
| IP | `194.164.148.145` |
| SSH | `ssh root@194.164.148.145` — password: `Federer7roger@` |
| HTTP (direct) | `http://194.164.148.145:8082` |
| HTTPS (public) | `https://194-164-148-145.sslip.io` |
| Flask service | `systemctl status merged` |
| Flask logs | `journalctl -u merged -f` |
| Backend files | `/opt/merged/backend/` |
| Frontend files | `/opt/merged/dist/` |
| Database | PostgreSQL — `sensor_db` / user `rapl` / password `rapl2026` |

---

## Sensor service details (this machine)

| | |
|-|---|
| Service name | `pi-merged-client` |
| Script | `/home/linux/merged-client/pi_client.py` |
| Posts to | `http://194.164.148.145:8082/ingest/readings` |
| API key | `merged-secret-2026` |
| Rate | 5 readings/second |
| Sensor A | `192.168.1.200:8234` |
| Sensor B | `192.168.1.201:8234` |
| Sensor C | `192.168.1.202:8234` |

---

## Default login credentials

| Username | Password | Role |
|----------|----------|------|
| superadmin | superadmin123 | Everything |
| admin | admin123 | All pages |
| supervisor | super123 | Dashboard, Run, Download |
| worker | worker123 | Dashboard only |

---

## GitHub repo

`https://github.com/software-rajdeep/thickness-monitoring-merged`
