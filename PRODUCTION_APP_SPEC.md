# Production Distribution Spec — Thickness Agent (SaaS)

This is the authoritative plan for turning the merged thickness app into a
**product we ship to customers as an installable app**, not as source code.

- **Model:** SaaS. One shared cloud server (the existing KVM at
  `194.164.148.145`) and one dashboard URL (Vercel). Many customers, each
  with their own installed agent, all pushing to the same server.
- **Connectivity assumption:** customer sites have reliable internet. The agent
  does **not** need offline buffering — if the server is briefly unreachable it
  drops the reading (current `pi_client.py` behaviour is fine).

The thing we install at the customer site is the **Thickness Agent**: a packaged,
double-click-installable version of `backend/pi_client.py`. The server and
dashboard stay centralized; the customer only opens a browser.

---

## 1. Architecture

```
 ┌─────────────────────── CUSTOMER SITE A ───────────────────────┐
 │   CD22 sensors            THICKNESS AGENT app                  │
 │  (wired LAN)              (installed on Pi / Windows / Linux)  │
 │  ┌──────────┐   TCP 8234  ┌────────────────────────────┐      │
 │  │ Sensor A │◄───────────►│  • reads sensors (5 Hz)    │      │
 │  │ Sensor B │             │  • first-run setup wizard  │      │
 │  │ Sensor C │             │  • holds device_id + key   │      │
 │  └──────────┘             │  • runs as a service       │      │
 └───────────────────────────┴─────────────┬──────────────┴──────┘
        SITE B ───────────┐                 │ HTTPS POST /ingest/readings
        SITE C ─────────┐ │                 │ header: device_id + device_key
                        ▼ ▼                 ▼
                 ┌────────────────────────────────────┐
                 │   YOUR CLOUD SERVER (KVM)          │
                 │   Flask (multi-tenant)             │
                 │    /ingest   → validate device     │
                 │              → tag w/ device_id    │
                 │    /provision (admin only)         │
                 │   PostgreSQL                       │
                 │    devices  (id, customer, key)    │
                 │    readings (… + device_id)        │
                 │    users    (… + customer_id)      │
                 └─────────────────┬──────────────────┘
                                   │ HTTPS / WebSocket
                                   ▼
                 ┌────────────────────────────────────┐
                 │   Dashboard (Vercel, one URL)      │
                 │   login → sees only own devices    │
                 └────────────────────────────────────┘
```

We already own the server + dashboard. The two new things are **(a)** the
installable Agent, and **(b)** a tenancy layer on the server.

---

## 2. The Thickness Agent app

Same codebase as `pi_client.py`, repackaged so the customer never sees Python.
The Python runtime + `requests` are bundled inside the binary — **zero
dependencies for the customer to install**.

| Platform | Package | Runs as | Build tool |
|---|---|---|---|
| Windows laptop | `ThicknessAgentSetup.exe` | Windows **Service** (auto-start) + tray icon | PyInstaller → Inno Setup |
| Linux laptop | `.deb` / AppImage | **systemd** service | PyInstaller → fpm/dpkg |
| Raspberry Pi | `.deb` (ARM) | **systemd** service | PyInstaller on ARM |

### First-run setup wizard (new feature)

Instead of hand-editing `sensor_network.json`, the installer opens a small local
page at `http://localhost:7000` that asks for:

1. **Activation key** — the per-device key issued at purchase.
2. **Sensor IPs** — A/B (Opposite) or A/B/C (SBS); port defaults to 8234.

The wizard then writes the local config, registers the device with the cloud,
and starts the service. Config lives next to the agent (e.g.
`%ProgramData%\ThicknessAgent\config.json` on Windows,
`/etc/thickness-agent/config.json` on Linux/Pi) and holds:

```json
{
  "server_url": "https://194-164-148-145.sslip.io",
  "device_id":  "dev_a1b2c3",
  "device_key": "<secret>",
  "sensors":    { "A": {"ip":"192.168.1.200","port":8234},
                  "B": {"ip":"192.168.1.201","port":8234} }
}
```

### Agent changes vs current `pi_client.py`

- Read `device_id` + `device_key` from config; send both on every POST
  (replaces the single shared `API_KEY = merged-secret-2026`).
- Add the localhost setup-wizard mini-server (config UI).
- No averaging/smoothing changes — raw-in pipeline stays exactly as today.

---

## 3. Device identity & provisioning

```
 YOU (sales/setup)                CLOUD                       CUSTOMER
 ────────────────               ─────────                    ──────────
 POST /provision  ───────────►  create device row
  {customer, mode}              device_id = dev_a1b2c3
                                device_key = <random>
        ◄──────────────────────  return key
        └─ give key to customer ───────────────────────────► enter key in wizard
                                  ◄── agent registers ──────  agent starts posting
                                  validate key
                                  every /ingest tagged
                                  with device_id
```

- Each reading carries `device_id` + `device_key`.
- Server validates the key, stamps data with `device_id`, stores it.
- Dashboard shows a user only devices belonging to their customer.
- Revoke a key → that install stops working (clean for support/billing).

---

## 4. Server changes (multi-tenancy) — Phase 1 work

1. **`devices` table:** `device_id` (PK), `customer_name`, `device_key_hash`,
   `sensor_mode` (`sbs`/`opposite`), `last_seen`, `created_at`, `revoked`.
2. **`device_id` column** added to `sensor_filtered_readings`,
   `sensor_unfiltered_readings`, `opposite_thickness_readings`,
   `opposite_thickness_raw_readings`.
3. **`/ingest/readings`:** replace the single global `last_ingest_reading` with a
   per-device structure keyed by `device_id`; validate the per-device key
   (hashed) instead of the one shared `INGEST_API_KEY`.
4. **`/provision` (admin only):** mints a new device, returns its key once.
5. **Users → customers:** add `customer_id` to `users`; all dashboard queries
   filter by it. Roles (superadmin/admin/supervisor/worker) unchanged.
6. **Live WebSocket:** room/namespace per device so live readings route to the
   correct dashboard.

> Keep the existing `INGEST_API_KEY` path working during migration (legacy
> single-tenant device) so nothing breaks while Phase 1 lands.

---

## 5. Roadmap

| Phase | Goal | Output |
|---|---|---|
| 0 | Lock the design | **This file** |
| 1 | Multi-tenant server | `devices` table, `device_id` on readings, per-device auth, `/provision` |
| 2 | Package agent (Windows) | `.exe` installer + setup wizard, end-to-end on new server |
| 3 | Other platforms | Linux `.deb` + Raspberry Pi `.deb` from one build script |
| 4 | Production polish | auto-update, agent heartbeat on dashboard, key-revocation UI |

---

## 6. Open items to decide later

- **Auto-update:** version-check endpoint + signed binary download (Phase 4).
- **Heartbeat:** agent posts health even when sensors are offline so the
  dashboard can show "agent online / sensors offline" distinctly.
- **Provisioning UI:** start with a manual `/provision` curl; add an admin page
  later.
- **Billing hook:** revoked device key is the natural enforcement point.
