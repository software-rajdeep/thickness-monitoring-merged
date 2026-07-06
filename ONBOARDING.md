# Customer Onboarding Runbook (Rajdeep internal)

How to take a new customer live, end to end. Each sale is ~5 minutes of our time.

## What the customer receives

1. **Thickness Agent installer** — `thickness-agent_<ver>_<arch>.deb`
   (build: clone the repo on a machine of the target architecture, `cd agent && ./build-deb.sh`.
   amd64 build exists on the Ubuntu PC at `~/agent-build/thickness-agent_1.0.0_amd64.deb`.
   For Raspberry Pi customers, build once on a Pi to get the arm64 .deb.)
2. **Activation key card** — printed by `tools/onboard_customer.py` (below).
3. **Dashboard login** — on the same key card (company + username + password).
4. **`CUSTOMER_QUICKSTART.md`** — the 4-step install sheet.

## Per-sale steps

```bash
# 1. Provision (creates customer + device + their company-admin login, prints key card)
#    Token: on the KVM — systemctl show merged -p Environment | tr ' ' '\n' | grep PROVISION
python3 tools/onboard_customer.py "Acme Steel" --mode opposite --devices 1 --out cards/

# 2. Send the customer: the .deb, the key card, CUSTOMER_QUICKSTART.md.

# 3. When they finish the wizard, verify data is flowing:
#    - dashboard: log in as them (or superadmin) and watch live values, or
#    - DB: SELECT count(*) FROM opposite_thickness_readings WHERE device_id='dev_xxx';
#    - devices.last_seen updates every POST.
```

Multiple stations/lines → `--devices N`; each device gets its own key card.
More dashboard users → the customer's admin creates them (Backend page), or we
POST `/auth/users` with their token.

## Revoking a customer/device (billing stop, lost key)

```sql
UPDATE devices SET revoked=true WHERE device_id='dev_xxx';
```
Ingest stops immediately (401). Un-revoke by setting false. A lost device key
cannot be recovered (only the hash is stored) — revoke and provision a new device.

## Support checks

| Symptom | Check |
|---|---|
| No live data | `systemctl status thickness-agent` on their box; `devices.last_seen` in DB |
| Agent can't reach server | Their LAN may hijack sslip.io DNS → use `http://194.164.148.145:8082` as SERVER_URL |
| Can't log in | Company name must match exactly (case-insensitive); users are per-company |
| Wrong/missing thickness | They must calibrate from the dashboard (per-device calibration) |

## Current capacity constraints (before ~10 customers)

- **KVM disk: 48 GB total, ~12 GB free.** At 5 Hz a device writes ~432k rows/day/table.
  `PER_DEVICE_ROW_CAP` (merged.service env) must be set so that
  `devices × 4 tables × cap × ~110 B` fits the disk. 850,000 ≈ 2 days of 5 Hz
  history per device ≈ ~0.4 GB/device — safe for 10–15 devices.
  **Expand the KVM disk (or add a volume) before promising 7-day history.**
- Nightly DB backup: `/etc/cron.d/sensor-db-backup` → `/root/db_backups/` (7 kept).
- One shared server = one blast radius. Fine for 10 customers; before 50,
  add monitoring/alerting on `merged.service`, disk, and per-device last_seen.
