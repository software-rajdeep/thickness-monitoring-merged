"""
local_db — SQLite drop-in replacement for psycopg2, used ONLY by the local
appliance build (thickness-local .deb).

The local entrypoint (local_main.py) installs this module as `psycopg2` in
sys.modules BEFORE merged_server / download_routes / email_alert_routes are
imported, so every `psycopg2.connect(...)` in the existing codebase
transparently opens the local SQLite database instead. The cloud deployment
never imports this file.

What it does:
  * Translates the Postgres-isms in the existing SQL to SQLite
    (%s placeholders, SERIAL, TIMESTAMPTZ, DOUBLE PRECISION, JSONB, NOW(),
    IS NOT DISTINCT FROM). Postgres-only maintenance statements
    (information_schema probes, sequences) are stubbed out.
  * Creates the FULL current schema on first connect — including the
    multi-tenant columns (device_id on reading tables; email/customer_id on
    users) that exist on the cloud DB but not in init_db()'s legacy DDL.
  * Seeds a global service superadmin (for Rajdeep technicians) with a random
    password written root-only to <data_dir>/service_login.txt.

DB file: $THICKNESS_DB_PATH, else <$THICKNESS_DATA_DIR>/thickness_local.db,
else ./thickness_local.db next to this file.
"""
import datetime
import os
import re
import secrets
import sqlite3
import threading
import types

# --- timestamp round-tripping -------------------------------------------------
# Store datetimes as ISO-8601 ('T' separator, matches datetime.isoformat() used
# by the query parameters elsewhere in the app); parse TIMESTAMP columns back
# into datetime objects (the app calls .isoformat() on fetched values).
sqlite3.register_adapter(datetime.datetime, lambda dt: dt.isoformat())
sqlite3.register_adapter(datetime.date, lambda d: d.isoformat())


def _convert_timestamp(raw):
    text = raw.decode() if isinstance(raw, bytes) else raw
    try:
        return datetime.datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return text


sqlite3.register_converter("TIMESTAMP", _convert_timestamp)
sqlite3.register_converter("TIMESTAMPTZ", _convert_timestamp)

# --- psycopg2 API surface -----------------------------------------------------
Error = sqlite3.Error
DatabaseError = sqlite3.DatabaseError
OperationalError = sqlite3.OperationalError
IntegrityError = sqlite3.IntegrityError
ProgrammingError = sqlite3.ProgrammingError

# `from psycopg2 import extras` — only imported by the app, never used, but the
# import must succeed. Real module object so sys.modules registration is clean.
extras = types.ModuleType("psycopg2.extras")
extras.RealDictCursor = None
extras.DictCursor = None

# --- SQL translation ----------------------------------------------------------
_SQL_SUBS = [
    (re.compile(r"\bSERIAL\s+PRIMARY\s+KEY\b", re.I), "INTEGER PRIMARY KEY AUTOINCREMENT"),
    (re.compile(r"\bTIMESTAMPTZ\b", re.I), "TIMESTAMP"),
    (re.compile(r"\bDOUBLE\s+PRECISION\b", re.I), "REAL"),
    (re.compile(r"\bJSONB\b", re.I), "TEXT"),
    (re.compile(r"\bNOW\(\)", re.I), "(datetime('now','localtime'))"),
    (re.compile(r"\bIS\s+NOT\s+DISTINCT\s+FROM\b", re.I), "IS"),
]

# Statements that only make sense on Postgres — silently succeed with no rows.
_NOOP_MARKERS = ("create sequence", "setval(", "nextval(", "alter table")


def _translate(sql):
    for rx, replacement in _SQL_SUBS:
        sql = rx.sub(replacement, sql)
    return sql.replace("%s", "?")


class _Cursor:
    def __init__(self, cur):
        self._cur = cur
        self._stub_rows = None   # set when a statement was stubbed out

    def execute(self, sql, params=None):
        low = sql.lower()
        if "information_schema" in low:
            # init_db()'s "does the id column have a default?" migration probe.
            # Answer with a non-NULL default so the sequence migration is skipped.
            self._stub_rows = [("AUTOINCREMENT",)]
            return
        if any(m in low for m in _NOOP_MARKERS):
            self._stub_rows = []
            return
        self._stub_rows = None
        if params is None:
            return self._cur.execute(_translate(sql))
        return self._cur.execute(_translate(sql), tuple(params))

    def fetchone(self):
        if self._stub_rows is not None:
            return self._stub_rows[0] if self._stub_rows else None
        return self._cur.fetchone()

    def fetchall(self):
        if self._stub_rows is not None:
            return list(self._stub_rows)
        return self._cur.fetchall()

    def fetchmany(self, size=None):
        if self._stub_rows is not None:
            return list(self._stub_rows)
        return self._cur.fetchmany(size) if size else self._cur.fetchmany()

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def description(self):
        return self._cur.description

    def close(self):
        self._cur.close()

    def __iter__(self):
        return iter(self._cur)


