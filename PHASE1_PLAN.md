# Phase 1 — Multi-Tenant Server: Implementation Plan

Goal: let many installed agents push to the one cloud server without their data,
calibration, or live streams colliding. This is the prerequisite for everything
else in `PRODUCTION_APP_SPEC.md`.

> **Status: PLAN ONLY. No live change until reviewed & approved.**
> The KVM is production. Migration is additive and reversible.

---

## 0. The key finding (why this is more than "add a column")

The server today is single-tenant **everywhere**, not just in the DB. All of
this state is global (one set of values for the whole server):

| Global state | Where | Must become |
|---|---|---|
| `last_ingest_reading = {"A","B","C"}` | line ~625 | per-device |
| moving-average `filter_windows` | `stream_ingest_loop` ~1261 | per-device |
| `thickness_state` / calibration | `get_thickness_state()` | per-device |
| `stream_state["active"]` | stream loop | per-device |
| WebSocket `socketio.emit("sensor_reading", …)` | ~1325 | per-device room |
| DB rows (4 reading tables) | `_db_write` ~1198 | tagged with `device_id` |

So the work is: **(1)** add `device_id` everywhere data/state is keyed, and
**(2)** change how readings are processed.

### Architectural decision: process readings at ingest time, per device

Today a single background thread (`stream_ingest_loop`) pulls the one global
`last_ingest_reading`, filters it, computes thickness, writes the DB, and emits.
That doesn't scale to N devices cleanly.

**For SaaS (CLOUD_MODE) we move the per-tick work INTO `/ingest/readings`:** when
device X POSTs, we filter→compute→store→emit *for device X only*, keyed by its
`device_id`. The background polling loop is only needed for the non-CLOUD on-prem
path (local sensor polling), which SaaS never uses — so it stays untouched as a
fallback. This localizes all multi-tenancy to the ingest path and avoids a global
loop iterating every device each tick.

---

## 1. Database migration (additive, reversible)

> **First step before writing SQL:** inspect the LIVE schema with `\d` on each
> table. The `CREATE TABLE` statements and the `INSERT` statements in
> `merged_server.py` disagree on column names (e.g. CREATE says
> `sensor_A_distance`, INSERT uses `sensor_a`), which means the live tables were
> altered by hand. Trust the live DB, not the source.

```sql
-- new tenancy tables
CREATE TABLE IF NOT EXISTS customers (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(120) UNIQUE NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS devices (
    device_id        VARCHAR(40) PRIMARY KEY,      -- e.g. dev_a1b2c3
    customer_id      INTEGER REFERENCES customers(id),
    device_key_hash  VARCHAR(255) NOT NULL,        -- werkzeug hash, never plaintext
    sensor_mode      VARCHAR(10) NOT NULL,         -- 'sbs' | 'opposite'
    label            VARCHAR(120),
    revoked          BOOLEAN DEFAULT FALSE,
    last_seen        TIMESTAMPTZ,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- tag every reading table; NULL-able so existing rows survive the migration
ALTER TABLE sensor_filtered_readings      ADD COLUMN IF NOT EXISTS device_id VARCHAR(40);
ALTER TABLE sensor_unfiltered_readings    ADD COLUMN IF NOT EXISTS device_id VARCHAR(40);
ALTER TABLE opposite_thickness_readings   ADD COLUMN IF NOT EXISTS device_id VARCHAR(40);
ALTER TABLE opposite_thickness_raw_readings ADD COLUMN IF NOT EXISTS device_id VARCHAR(40);

-- composite indexes so per-device queries stay fast
CREATE INDEX IF NOT EXISTS idx_filtered_dev_ts   ON sensor_filtered_readings      (device_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_unfiltered_dev_ts ON sensor_unfiltered_readings    (device_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_thick_dev_ts      ON opposite_thickness_readings   (device_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_thickraw_dev_ts   ON opposite_thickness_raw_readings (device_id, timestamp DESC);

-- users belong to a customer (NULL = superadmin / sees all)
ALTER TABLE users ADD COLUMN IF NOT EXISTS customer_id INTEGER REFERENCES customers(id);
```

**Backwards compatibility:** existing rows get `device_id = NULL`. We backfill a
single legacy device row (`dev_legacy`) and `UPDATE … SET device_id='dev_legacy'
WHERE device_id IS NULL` so the current single install keeps working unbroken.

