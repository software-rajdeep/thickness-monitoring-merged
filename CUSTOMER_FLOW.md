# Customer Flow — 10-Customer SaaS, End to End

Grounded in the **live KVM database** (inspected 2026-06-30), not the source.
Live schema reality:
- Reading tables use `sensor_a, sensor_b, sensor_c` / `sensor_a, sensor_b, thickness`,
  `timestamp` is **without time zone**, columns are `real`.
- `users` = `id, username, password_hash, role` (no `created_at`).
- Row caps today are **global**: 1,000,000 raw / 10,000,000 filtered per table.
- Current data: ~27k rows filtered/unfiltered, ~24k thickness.

---

## 1. The whole journey at a glance

```
 WE (Rajdeep)                          CLOUD (KVM)                    CUSTOMER (Acme Steel)
 ────────────                          ───────────                    ─────────────────────
 1. Sell to Acme
 2. Provision ─────────────►  create customer "Acme"
    (mode=opposite, 2 sensors)        create device dev_acme01
                                       store key HASH only
        ◄───────────── returns ONE-TIME key card
 3. Hand over:
    • Agent installer (their OS)
    • Key card (device_id + key)  ───────────────────────────────►  4. Install app
    • Dashboard URL + user logins                                      run setup wizard
                                                                       enter KEY
                                       5. /agent/activate  ◄──────────  app calls server
                                          validate key
                                          return customer details ───►  app shows:
                                          {name, mode, sensor_count}    "Acme Steel —
                                                                         Opposite, 2 sensors"
                                                                       6. enter sensor IPs
                                                                          test → all green
                                                                          service starts
                                       7. /ingest/readings ◄───────────  agent posts 5 Hz
                                          validate device                 with device headers
                                          tag row device_id=dev_acme01
                                          store + emit to room
                                                                       8. Acme user logs in
                                          login → customer_id=Acme  ◄──    at dashboard URL
                                          show ONLY Acme's data ─────►    sees live thickness
```

The customer never sees code, never sees other customers, and never types
anything except **the key and their sensor IPs**. Everything else (company name,
mode, sensor count, dashboard access) is provisioned by us and pulled from the
server during activation.

---

## 2. What we PROVIDE to each customer (the deliverables)

| Deliverable | What it is |
|---|---|
| **Agent installer** | One file for their platform: `ThicknessAgentSetup.exe` (Windows), `.deb` (Linux/Pi). Bundles Python — no dependencies. |
| **Activation key card** | `device_id` (e.g. `dev_acme01`) + `device_key` (secret). One per sensor station. This is the only secret they hold. |
| **Dashboard access** | The existing URL `https://merged-version.vercel.app` + their company's user logins (we create them, scoped to their customer). |
| **Quick-start sheet** | 4 steps: install → enter key → enter sensor IPs → done. |

---

## 3. What the APP asks (the setup wizard)

The agent serves a tiny local page at `http://localhost:7000` on first run.
**It asks for only two things; the rest it learns from the server.**

```
 Step 1  ┌────────────────────────────────────────────┐
         │  Activate this device                      │
         │  Enter your Activation Key:                │
         │  device id : [ dev_acme01            ]     │
         │  key        : [ ••••••••••••••••      ]     │
         │                              [ Activate ]  │
         └────────────────────────────────────────────┘
                    │  POST /agent/activate
                    ▼
 Step 2  ┌────────────────────────────────────────────┐
         │  ✓ Activated                               │
         │  Customer : Acme Steel                     │   ← came FROM server
         │  Mode     : Opposite (2 sensors: A, B)     │   ← came FROM server
         │                               [ Next ]     │
         └────────────────────────────────────────────┘
 Step 3  ┌────────────────────────────────────────────┐
         │  Sensor network                            │
         │  Sensor A IP : [ 192.168.1.200 ] port 8234 │
         │  Sensor B IP : [ 192.168.1.201 ] port 8234 │
         │                         [ Test connection ]│
         └────────────────────────────────────────────┘
 Step 4  ┌────────────────────────────────────────────┐
         │  ✓ Sensor A reachable   ✓ Sensor B reachable│
         │  ✓ Server reachable                        │
         │            [ Finish & start monitoring ]   │
         └────────────────────────────────────────────┘
```

After "Finish": the agent writes its config, registers, installs itself as a
service (auto-start on boot), and begins posting. The wizard is never needed
again unless they re-configure.

**Why the app gets customer details from the server:** the key is the identity.
The server is the single source of truth for *who this device belongs to* and
*what mode it runs*. The customer can't get it wrong, and we can change a
customer's plan/mode server-side without re-shipping anything.

---

## 4. The activation handshake (new endpoint)

```
POST /agent/activate
  body: { "device_id": "dev_acme01", "device_key": "<secret>" }

  server: SELECT * FROM devices WHERE device_id=%s AND revoked=false
          check_password_hash(device_key_hash, device_key)
          UPDATE devices SET last_seen=NOW()

  200 -> { "customer_name": "Acme Steel",
           "sensor_mode": "opposite",
           "sensor_count": 2,
           "sensor_labels": ["A","B"],
           "server_url": "https://194-164-148-145.sslip.io",
           "post_rate_hz": 5 }
  401 -> invalid / revoked
```

Then every reading POST carries the identity:
```
POST /ingest/readings
  headers: X-Device-Id: dev_acme01 ,  X-Device-Key: <secret>
  body:    { timestamp, sensor_A, sensor_B }
  server:  validate -> stamp device_id -> store -> emit to room "dev_acme01"
```

