# LOCAL-BACKEND-APP — run the offline local version from the backend files

**What this is:** the same offline appliance as `local-exe-app`, but run the
plain way — as a normal console/server process instead of a windowed exe.

Two flavours share this folder:
- **From source (dev / testing):** `python backend/local_main.py` — what you
  do while developing on your PC.
- **As the Linux appliance (.deb):** `local/build-local-deb.sh` packages
  `backend/local_main.py` into a `thickness-local` systemd service for the
  customer's offline box (the `thickness-local.service` unit in `local/`).

There is **no GUI/tray here** — the server runs in the foreground (or under
systemd) and you open the dashboard in a browser. Data goes to local SQLite.
Everything is gated behind `LOCAL_MODE=true`.

---

## Real files for this product (single source — edit in place)

| File | Why it matters |
|------|----------------|
| `backend/local_main.py` | Entry point — sets LOCAL_MODE, SQLite shim, then starts the server |
| `backend/local_db.py` | SQLite layer standing in for PostgreSQL |
| `backend/local_license.py` | Offline license / activation check |
| `backend/local_gui.py` | The windowed wrapper (used only by the exe build) |
| `backend/merged_server.py` | The actual Flask server (shared with the server version!) |
| `local/build-local-deb.sh` | Builds the shippable Linux `.deb` appliance |
| `local/thickness-local.service` | systemd unit used by the .deb |
| `local/packaging/` | .deb metadata (control, postinst, prerm) |
| `local/requirements-local.txt` | Python deps needed to run from source |

---

## How to run it from source

**Double-click `RUN-SOURCE.cmd`** (Windows) — equivalent to:

```bash
python backend/local_main.py
```

On Linux (the appliance build machine):
```bash
python3 backend/local_main.py
```

Then open the dashboard in a browser. Default port is **5002**
(`http://localhost:5002`) unless overridden by the `SERVER_PORT` env var.
The data directory is `C:\ProgramData\ThicknessLocal` on Windows /
`/var/lib/thickness-local` on Linux.

> Needs Python + deps from `local/requirements-local.txt`. For a full offline
> test you also need the frontend served locally: the exe/.deb bundle the
> frontend, but running bare from source serves the SPA from `dist/`, so build
> it once with `npx vite build --mode localapp` (offline/same-origin build).

---

## How to build the Linux .deb (the shipped offline appliance)

Run on the target architecture machine:
```bash
cd local
./build-local-deb.sh 1.0.0     # builds frontend (localapp) + binary + .deb
sudo dpkg -i thickness-local_1.0.0_<arch>.deb
```
Output: `local/thickness-local_<version>_<arch>.deb` — installs and enables
`thickness-local.service`.

> As with the exe, the localapp frontend build overwrites `dist/`. Run a plain
> `npm run build` again before a cloud deploy.

---

## Edit map — "where do I go to change X?"

| I want to change… | Edit here |
|-------------------|-----------|
| Any dashboard screen / chart / page (React) | `src/` at the repo root |
| Backend logic, stream loop, thickness math | `backend/merged_server.py` |
| Local-only startup / data dir / SQLite / licensing | `backend/local_main.py`, `backend/local_db.py`, `backend/local_license.py` |
| The .deb packaging (postinst secret, service file) | `local/packaging/`, `local/thickness-local.service` |
| App look & feel | `src/styles/global.css` |

The backend logic you edit here is **the same file** the server version uses
(`backend/merged_server.py`) — it picks local vs cloud behaviour from
`LOCAL_MODE` / `CLOUD_MODE`. So one edit fixes the same behaviour everywhere.