class _Connection:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self, *args, **kwargs):
        return _Cursor(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


# --- schema -------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT UNIQUE NOT NULL,
    customer_id INTEGER,
    device_key_hash TEXT,
    sensor_mode TEXT DEFAULT 'opposite',
    label TEXT,
    revoked INTEGER DEFAULT 0,
    last_seen TIMESTAMP,
    calibration TEXT,
    created_at TIMESTAMP DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    email TEXT,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'worker',
    customer_id INTEGER,
    created_at TIMESTAMP DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS user_calibrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    calibration_json TEXT,
    updated_at TIMESTAMP DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS sensor_filtered_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT (datetime('now','localtime')),
    sensor_a REAL, sensor_b REAL, sensor_c REAL,
    device_id TEXT DEFAULT 'dev_legacy'
);
CREATE TABLE IF NOT EXISTS sensor_unfiltered_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT (datetime('now','localtime')),
    sensor_a REAL, sensor_b REAL, sensor_c REAL,
    device_id TEXT DEFAULT 'dev_legacy'
);
CREATE TABLE IF NOT EXISTS opposite_thickness_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT (datetime('now','localtime')),
    sensor_a REAL, sensor_b REAL, thickness REAL,
    device_id TEXT DEFAULT 'dev_legacy'
);
CREATE TABLE IF NOT EXISTS opposite_thickness_raw_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT (datetime('now','localtime')),
    sensor_a REAL, sensor_b REAL, thickness REAL,
    device_id TEXT DEFAULT 'dev_legacy'
);
CREATE INDEX IF NOT EXISTS idx_filtered_timestamp ON sensor_filtered_readings (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_unfiltered_timestamp ON sensor_unfiltered_readings (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_thickness_timestamp ON opposite_thickness_readings (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_thickness_raw_timestamp ON opposite_thickness_raw_readings (timestamp DESC);
"""

_init_lock = threading.Lock()
_initialized = False


def db_path():
    override = os.environ.get("THICKNESS_DB_PATH")
    if override:
        return override
    data_dir = os.environ.get("THICKNESS_DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(data_dir, "thickness_local.db")


def _seed_service_account(conn):
    """Global (blank-company) superadmin for Rajdeep technicians. Password is
    generated once and written to service_login.txt in the data dir (0600) —
    readable over SSH by whoever administers the box, never printed to the UI."""
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users WHERE role='superadmin' AND customer_id IS NULL")
    if cur.fetchone()[0]:
        return
    from werkzeug.security import generate_password_hash
    password = secrets.token_urlsafe(12)
    cur.execute(
        "INSERT INTO users (username, email, password_hash, role, customer_id) VALUES (?,?,?,?,NULL)",
        ("superadmin", None, generate_password_hash(password), "superadmin"))
    conn.commit()
    note = os.path.join(os.path.dirname(db_path()), "service_login.txt")
    with open(note, "w") as f:
        f.write("Thickness Local — service login (Rajdeep technicians only)\n"
                "Company : (leave blank)\n"
                "Username: superadmin\n"
                f"Password: {password}\n")
    try:
        os.chmod(note, 0o600)
    except OSError:
        pass


def _ensure_schema():
    global _initialized
    with _init_lock:
        if _initialized:
            return
        path = db_path()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        conn = sqlite3.connect(path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            conn.commit()
            _seed_service_account(conn)
        finally:
            conn.close()
        _initialized = True


def connect(*args, **kwargs):
    """psycopg2.connect(host=..., database=..., user=..., password=...) shim.
    All connection kwargs are ignored — there is exactly one local database."""
    _ensure_schema()
    conn = sqlite3.connect(db_path(), detect_types=sqlite3.PARSE_DECLTYPES, timeout=10.0)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return _Connection(conn)
