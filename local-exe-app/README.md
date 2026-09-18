# LOCAL-EXE-APP — the thickness-local.exe (Windows offline appliance)

**What this is:** the self-contained Windows program
`local/dist/thickness-local.exe`. It runs the whole app offline on one PC
(no internet, no cloud): it starts the Flask/SocketIO server in the
background, opens the dashboard in the browser, and puts a tray icon in the
system tray. Everything is stored in local SQLite.

It is built from `backend/local_gui.py` by `local/thickness-local.spec`.

---

## Real files for this product (single source — edit in place)

| File | Why it matters |
|------|----------------|
| `backend/local_gui.py` | The code the .exe runs (windowed launcher, tray, browser open) |
| `backend/local_main.py` | The offline server logic the launcher reuses (LOCAL_MODE + SQLite) |
| `backend/local_db.py` | SQLite layer standing in for PostgreSQL in offline mode |
| `backend/local_license.py` | Offline Ed25519 license / activation (no internet needed) |
| `local/thickness-local.spec` | PyInstaller recipe that produces the .exe |
| `local/thickness-local.exe` | The built exe (gitignored — produced by the build below) |
| `backend/license_pubkey.txt` | Public key the exe needs to verify license cards |

---

## How to run it

**Double-click `RUN-EXE.cmd`** (in this folder). It launches the built exe if
present; if the exe was never built it falls back to running the same code
from source (`python backend/local_gui.py`).

You can also run the built exe directly:
```
local\dist\thickness-local.exe
```
or run it from source without building:
```
python backend\local_gui.py
```

> Running from source needs Python + the local requirements installed
> (`local/requirements-local.txt`). The exe needs nothing.

---

## How to build a new .exe

Run from the **repo root** (the shared backend + frontend live there):

```powershell
# 1. (FIRST TIME ONLY) create the build venv + install tools
python -m venv local\.build-venv-win
local\.build-venv-win\Scripts\pip install --upgrade pip pyinstaller
local\.build-venv-win\Scripts\pip install -r local\requirements-local.txt

# 2. Build the frontend in "localapp" mode (same-origin URLs, no cloud URL)
npx vite build --mode localapp        # writes to root dist/

# 3. Build the exe with PyInstaller (Windows, using local/.build-venv-win)
local\.build-venv-win\Scripts\pyinstaller --noconfirm local\thickness-local.spec
# Output: local\dist\thickness-local.exe
```

> **Important:** the localapp build overwrites `dist/` with the offline
> frontend. Run a plain `npm run build` again before any cloud/server deploy
> (see `server-app`).

---

## How the exe's backend is wired (shared code — do NOT fork)

The exe does **not** keep its own private copy of the code. It runs the exact
same backend files as the other products. The import chain is:

```
local\dist\thickness-local.exe
   └─ runs → backend\local_gui.py        (windowed launcher: tray, open browser, firewall)
               └─ imports → backend\local_main.py   (sets LOCAL_MODE, installs SQLite shim, starts server)
                              └─ imports → backend\merged_server.py   (the actual Flask app + all routes)
```

`backend/merged_server.py` is **the same file** the cloud/server app uses. It
switches behaviour purely from environment variables:
- `LOCAL_MODE=true`  → offline, local SQLite (`backend/local_db.py` shims `psycopg2`)
- `CLOUD_MODE=true`  → hosted, real PostgreSQL

So a single edit to a shared file fixes the same behaviour in **both** the exe
and the server version. We deliberately chose **not** to copy the code into this
folder — a fork would let fixes silently miss the other version (drift). Edit the
shared files at the repo root, then recompile the exe.

---

## Edit map — "where do I go to change X?"

Because the local app and the server app share the same core code, most edits
are made **once**, in the shared files, and affect every version:

| I want to change… | Edit here |
|-------------------|-----------|
| Any dashboard screen, chart, page (React) | `src/` at the repo root |
| Backend API behaviour / stream math | `backend/merged_server.py` |
| Something local-only (SQLite, activation, launcher) | `backend/local_*.py` above |
| The way the exe is packaged / its name / icon | `local/thickness-local.spec` |
| The app's look & feel / colours | `src/styles/global.css` |

There is **no separate copy** of the code in this folder on purpose — if there
were, a fix here would silently miss the server version. Edit the shared files,
then rebuild the exe with the steps above.
