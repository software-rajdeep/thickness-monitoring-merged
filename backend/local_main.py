"""
Entrypoint for the LOCAL APPLIANCE build (thickness-local).

This is what the PyInstaller binary runs. It must execute BEFORE any of the
app modules are imported:

  1. Set the appliance environment defaults (LOCAL_MODE, data dir, bundled
     frontend path, port).
  2. Install the SQLite shim under the name `psycopg2` so merged_server,
     download_routes and email_alert_routes all use the local database with
     zero code changes.

Run from source for development/testing:
    THICKNESS_DATA_DIR=/tmp/thickness python3 backend/local_main.py
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- appliance environment defaults (real env vars always win) ---------------
os.environ.setdefault("LOCAL_MODE", "true")
os.environ.setdefault("CLOUD_MODE", "false")

if getattr(sys, "frozen", False):
    # PyInstaller onefile: the frontend dist is unpacked inside the bundle.
    os.environ.setdefault("FRONTEND_DIST", os.path.join(sys._MEIPASS, "dist"))

if sys.platform.startswith("win"):
    _default_data = os.path.join(os.environ.get("ProgramData", "."), "ThicknessLocal")
else:
    _default_data = "/var/lib/thickness-local"
os.environ.setdefault("THICKNESS_DATA_DIR", _default_data)
os.makedirs(os.environ["THICKNESS_DATA_DIR"], exist_ok=True)

# --- SQLite shim: must be installed before merged_server is imported ---------
sys.path.insert(0, BASE_DIR)
import local_db  # noqa: E402
sys.modules["psycopg2"] = local_db
sys.modules["psycopg2.extras"] = local_db.extras

import merged_server  # noqa: E402

if __name__ == "__main__":
    merged_server.main()