**Rollback:** drop the new tables + columns; nothing destructive happens to
existing reading data.

---

## 2. Server code changes (`merged_server.py`)

Add `init_db()` statements for the migration above (idempotent `IF NOT EXISTS`).

### 2a. Per-device state container
Replace the three globals with one dict keyed by device:
```python
device_state = {}   # device_id -> {
                    #   "last_reading": {"A":..,"B":..,"C":..},
                    #   "filter_windows": {sid: deque(maxlen=FILTER_WINDOW)},
                    #   "thickness_state": {...}, "active": bool }
```
A helper `get_device_state(device_id)` lazily creates the entry.

### 2b. `/ingest/readings` — auth + process per device
- Read `device_id` + `device_key` from headers (`X-Device-Id`, `X-Device-Key`).
- Look up `devices`, check `revoked=false`, verify `check_password_hash`.
- **Legacy path kept:** if no `device_id` header and the old `INGEST_API_KEY`
  matches, treat as `dev_legacy` (so today's single install is unaffected).
- Update `last_seen`.
- Then run the per-tick work for THAT device: push to its filter windows,
  compute raw + filtered thickness from ITS calibration, `_db_write(... device_id)`,
  and `socketio.emit("sensor_reading", payload, room=device_id)`.

### 2c. `_db_write` — add `device_id` to the 4 INSERTs.

### 2d. Read endpoints / `download_routes.py` — scope by device
Every "latest reading" / history / CSV query gains `WHERE device_id = %s`. The
device is chosen by the logged-in user's customer (a customer with one device →
implicit; multiple → a selector).

### 2e. WebSocket rooms
On socket connect, client sends its selected `device_id`; server `join_room`s it.
Emits target that room instead of broadcasting to everyone.

### 2f. `/provision` (admin only)
`POST /provision {customer, sensor_mode, label}` → creates customer (if new) +
device, returns `{device_id, device_key}` **once** (only the hash is stored).
Protect with a superadmin check / admin token.

### 2g. Calibration endpoints — key by device
`/thickness/calibration`, `/thickness/state`, etc. operate on
`get_device_state(device_id)` instead of the global.

---

## 3. Frontend changes (minimal for Phase 1)
- Login already returns role; add `customer_id` + device list.
- If a user has >1 device, show a device selector; pass `device_id` into the
  socket join and into history/CSV calls.
- Single-device customers: no visible change.

---

## 4. Agent change (`pi_client.py`)
- Send `X-Device-Id` + `X-Device-Key` headers (from config) instead of the shared
  `X-Api-Key`. (Full agent packaging is Phase 2; this header change is the only
  Phase-1 touch.)

---

## 5. Rollout order (safe, staged)
1. Run migration SQL on KVM (additive) + backfill `dev_legacy`. Verify existing
   dashboard still works (reads NULL/legacy rows).
2. Deploy server with **legacy ingest path intact** + new per-device path. Today's
   install keeps posting via the old key → still works.
3. Provision a **test device**, point a second agent (or a laptop) at it, confirm
   its data lands tagged and isolated from `dev_legacy`.
4. Add device scoping to read/CSV endpoints + dashboard selector.
5. Migrate the real install from `dev_legacy` to a real provisioned device.
6. Only after all customers are on real devices: retire the legacy key.

---

## 6. Risks / watch-items
- **Live schema vs source mismatch** — inspect `\d` first (section 1).
- **0-byte truncation rule** — deploy all 4 backend .py files in one shot via
  `deploy.py`; never leave ad-hoc upload scripts in the repo root.
- **Concurrent dev** — a 2nd session edits the Ubuntu repo; `git pull` /
  back up KVM files before deploying.
- **CORS/Traefik** — no changes needed; don't re-add Flask-CORS.
- **WebSocket** — keep `cors_allowed_origins='*'` on `SocketIO(...)`.

---

## 7. Estimate
| Step | Effort |
|---|---|
| Migration SQL + backfill | small |
| Per-device state + ingest rewrite | medium |
| Read/CSV scoping + selector | medium |
| `/provision` + admin guard | small |
| Test with 2 devices end-to-end | small |

Net: a focused but contained change. The riskiest part is the ingest rewrite;
the legacy path keeps production safe throughout.