---

## 5. The database for 10 customers

### New tables
```sql
customers ( id, name UNIQUE, created_at )
devices   ( device_id PK, customer_id -> customers,
            device_key_hash, sensor_mode, label,
            revoked DEFAULT false, last_seen, created_at )
```

### Existing reading tables — add tenant tag
```sql
ALTER TABLE sensor_filtered_readings        ADD COLUMN device_id VARCHAR(40);
ALTER TABLE sensor_unfiltered_readings      ADD COLUMN device_id VARCHAR(40);
ALTER TABLE opposite_thickness_readings     ADD COLUMN device_id VARCHAR(40);
ALTER TABLE opposite_thickness_raw_readings ADD COLUMN device_id VARCHAR(40);
-- + composite index (device_id, timestamp DESC) on each
```

### Users belong to a customer
```sql
ALTER TABLE users ADD COLUMN customer_id INTEGER;   -- NULL = Rajdeep superadmin (sees all)
```

### Example seed for 10 customers
- 10 rows in `customers`.
- ≥1 row per customer in `devices` (a customer with two sensor stations gets two
  devices, e.g. `dev_acme01`, `dev_acme02`).
- Each customer's dashboard users carry their `customer_id`.

---

## 6. THE operational gotcha at 10 customers — row caps & volume

This is the single most important change. Today the trim is **global**:

> at 5 Hz, one device writes **432,000 rows/day per table**.
> 10 devices → **~4.3 million rows/day** into the (1,000,000-cap) raw table.
> The global cap would hold barely **~5 hours** of combined data, and customers
> would silently evict each other's history.

Fixes (all required):
1. **Per-device trim** — cap rows *per `device_id`*, not per table:
   `keep newest N rows WHERE device_id=X`. So Acme's history can't be deleted by
   Beta's traffic.
2. **Retention policy** — raw/unfiltered tables are high-volume; keep them at full
   5 Hz for a short window (e.g. 7–30 days/device) and rely on the filtered tables
   for long-term. Or downsample older raw data.
3. **Disk watch** — ~17M rows/day across all tables at 10 customers. Add a daily
   size check + alert. (Already have email-alert plumbing to reuse.)

> Decision needed from you: **how long must raw 5 Hz history be retained per
> customer?** That single number sizes the whole database.

---

## 7. What WE do per sale (provisioning)

```
POST /provision            (superadmin only)
  { "customer": "Acme Steel", "sensor_mode": "opposite", "label": "Line 1" }
  -> creates customer (if new) + device
  -> returns { device_id, device_key }   (key shown ONCE, only hash stored)
```
Start as a `curl` we run; later a superadmin page "Add customer / Add device"
that prints the key card. Revoking is `UPDATE devices SET revoked=true` — that
install instantly stops working (clean for billing/support).

---

## 8. Dashboard scoping (per customer)

- Login already returns `role`; add `customer_id` + the customer's device list.
- Superadmin (`customer_id=NULL`) → can switch between all customers/devices.
- Customer user → locked to their `customer_id`; if they have >1 device, a device
  picker; all history/CSV/live queries get `WHERE device_id=…`.
- WebSocket: client joins room `device_id`; server emits live readings only to it.

---

## 9. Decisions (locked 2026-06-30)

1. **Retention — RECOMMENDED DEFAULT (configurable):**
   per-device cap of **~3,000,000 rows ≈ 7 days at 5 Hz** on every reading table,
   enforced **per `device_id`**. Env-configurable (`PER_DEVICE_ROW_CAP`) so we can
   raise it per customer later. Long-term downsampling (5 Hz → 1/min rollup) is
   deferred to a Phase-4 enhancement, not built now.
   - Sizing: 1 device ≈ 4 tables × 3M ≈ 12M rows. 10 customers averaging ~1.5
     devices ≈ ~15 devices ≈ **~180M rows**. With indexes that is **~15–20 GB**.
   - **⚠ KVM DISK CONSTRAINT (checked 2026-06-30):** `/` is 48 GB, **only 12 GB
     free** (76% used); DB is 20 MB today. 7-day raw at 10 customers (~15–20 GB)
     **will NOT fit.** One of these is required before scaling past ~2–3 customers:
     - **(a)** expand the KVM disk/volume (cleanest — gives headroom for growth), or
     - **(b)** shorten raw retention to **~2 days** (`PER_DEVICE_ROW_CAP ≈ 850k`),
       keeping only filtered/thickness longer, or
     - **(c)** build the 5 Hz→1/min downsample rollup now instead of deferring it
       (keeps long history in a fraction of the space).
   - **Recommended:** (a) expand disk to ~100 GB if possible; otherwise (b) 2-day
     raw cap now + (c) downsampling as the Phase-4 follow-up.

2. **Multiple devices per customer — YES.** A customer can install the agent on
   several machines/lines; each is its own `device_id` + key card, all shown under
   their dashboard with a **device picker**. Reading queries always scope by the
   selected `device_id`.

3. *(still open)* **Who creates customer dashboard logins** — assume **us at
   provisioning time** for now (simplest); self-service can come later.

4. *(still open)* **Mode per device** — assume **fixed at provisioning** (set in
   the `devices` row); the agent reads it from `/agent/activate`. Customer does not
   switch modes in the app.
