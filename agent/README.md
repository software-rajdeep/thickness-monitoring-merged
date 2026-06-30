# Thickness Agent — the installable customer app

This is what we ship to a customer. It runs near their CD22 sensors (on a
Raspberry Pi, Linux PC, Windows PC, or Mac), reads the sensors, and uploads
readings to the cloud, tagged with the customer's unique device identity.

The customer never sees code and never types their company name or mode — those
come from the server when they enter their **Activation Code**.

---

## What the customer receives

1. **The installer / binary** for their platform (built from this folder).
2. **An Activation Card** — a `device_id` + `device_key` we generate per device.
   The key is the password; it is useless to anyone without the matching
   `device_id`, and we can revoke it server-side at any time.
3. **Dashboard URL + login** to view their data from anywhere.

---

## How it gets configured — two ways

### A. We pre-configure it (headless — no browser, best for a Pi we ship)

Drop the customer's Activation Card values into `/etc/thickness-agent/agent.env`
(template: `agent.env.example`), then `sudo systemctl restart thickness-agent`.
The agent validates with the server, pulls the customer name + mode, and starts
uploading. The customer powers it on and does nothing.

```ini
DEVICE_ID=dev_xxxxxxxx
DEVICE_KEY=<secret from provision>
SENSORS=A=192.168.1.200,B=192.168.1.201
SERVER_URL=http://194.164.148.145:8082
```

### B. The customer runs the setup wizard (self-service)

1. Install the agent (service starts automatically, or run the binary).
2. Open **http://localhost:7000** — the setup wizard.
3. **Step 1** — paste the Activation `device_id` + key → *Activate*.
   The agent checks with the server and shows the customer name + sensor mode.
4. **Step 2** — enter the sensor IPs → *Test connection* (checks each sensor +
   the server). When all green, config saves and monitoring starts.
5. **Step 3** — done. It runs in the background and restarts on boot.

---

## How WE provision a customer (one command)

```bash
curl -s -X POST https://194-164-148-145.sslip.io/provision \
  -H "X-Admin-Token: $PROVISION_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"customer":"Acme Steel","sensor_mode":"opposite","label":"Line 1"}'
# -> {"device_id":"dev_xxxxxxxx","device_key":"<secret>", ...}
```

Print the returned `device_id` + `device_key` onto the customer's Activation Card.
The plaintext key is shown **once** — only its hash is stored. To disable a
device later: `UPDATE devices SET revoked=true WHERE device_id='dev_xxxxxxxx';`
(a revoke endpoint/UI is a follow-up).

`sensor_mode` is `opposite` (2 sensors A,B) or `sbs` (3 sensors A,B,C).

---

## Building the installer

Build **on the target architecture** (PyInstaller does not cross-compile).

### Linux / Raspberry Pi — one command → a shippable `.deb` (recommended)

```bash
./build-deb.sh                 # -> thickness-agent_1.0.0_<arch>.deb
```

Run it on the same chip type as the customer machine: an x86_64 Linux PC produces
an `amd64` package; a 64-bit Pi produces `arm64`; a 32-bit Pi produces `armhf`
(the script auto-detects and stamps the architecture). The resulting `.deb`
bundles the binary, the systemd service, and the install hooks, so on the
customer machine it's just:

```bash
sudo dpkg -i thickness-agent_1.0.0_<arch>.deb   # service auto-starts on install + on boot
```

### Other platforms — bare binary

| Target | Command | Output | Run as a service via |
|---|---|---|---|
| Windows | `.\build.ps1` | `dist\thickness-agent.exe` | [NSSM](https://nssm.cc) (steps echoed by `build.ps1`) |
| macOS | `./build.sh` | `dist/thickness-agent` | a LaunchAgent plist |
| Linux (binary only) | `./build.sh` | `dist/thickness-agent` | copy + `thickness-agent.service` |

---

## Configuration

The wizard writes a `config.json`:

| Platform | Path |
|---|---|
| Windows | `%ProgramData%\ThicknessAgent\config.json` |
| Linux / Pi | `/etc/thickness-agent/config.json` (or `~/.thickness-agent/`) |
| macOS | `~/Library/Application Support/ThicknessAgent/config.json` |

Override with the `THICKNESS_AGENT_CONFIG` env var. Other env knobs:
`SERVER_URL` (default cloud), `WIZARD_PORT` (default 7000), `POST_RATE_HZ`
(default 5).

To re-configure later, reopen `http://localhost:7000`.

---

## Provisioning also creates the customer's admin login

`/provision` returns the device code **and**, for a customer's first device, a
fresh admin login for that customer (no shared admin/admin123):

```json
{ "device_id":"dev_xxxx", "device_key":"...",
  "admin_email":"admin@acmesteel.local", "admin_password":"<generated>" }
```

Put both on the activation card. The customer logs into the dashboard with that
email + password and can create their own users (scoped to their company).
Pass `admin_email` / `admin_password` in the provision body to set them yourself.

## Auth model (dashboard)

- **Login:** `POST /auth/login {email,password}` → `{token, user}`. Send the
  token as `Authorization: Bearer <token>` on protected calls.
- **Roles:** `superadmin` (us, all customers) · `customer_admin` (manages own
  company's users) · `operator` · `viewer`.
- **User management:** `/auth/users` GET/POST, `/auth/users/<id>` DELETE,
  `/auth/users/<id>/password` POST — all scoped to the caller's customer.

## Environment variables (server side)

Set in `merged.service` on the KVM:

- `PROVISION_ADMIN_TOKEN` — guards `/provision`. **Changed from default.**
- `AUTH_SECRET` — signs login tokens. **Must be a strong secret (set).**
- `SUPERADMIN_PASSWORD` — superadmin password seeded on a fresh DB.
- `PER_DEVICE_ROW_CAP` — per-device DB row cap (default 3,000,000 ≈ 7 days @ 5 Hz).
- `AUTH_TOKEN_TTL` — login token lifetime in seconds (default 7 days).
