# Go-Live Plan — Thickness Monitoring SaaS (10 Customers)

State as of 2026-07-06. The product is functionally multi-tenant and tested;
what remains is one deploy command, a handful of capacity/ops items, and
per-customer logistics. Items marked **[YOU]** need a human decision, money,
or physical access; everything else Claude can execute remotely.

---

## Phase 0 — Flip the switch (blocked only on your "deploy"; ~15 min)

The security lockdown + onboarding kit is finished, smoke-tested (19/19
against the live DB on a side port), and staged on branch `golive-lockdown`.
Backups are already taken.

- [ ] Run `finalize_golive.py` — merges to main (Vercel redeploys), deploys
      backend to KVM, restarts `merged.service`, sets
      `PER_DEVICE_ROW_CAP=850000` (~2 days of 5 Hz history per device),
      installs nightly `pg_dump` cron, rotates the global superadmin password
      (printed once), smoke-tests production over HTTPS.
- [ ] Manual sanity pass in a browser: demo login (company "Demo Customer",
      admin / demo1234) → dashboard, user management, CSV download.
- [ ] Record the new superadmin password + `PROVISION_ADMIN_TOKEN` somewhere
      safe (password manager, not the repo). **[YOU]**

**Rollback:** `.bak-prelockdown` files on the KVM + `systemctl restart merged`;
Vercel keeps every previous deployment one click away.

---

## Phase 1 — Before onboarding 10 customers (~2–4 days)

### 1. Disk / retention (the one real capacity blocker)
KVM has 48 GB, ~12 GB free. At 5 Hz one device ≈ 432k rows/day/table.
- With the 2-day cap now being set: ~0.4 GB/device → 10–15 devices fit. OK to launch.
- To promise 7-day raw history (the better product): expand the KVM disk to
  ~100 GB at the hosting provider. **[YOU — hosting panel/billing]**
- Alternative if disk can't grow: build the 5 Hz→1-per-minute rollup job so
  long history is cheap (Claude can build this; ~a day of work).

### 2. Agent installers per customer platform
- x86-64 Linux `.deb`: **done** (`~/agent-build/thickness-agent_1.0.0_amd64.deb`
  on the Ubuntu PC; rebuildable anywhere with `agent/build-deb.sh`).
- Raspberry Pi (arm64): run `./build-deb.sh` once **on a Pi**. **[YOU — need the Pi]**
- Windows agent (if any customer's PC is Windows): PyInstaller build of the
  same `thickness_agent.py` + NSSM/scheduled-task service wrapper. Only build
  if a customer actually needs it (~half a day).
- Decide + record per-customer hardware requirements (their PC/Pi must be on
  the same LAN as the CD22 sensors, always-on, internet access outbound only).

### 3. Ubuntu PC back online **[YOU — power it on]**
- Reconcile its repo (check for unpushed/uncommitted work, then `git pull`).
- Confirm `thickness-agent` resumes streaming when sensors are reconnected —
  this is also your live demo rig for sales.

### 4. Onboarding dry run (Claude, ~30 min)
- Provision a fake customer with `tools/onboard_customer.py`, walk the entire
  key-card → activate → ingest → dashboard → CSV path, then revoke + clean up.
  Proves the exact flow a real customer will follow, post-lockdown.

### 5. Small fixes surfaced by the lockdown
- `sensor_setup.html` (internal sensor-config tool) now needs an admin token
  for `/config/network` — give it a token prompt or serve it behind login.
  Internal-only impact; not customer-blocking.
- Verify the old Electron desktop build (if anyone still uses it) against the
  gated endpoints; retire it or rebuild from current main.

---

## Phase 2 — Operational maturity (first 2 weeks of live customers)

### 6. Monitoring & alerting (highest value; reuses existing email plumbing)
- Alert when a paying customer's device goes silent (`devices.last_seen`
  older than N minutes) — you find out before the customer calls.
- Disk usage + `merged.service` health alert on the KVM.
- Daily one-line status email: devices online, rows/day, disk free.

### 7. Real domain **[YOU — buy domain, ~$12/yr]**
- e.g. `app.rajdeepanalytics.com` (Vercel) + `api.rajdeepanalytics.com`
  (points at the KVM, Traefik gets a proper cert).
- Fixes the sslip.io DNS-hijack problem seen on sensor LANs, looks
  professional on the key card, and decouples you from the KVM's IP.

### 8. Provisioning UI
- Superadmin-only "Customers" page in the dashboard: add customer, add device,
  print key card, revoke, see last-seen. Replaces the CLI script for daily use.

### 9. Security round 2
- Rate-limit `/auth/login` and `/ingest/readings` (brute-force/flood guard).
- Socket.io `join_device` should validate the token owns the room (device IDs
  are unguessable today, but belt-and-braces).
- Remove legacy `/login` once nothing uses it.
- Move KVM/DB passwords out of CLAUDE.md/repo into a secrets store; rotate
  KVM root password. **[YOU — new passwords]**

### 10. Support & commercial **[YOU]**
- Pricing per device/month, contract/terms, support SLA (the runbook +
  quick-start docs are already written).
- A support email/phone on the key card (currently aadit@rajdeepanalytics.com).

---

## Phase 3 — Scaling 10 → 50 customers (do when growth demands)

- **Managed/backed-up PostgreSQL** or a second disk; offsite backup copies
  (nightly dumps currently stay on the same machine).
- **Data rollups** (5 Hz → 1/min after 7 days) for months of history.
- **Staging environment** + GitHub Actions CI (build frontend, syntax-check
  backend, run the smoke suite on every push) — removes the "deploy and pray"
  risk entirely.
- **Agent auto-update channel** (agents poll for new versions).
- **Bigger/second KVM or move to a cloud with snapshots**; the single server
  is a single blast radius — fine at 10, not at 50.
- Android app (spec already drafted in `ANDROID_APP_SPEC.md`) if customers
  want mobile dashboards.

---

## Standing risks / discipline

- **One repo rule:** all git work from fresh clones of `origin/main` (the
  Windows clone and Ubuntu checkout have both gone stale before). Never leave
  ad-hoc upload scripts in the repo root; never deploy without checking
  `git fetch` first (a concurrent dev session has overwritten main once).
- **Per-sale flow (once live):** `onboard_customer.py "Name"` → send .deb +
  key card + CUSTOMER_QUICKSTART.md → watch `last_seen` go green. ~5 minutes.
