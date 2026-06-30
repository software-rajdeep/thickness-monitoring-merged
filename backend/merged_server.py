"""
MERGED BACKEND SERVER
Supports both Side-by-Side (3 sensors) and Opposite-Side (2 sensors) modes.
Single server on port 5000 - run once, supports all frontend modes.
"""
import socket
import time
import datetime
import threading
import psycopg2
import json
import os
import csv
import io
import re
import secrets
from functools import wraps
from collections import deque
from psycopg2 import extras
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from flask import Flask, request, jsonify, send_from_directory, g
# flask_cors removed — CORS handled exclusively by Traefik to avoid duplicate headers
from flask_socketio import SocketIO, join_room
from flask import Response

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# ==========================================
# CONFIGURATION
# ==========================================
# --- NETWORK CONFIG ---
SENSOR_CONFIGS = {}

SENSOR_TIMEOUT = 2.0
SERVER_IP = '0.0.0.0'
SERVER_PORT = int(os.environ.get("SERVER_PORT", "5002"))
CLOUD_MODE = os.environ.get("CLOUD_MODE", "false").lower() == "true"
INGEST_API_KEY = os.environ.get("INGEST_API_KEY", "merged-secret-2026")

# --- DATABASE CONFIG ---
DB_HOST = "localhost"
DB_NAME = "sensor_db"
DB_USER = "rapl"
DB_PASS = "rapl2026"
DB_TABLE_FILTERED = "sensor_filtered_readings"
DB_TABLE_UNFILTERED = "sensor_unfiltered_readings"
DB_TABLE_THICKNESS = "opposite_thickness_readings"
DB_TABLE_THICKNESS_RAW = "opposite_thickness_raw_readings"
DB_TABLE_USERS = "users"
DB_TABLE_USER_CALIBRATIONS = "user_calibrations"

LIMIT_FILTERED = 10_000_000
LIMIT_UNFILTERED = 1_000_000
LIMIT_THICKNESS = 10_000_000
LIMIT_THICKNESS_RAW = 1_000_000

# --- MULTI-TENANT (SaaS) ---
# Per-device row cap (applies per device_id, not per table). ~3M rows ≈ 7 days at
# 5 Hz. Env-overridable so we can raise it per deployment without a code change.
PER_DEVICE_ROW_CAP = int(os.environ.get("PER_DEVICE_ROW_CAP", "3000000"))
# Admin token guarding /provision (device minting). Override in the systemd unit.
PROVISION_ADMIN_TOKEN = os.environ.get("PROVISION_ADMIN_TOKEN", "rajdeep-admin-2026")
# device_id used for the original single-tenant install (legacy ingest path).
LEGACY_DEVICE_ID = "dev_legacy"

# --- AUTH (signed session tokens) ---
# Secret signing key for login tokens. MUST be overridden in the systemd unit.
AUTH_SECRET = os.environ.get("AUTH_SECRET", "dev-insecure-change-me")
AUTH_TOKEN_TTL = int(os.environ.get("AUTH_TOKEN_TTL", str(7 * 24 * 3600)))  # 7 days
# Password for the seeded superadmin on a FRESH database (generated if unset).
SUPERADMIN_PASSWORD = os.environ.get("SUPERADMIN_PASSWORD")

# Moving-average window for the "filtered" tables. At 5 Hz, 10 samples ≈ 2 s of
# smoothing. The unfiltered/raw tables always store the instantaneous value;
# the filtered tables store the rolling average of the last FILTER_WINDOW samples.
FILTER_WINDOW = 10

# --- FILE CONFIG ---
CONFIG_FILE_PATH = os.path.join(BASE_DIR, "sensor_config.json")
NETWORK_CONFIG_FILE_PATH = os.path.join(BASE_DIR, "sensor_network.json")
THICKNESS_STATE_FILE_PATH = os.path.join(BASE_DIR, "thickness_state.json")
THICKNESS_LIMIT_FILE_PATH = os.path.join(BASE_DIR, "thickness_limit.json")

# --- ZERO OFFSET ---
ZERO_OFFSET_MM = 35.0

STX = 0x02
ETX = 0x03

# ==========================================
# CONFIG FILE HELPERS
# ==========================================
def init_config_file():
    """Creates an empty sensor_config.json if one doesn't exist."""
    try:
        if not os.path.exists(CONFIG_FILE_PATH):
            print("--- Creating empty sensor_config.json ---")
            print("  WARNING: sensor_config.json not found. Created empty file.")
            print("  You MUST configure your sensors in sensor_network.json (see README).")
            with open(CONFIG_FILE_PATH, 'w') as f:
                json.dump({}, f, indent=4)
    except Exception as e:
        print(f"Warning: Could not init config file: {e}")

def init_network_config_file():
    """Creates a sample sensor_network.json if one doesn't exist."""
    try:
        if not os.path.exists(NETWORK_CONFIG_FILE_PATH):
            print("--- Creating sample sensor_network.json ---")
            print("  WARNING: sensor_network.json not found.")
            print("  Please edit this file with your sensor IP addresses.")
            sample_config = {
                "A": {"ip": "192.168.1.200", "port": 8234, "name": "Sensor A", "sensor_type": "cd22"},
                "B": {"ip": "192.168.1.201", "port": 8234, "name": "Sensor B", "sensor_type": "cd22"},
            }
            with open(NETWORK_CONFIG_FILE_PATH, 'w') as f:
                json.dump(sample_config, f, indent=4)
            print(f"  Created sample file at: {NETWORK_CONFIG_FILE_PATH}")
            print("  Edit it to match your actual sensor network before starting the server.")
    except Exception as e:
        print(f"Warning: Could not init network config file: {e}")

def normalize_network_config(payload, base_config=None):
    if base_config is None:
        base_config = SENSOR_CONFIGS
    normalized = {}
    for sid, defaults in base.items():
        merged = {}
        for key in defaults:
            merged[key] = False if key == "reconnect" else defaults[key]
        if sid in payload:
            entry = payload[sid]
            for key in defaults:
                if key == "reconnect":
                    merged[key] = bool(entry.get(key, defaults[key]))
                    continue
                merged[key] = entry.get(key, defaults[key])
        ip = str(entry.get("ip", defaults["ip"])).strip()
        try:
            port = int(entry.get("port", defaults["port"]))
        except (TypeError, ValueError):
            port = defaults["port"]
        name = str(entry.get("name", defaults["name"])).strip() or defaults["name"]
        merged["ip"] = ip
        merged["port"] = port
        merged["name"] = name
        normalized[sid] = merged
    return normalized

def load_network_config():
    """Load network config from file. Returns {} on failure."""
    try:
        with open(NETWORK_CONFIG_FILE_PATH, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_network_config(config):
    """Save network config to file."""
    try:
        with open(NETWORK_CONFIG_FILE_PATH, 'w') as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        print(f"Warning: Could not save network config: {e}")

def rebuild_active_sensors():
    global active_sensors_map
    with sensors_lock:
        for sensor_id, sensor in list(active_sensors_map.items()):
            try:
                sensor.disconnect()
            except Exception:
                pass
        active_sensors_map.clear()
        config = load_network_config()
        if not config:
            print("  [Config] No sensor_network.json found or file is empty. No sensors activated.")
            print("  [Config] Create sensor_network.json with your sensor configurations.")
            return
        for sensor_id, cfg in config.items():
            sid_upper = sensor_id.upper()
            if cfg.get("reconnect", True) is False:
                print(f"  [Config] Sensor {sid_upper} disabled (reconnect=false) — skipping")
                continue
            sensor = CD22Sensor(cfg["ip"], cfg["port"], cfg.get("name", f"Sensor {sid_upper}"))
            active_sensors_map[sid_upper] = sensor
            print(f"  [Config] Sensor {sid_upper} @ {cfg['ip']}:{cfg['port']}")

def refresh_sensor_configs(new_config=None):
    """Update SENSOR_CONFIGS from file or provided dict; rebuild active sensors."""
    global SENSOR_CONFIGS
    if new_config:
        SENSOR_CONFIGS.update(new_config)
        save_network_config(SENSOR_CONFIGS)
    else:
        SENSOR_CONFIGS = load_network_config()
        if not SENSOR_CONFIGS:
            print("  [Config] WARNING: sensor_network.json is missing or empty.")
            print("  [Config] Create sensor_network.json with your sensor configurations.")
    rebuild_active_sensors()

# ==========================================
# THICKNESS STATE HELPERS
# ==========================================
def default_thickness_state():
    return {
        "setup_ready": False,
        "captured_at": None,
        "reference_readings": {"A": None, "B": None},
        "calibration_completed": False,
        "calibration_active": False,
        "calibration_captured_at": None,
        "calibration_reference_thickness": 0.0,
        "calibration_baseline_readings": {"A": None, "B": None},
        "gap_distance": 0.0,
        "auto_gap_active": False,
        "object_thickness": None,
        "thickness_tolerance_min": None,
        "thickness_tolerance_max": None,
    }

def normalize_sensor_readings(raw_readings):
    return {k: round(float(v), 3) if v is not None else None for k, v in raw_readings.items()}

def load_thickness_state():
    """Load global thickness state from JSON file."""
    if not os.path.exists(THICKNESS_STATE_FILE_PATH):
        return default_thickness_state()
    try:
        with open(THICKNESS_STATE_FILE_PATH, 'r') as file_handle:
            loaded_state = json.load(file_handle)
    except (json.JSONDecodeError, Exception):
        return default_thickness_state()
    state = default_thickness_state()
    state["setup_ready"] = bool(loaded_state.get("setup_ready", False))
    state["captured_at"] = loaded_state.get("captured_at")
    state["reference_readings"] = normalize_sensor_readings(loaded_state.get("reference_readings", {}))
    state["calibration_completed"] = bool(
        loaded_state.get("calibration_completed", loaded_state.get("calibration_active", False))
    )
    state["calibration_active"] = state["calibration_completed"]
    state["calibration_captured_at"] = loaded_state.get("calibration_captured_at")
    reference_thickness = loaded_state.get("calibration_reference_thickness", 0.0)
    try:
        state["calibration_reference_thickness"] = float(reference_thickness)
    except (TypeError, ValueError):
        state["calibration_reference_thickness"] = 0.0
    state["calibration_baseline_readings"] = normalize_sensor_readings(
        loaded_state.get("calibration_baseline_readings", {})
    )
    state["gap_distance"] = float(loaded_state.get("gap_distance", 0.0))
    state["auto_gap_active"] = bool(loaded_state.get("auto_gap_active", False))
    state["object_thickness"] = loaded_state.get("object_thickness")
    state["thickness_tolerance_min"] = loaded_state.get("thickness_tolerance_min")
    state["thickness_tolerance_max"] = loaded_state.get("thickness_tolerance_max")
    return state

def save_thickness_state(state):
    """Save global thickness state to JSON file."""
    with open(THICKNESS_STATE_FILE_PATH, 'w') as file_handle:
        json.dump(state, file_handle, indent=4)

def init_thickness_state_file():
    if not os.path.exists(THICKNESS_STATE_FILE_PATH):
        save_thickness_state(default_thickness_state())

# --- THICKNESS LIMIT (global, shared across all users) ---
def default_thickness_limit():
    return {"active": False, "min": "", "max": ""}

def load_thickness_limit():
    """Load the global thickness limit from JSON file."""
    if not os.path.exists(THICKNESS_LIMIT_FILE_PATH):
        return default_thickness_limit()
    try:
        with open(THICKNESS_LIMIT_FILE_PATH, 'r') as file_handle:
            loaded = json.load(file_handle)
    except (json.JSONDecodeError, Exception):
        return default_thickness_limit()
    limit = default_thickness_limit()
    limit["active"] = bool(loaded.get("active", False))
    raw_min = loaded.get("min", "")
    raw_max = loaded.get("max", "")
    limit["min"] = "" if raw_min is None else str(raw_min)
    limit["max"] = "" if raw_max is None else str(raw_max)
    return limit

def save_thickness_limit(limit):
    """Save the global thickness limit to JSON file."""
    with open(THICKNESS_LIMIT_FILE_PATH, 'w') as file_handle:
        json.dump(limit, file_handle, indent=4)

def init_thickness_limit_file():
    if not os.path.exists(THICKNESS_LIMIT_FILE_PATH):
        save_thickness_limit(default_thickness_limit())

def get_thickness_limit():
    global thickness_limit
    return thickness_limit

def set_thickness_limit(new_limit):
    global thickness_limit
    thickness_limit = new_limit
    save_thickness_limit(new_limit)

def get_thickness_state():
    global thickness_state
    return thickness_state

def set_thickness_state(new_state):
    global thickness_state
    thickness_state = new_state
    save_thickness_state(new_state)

def capture_starting_readings():
    failures = []
    with sensors_lock:
        captured = {}
        for sid in sorted(active_sensors_map.keys()):
            sensor = active_sensors_map[sid]
            reading = sensor.get_single_measurement()
            if reading is not None:
                captured[sid] = round(float(reading), 3)
            else:
                failures.append(sid)
        if not captured:
            return None, failures
        updated_state = default_thickness_state()
        updated_state["setup_ready"] = True
        updated_state["captured_at"] = datetime.datetime.now().isoformat()
        for sid, val in captured.items():
            updated_state["reference_readings"][sid] = val
        set_thickness_state(updated_state)
        return captured, failures

def capture_active_sensor_readings():
    failures = []
    with sensors_lock:
        captured = {}
        for sid in sorted(active_sensors_map.keys()):
            sensor = active_sensors_map[sid]
            reading = sensor.get_single_measurement()
            if reading is not None:
                captured[sid] = round(float(reading), 3)
            else:
                failures.append(sid)
        if not captured:
            return None, failures
        return captured, failures

def capture_calibration(reference_thickness):
    failures = []
    with sensors_lock:
        captured = {}
        for sid in sorted(active_sensors_map.keys()):
            sensor = active_sensors_map[sid]
            reading = sensor.get_single_measurement()
            if reading is not None:
                captured[sid] = round(float(reading), 3)
            else:
                failures.append(sid)
        if not captured:
            return None, failures
        current_state = get_thickness_state()
        updated_state = default_thickness_state()
        updated_state["setup_ready"] = current_state.get("setup_ready", False)
        updated_state["captured_at"] = current_state.get("captured_at")
        updated_state["reference_readings"] = normalize_sensor_readings(current_state.get("reference_readings", {}))
        updated_state["calibration_completed"] = True
        updated_state["calibration_active"] = True
        updated_state["calibration_captured_at"] = datetime.datetime.now().isoformat()
        updated_state["calibration_reference_thickness"] = round(float(reference_thickness), 3)
        for sensor_id, reading_val in captured.items():
            updated_state["calibration_baseline_readings"][sensor_id] = reading_val
        set_thickness_state(updated_state)
        return captured, failures

def reset_calibration_state():
    current_state = get_thickness_state()
    updated_state = default_thickness_state()
    updated_state["setup_ready"] = current_state.get("setup_ready", False)
    updated_state["captured_at"] = current_state.get("captured_at")
    updated_state["reference_readings"] = normalize_sensor_readings(current_state.get("reference_readings", {}))
    set_thickness_state(updated_state)
    return updated_state

def calculate_opposite_thickness(dist_A, dist_B):
    if dist_A is None or dist_B is None:
        return None
    try:
        a = float(dist_A)
        b = float(dist_B)
        state = get_thickness_state()
        gap = state.get("gap_distance", 0.0)
        if gap <= 0:
            return None
        thickness = gap - (ZERO_OFFSET_MM + a) - (ZERO_OFFSET_MM + b)
        return round(thickness, 3)
    except (TypeError, ValueError):
        return None

def calculate_thickness(sensor_id, current_reading):
    if current_reading is None:
        return None
    try:
        current = float(current_reading)
    except (TypeError, ValueError):
        return None
    state = get_thickness_state()
    if not state.get("calibration_active", False):
        return round(current, 3)
    baselines = state.get("calibration_baseline_readings", {})
    baseline = baselines.get(sensor_id)
    if baseline is None:
        return round(current, 3)
    try:
        baseline = float(baseline)
    except (TypeError, ValueError):
        return round(current, 3)
    reference = state.get("calibration_reference_thickness", 0.0)
    try:
        reference = float(reference)
    except (TypeError, ValueError):
        reference = 0.0
    offset = current - baseline
    return round(reference + offset, 3)

# ==========================================
# DATABASE HELPERS
# ==========================================
def init_db():
    """Create database tables if they don't exist."""
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
        cur = conn.cursor()

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {DB_TABLE_FILTERED} (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMPTZ DEFAULT NOW(),
                sensor_A DOUBLE PRECISION,
                sensor_B DOUBLE PRECISION,
                sensor_C DOUBLE PRECISION
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {DB_TABLE_UNFILTERED} (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMPTZ DEFAULT NOW(),
                sensor_A DOUBLE PRECISION,
                sensor_B DOUBLE PRECISION,
                sensor_C DOUBLE PRECISION
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {DB_TABLE_THICKNESS} (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMPTZ DEFAULT NOW(),
                sensor_A_distance DOUBLE PRECISION,
                sensor_B_distance DOUBLE PRECISION,
                thickness DOUBLE PRECISION
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {DB_TABLE_THICKNESS_RAW} (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMPTZ DEFAULT NOW(),
                sensor_A_distance DOUBLE PRECISION,
                sensor_B_distance DOUBLE PRECISION,
                computed_thickness DOUBLE PRECISION
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {DB_TABLE_USERS} (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                role VARCHAR(20) NOT NULL DEFAULT 'worker',
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {DB_TABLE_USER_CALIBRATIONS} (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                calibration_json JSONB,
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                FOREIGN KEY (username) REFERENCES {DB_TABLE_USERS}(username) ON DELETE CASCADE
            )
        """)

        # Create indexes
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_filtered_timestamp
            ON {DB_TABLE_FILTERED} (timestamp DESC)
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_unfiltered_timestamp
            ON {DB_TABLE_UNFILTERED} (timestamp DESC)
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_thickness_timestamp
            ON {DB_TABLE_THICKNESS} (timestamp DESC)
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_thickness_raw_timestamp
            ON {DB_TABLE_THICKNESS_RAW} (timestamp DESC)
        """)

        # Seed ONLY a superadmin on a fresh database. No shared weak defaults —
        # each customer gets their own admin via /provision. Password comes from
        # SUPERADMIN_PASSWORD env, or is generated and printed once.
        cur.execute(f"SELECT COUNT(*) FROM {DB_TABLE_USERS} WHERE role='superadmin'")
        if cur.fetchone()[0] == 0:
            sa_pw = SUPERADMIN_PASSWORD or secrets.token_urlsafe(12)
            cur.execute(
                f"INSERT INTO {DB_TABLE_USERS} (username, password_hash, role) VALUES (%s, %s, %s)",
                ("superadmin", generate_password_hash(sa_pw), "superadmin")
            )
            conn.commit()
            print(f"--- Created superadmin (password: {sa_pw}) ---")

        # Ensure id columns have sequences (migration for tables created without SERIAL default)
        for table, seq in [
            (DB_TABLE_FILTERED,      "sensor_filtered_readings_id_seq"),
            (DB_TABLE_UNFILTERED,    "sensor_unfiltered_readings_id_seq"),
            (DB_TABLE_THICKNESS,     "opposite_thickness_readings_id_seq"),
            (DB_TABLE_THICKNESS_RAW, "opposite_thickness_raw_readings_id_seq"),
        ]:
            cur.execute(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name=%s AND column_name='id'",
                (table,)
            )
            row = cur.fetchone()
            if row and row[0] is None:
                cur.execute(f"CREATE SEQUENCE IF NOT EXISTS {seq}")
                cur.execute(
                    f"SELECT setval('{seq}', COALESCE((SELECT MAX(id) FROM {table}), 0))"
                )
                cur.execute(f"ALTER TABLE {table} ALTER COLUMN id SET DEFAULT nextval('{seq}')")
                conn.commit()
                print(f"--- Migrated {table}: added id sequence ---")

        cur.close()
        conn.close()
        print("--- Database tables initialized ---")
    except Exception as e:
        print(f"!!! Database initialization error: {e} !!!")

# ==========================================
# SENSOR CLASS
# ==========================================
class CD22Sensor:
    def __init__(self, ip, port, name):
        self.ip = ip
        self.port = port
        self.name = name
        self.sock = None
        self.lock = threading.Lock()
        self.connected = False

    def connect(self):
        if self.connected: return True
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(SENSOR_TIMEOUT)
            self.sock.connect((self.ip, self.port))
            self.connected = True
            return True
        except Exception:
            self.connected = False
            if self.sock:
                try: self.sock.close()
                except: pass
                self.sock = None
            return False

    def disconnect(self):
        with self.lock:
            self.connected = False
            if self.sock:
                try: self.sock.close()
                except: pass
                self.sock = None

    def get_single_measurement(self):
        cmd_bytes = bytes([STX, 0x43, 0xB0, 0x01, ETX, (0x43^0xB0^0x01)])
        with self.lock:
            if not self.connected:
                if not self.connect(): return None
            try:
                self.sock.settimeout(0.1)
                self.sock.sendall(cmd_bytes)
                resp = self.sock.recv(6)
                self.sock.settimeout(SENSOR_TIMEOUT)
                if resp and len(resp) == 6 and resp[1] == 0x06:
                    raw = (resp[2] << 8) | resp[3]
                    if raw > 32767: raw -= 65536
                    return raw * 0.01
            except Exception:
                self.connected = False
                if self.sock: self.sock.settimeout(SENSOR_TIMEOUT)
                return None
        return None

# ==========================================
# FLASK & SOCKETIO SETUP
# ==========================================
app = Flask(__name__)

# Register user management routes
from user_routes import register_user_routes
register_user_routes(app)

# Register download routes
from download_routes import register_download_routes
register_download_routes(app, DB_TABLE_FILTERED, DB_TABLE_UNFILTERED, DB_TABLE_THICKNESS, DB_TABLE_THICKNESS_RAW)

# Register email alert routes
from email_alert_routes import email_alerts_bp, check_thresholds_and_alert
app.register_blueprint(email_alerts_bp)

# CORS(app) removed — handled by Traefik middleware
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

active_sensors_map = {}
sensors_lock = threading.Lock()
thickness_state = load_thickness_state()
thickness_limit = load_thickness_limit()

stream_state = {
    "active": True,
    "target_rate_hz": 5.0,
    "thread": None,
    "connected_clients": 0
}

last_ingest_reading = {"A": None, "B": None, "C": None}
pending_config_commands = []
config_results = []
pending_config_lock = threading.Lock()
_last_ingest_emit_time = 0.0
_ingest_emit_lock = threading.Lock()

# Sensor-freshness tracking. last_ingest_monotonic = monotonic timestamp of the
# most recent ingest POST that carried real data. The stream loop uses it to
# detect when the sensors (or the pi_client link) have gone offline, so it can
# stop presenting the last received values as if they were still live.
last_ingest_monotonic = 0.0
SENSOR_STALE_SECONDS = 3.0
_last_status_emit_mono = 0.0
_last_status_online = None

# ==========================================
# APIS - SERVER CONFIG (missing endpoint that frontends expect)
# ==========================================
@app.route('/server/config', methods=['GET'])
def get_server_config():
    """Return the current server configuration.
    Supports optional ?mode=opposite to filter sensor_configs to only A & B.
    Default (?mode=sbs or omitted) returns all sensors A, B, C.
    """
    mode = request.args.get("mode", "sbs").lower()
    if mode == "opposite":
        filtered_configs = {k: v for k, v in SENSOR_CONFIGS.items() if k.upper() in {"A", "B"}}
    else:
        filtered_configs = dict(SENSOR_CONFIGS)

    return jsonify({
        "sensor_configs": filtered_configs,
        "server_port": SERVER_PORT,
        "sensor_timeout": SENSOR_TIMEOUT,
        "limit_filtered": LIMIT_FILTERED,
        "limit_unfiltered": LIMIT_UNFILTERED,
        "limit_thickness": LIMIT_THICKNESS,
        "limit_thickness_raw": LIMIT_THICKNESS_RAW,
        "db_host": DB_HOST,
        "db_name": DB_NAME,
    }), 200

# ==========================================
# APIS - NETWORK CONFIG (used by sensor_setup.html)
# ==========================================
@app.route('/config/network', methods=['GET', 'POST'])
def handle_network_config():
    """GET: return current sensor_network.json config.
       POST: save new network config and activate sensors."""
    if request.method == 'GET':
        config = load_network_config()
        if not config:
            return jsonify({}), 200
        return jsonify(config), 200

    elif request.method == 'POST':
        try:
            payload = request.get_json()
            if not payload:
                return jsonify({"error": "No JSON payload provided"}), 400
            save_network_config(payload)
            refresh_sensor_configs(payload)
            active = list(active_sensors_map.keys())
            return jsonify({
                "message": "Network config saved successfully.",
                "active_sensors": active
            }), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500

# ==========================================
# APIS - CONFIG FILE SYNC
# ==========================================
@app.route('/config/file', methods=['GET', 'POST'])
def handle_config_file():
    if request.method == 'GET':
        try:
            with open(CONFIG_FILE_PATH, 'r') as f:
                config_data = json.load(f)
            return jsonify(config_data), 200
        except FileNotFoundError:
            return jsonify({"error": "Configuration file not found"}), 404
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    elif request.method == 'POST':
        try:
            new_config = request.json
            if not new_config:
                return jsonify({"error": "No JSON payload provided"}), 400
            with open(CONFIG_FILE_PATH, 'w') as f:
                json.dump(new_config, f, indent=4)
            return jsonify({"message": "Configuration file updated successfully"}), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500

def _thickness_ctx():
    """Resolve which calibration state a /thickness/* request targets.

    Real provisioned device (device_id in query or JSON body) -> that device's
    per-device state, persisted to devices.calibration. Otherwise -> the global
    legacy state (unchanged behaviour for the original single install).

    Returns (device_id, get_state, set_state, latest_reading_dict).
    """
    did = request.args.get("device_id")
    if not did and request.is_json:
        did = (request.get_json(silent=True) or {}).get("device_id")
    if did and did != LEGACY_DEVICE_ID:
        st = _get_device_state(did)
        def getter():
            return st["thickness"]
        def setter(s):
            st["thickness"] = s
            _save_device_calibration(did, s)
        return did, getter, setter, dict(st.get("last_raw") or {})
    legacy_latest = {k: v for k, v in last_ingest_reading.items() if v is not None}
    return LEGACY_DEVICE_ID, get_thickness_state, set_thickness_state, legacy_latest


@app.route('/thickness/state', methods=['GET'])
def thickness_state_api():
    _did, getter, _setter, _latest = _thickness_ctx()
    return jsonify(getter()), 200

@app.route('/thickness/limit', methods=['GET', 'POST'])
def thickness_limit_api():
    """Global thickness limit, shared across all users and sessions.

    GET  -> current limit {active, min, max}
    POST -> persist a new limit; survives logout, login as another user,
            and server restarts (stored in thickness_limit.json).
    """
    if request.method == 'GET':
        return jsonify(get_thickness_limit()), 200
    data = request.get_json(silent=True) or {}
    limit = default_thickness_limit()
    limit["active"] = bool(data.get("active", False))
    raw_min = data.get("min", "")
    raw_max = data.get("max", "")
    limit["min"] = "" if raw_min is None else str(raw_min)
    limit["max"] = "" if raw_max is None else str(raw_max)
    set_thickness_limit(limit)
    return jsonify({"message": "Thickness limit saved.", **limit}), 200

@app.route('/thickness/setup-ready', methods=['POST'])
def thickness_setup_ready():
    _did, getter, setter, latest = _thickness_ctx()
    reading = {k: v for k, v in latest.items() if v is not None}
    if not reading:
        return jsonify({"error": "No sensor reading received yet. Ensure the agent is running and sending data."}), 400
    updated_state = default_thickness_state()
    updated_state["setup_ready"] = True
    updated_state["captured_at"] = datetime.datetime.now().isoformat()
    for sid, val in reading.items():
        updated_state["reference_readings"][sid] = round(float(val), 3)
    setter(updated_state)
    return jsonify({
        "message": "Starting readings captured successfully.",
        "setup_ready": True,
        "captured_at": updated_state.get("captured_at"),
        "reference_readings": updated_state.get("reference_readings", {}),
        "captured_readings": reading,
    }), 200

@app.route('/thickness/calibration', methods=['POST'])
def thickness_calibration():
    _did, getter, setter, latest = _thickness_ctx()
    data = request.json or {}
    try:
        reference_thickness = float(data.get("reference_thickness"))
    except (TypeError, ValueError):
        return jsonify({"error": "A valid reference thickness is required."}), 400
    if reference_thickness < 0:
        return jsonify({"error": "Reference thickness must be zero or greater."}), 400
    reading = {k: v for k, v in latest.items() if v is not None}
    if not reading:
        return jsonify({"error": "No sensor reading received yet. Ensure the agent is running and sending data."}), 400
    current_state = getter()
    updated_state = default_thickness_state()
    updated_state["setup_ready"] = current_state.get("setup_ready", False)
    updated_state["captured_at"] = current_state.get("captured_at")
    updated_state["reference_readings"] = normalize_sensor_readings(current_state.get("reference_readings", {}))
    updated_state["calibration_completed"] = True
    updated_state["calibration_active"] = True
    updated_state["calibration_captured_at"] = datetime.datetime.now().isoformat()
    updated_state["calibration_reference_thickness"] = round(float(reference_thickness), 3)
    for sensor_id, reading_val in reading.items():
        updated_state["calibration_baseline_readings"][sensor_id] = round(float(reading_val), 3)
    setter(updated_state)
    return jsonify({
        "message": "Calibration saved successfully.",
        "calibration_active": True,
        "calibration_captured_at": updated_state.get("calibration_captured_at"),
        "calibration_reference_thickness": updated_state.get("calibration_reference_thickness", 0.0),
        "calibration_baseline_readings": updated_state.get("calibration_baseline_readings", {}),
        "captured_readings": reading,
    }), 200

@app.route('/thickness/gap', methods=['POST'])
def thickness_gap_set():
    """Set the distance between the two sensor faces (opposite mode)."""
    _did, getter, setter, _latest = _thickness_ctx()
    data = request.json or {}
    try:
        gap_distance = float(data.get("gap_distance"))
    except (TypeError, ValueError):
        return jsonify({"error": "A valid gap distance is required."}), 400
    if gap_distance <= 0:
        return jsonify({"error": "Gap distance must be greater than zero."}), 400
    current_state = getter()
    current_state["gap_distance"] = round(gap_distance, 3)
    current_state["calibration_completed"] = True
    current_state["calibration_active"] = True
    setter(current_state)

    return jsonify({
        "message": "Gap distance set successfully.",
        "gap_distance": current_state["gap_distance"],
        "calibration_active": True
    }), 200

@app.route('/thickness/auto-gap', methods=['POST'])
def thickness_auto_gap_set():
    """Auto-calculate gap distance using object thickness (opposite mode)."""
    _did, getter, setter, latest = _thickness_ctx()
    data = request.json or {}
    try:
        object_thickness = float(data.get("object_thickness"))
    except (TypeError, ValueError):
        return jsonify({"error": "A valid object thickness is required."}), 400
    if object_thickness <= 0:
        return jsonify({"error": "Object thickness must be greater than zero."}), 400
    reading = {k: round(float(v), 3) for k, v in latest.items() if v is not None}
    if len(reading) < 2 or reading.get("A") is None or reading.get("B") is None:
        return jsonify({"error": "Waiting for readings from both sensors. Ensure the agent is running."}), 400
    dist_A = reading.get("A")
    dist_B = reading.get("B")
    total_gap = 2 * ZERO_OFFSET_MM + float(dist_A) + float(dist_B) + object_thickness
    tol_min = data.get("thickness_tolerance_min")
    tol_max = data.get("thickness_tolerance_max")
    if tol_min is not None:
        try: tol_min = float(tol_min)
        except: tol_min = None
    if tol_max is not None:
        try: tol_max = float(tol_max)
        except: tol_max = None
    current_state = getter()
    current_state["gap_distance"] = round(total_gap, 3)
    current_state["calibration_completed"] = True
    current_state["calibration_active"] = True
    current_state["auto_gap_active"] = True
    current_state["object_thickness"] = object_thickness
    current_state["thickness_tolerance_min"] = tol_min
    current_state["thickness_tolerance_max"] = tol_max
    setter(current_state)

    return jsonify({
        "message": "Auto-gap setup completed successfully.",
        "gap_distance": round(total_gap, 3),
        "calibration_active": True,
        "auto_gap_active": True,
        "object_thickness": object_thickness,
        "captured_readings": reading,
        "thickness_tolerance_min": tol_min,
        "thickness_tolerance_max": tol_max,
    }), 200

@app.route('/thickness/calibration/reset', methods=['POST'])
def thickness_calibration_reset():
    _did, getter, setter, _latest = _thickness_ctx()
    current_state = getter()
    updated_state = default_thickness_state()
    updated_state["setup_ready"] = current_state.get("setup_ready", False)
    updated_state["captured_at"] = current_state.get("captured_at")
    updated_state["reference_readings"] = normalize_sensor_readings(current_state.get("reference_readings", {}))
    setter(updated_state)
    return jsonify({
        "message": "Calibration reset successfully.",
        "calibration_active": updated_state.get("calibration_active", False),
        "calibration_reference_thickness": updated_state.get("calibration_reference_thickness", 0.0),
        "calibration_baseline_readings": updated_state.get("calibration_baseline_readings", {}),
        "calibration_captured_at": updated_state.get("calibration_captured_at"),
    }), 200

# ==========================================
# APIS - AUTHENTICATION
# ==========================================
@app.route('/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
        cur = conn.cursor()
        cur.execute(f"SELECT password_hash, role FROM {DB_TABLE_USERS} WHERE username = %s", (username,))
        user_record = cur.fetchone()
        cur.close()
        conn.close()
        if user_record and check_password_hash(user_record[0], password):
            # All users share the global thickness state (no per-user calibration)
            return jsonify({
                "message": "Login successful",
                "username": username,
                "role": user_record[1],
                "has_calibration": get_thickness_state().get("calibration_active", False),
            }), 200
        else:
            return jsonify({"error": "Invalid username or password"}), 401
    except Exception as e:
        print(f"Login DB Error: {e}")
        return jsonify({"error": "Internal server error connecting to database"}), 500

@app.route('/login/demo', methods=['POST'])
def demo_login():
    """Allow demo login without database."""
    from demo_accounts import DEMO_ACCOUNTS
    data = request.json
    username = data.get('username')
    password = data.get('password')
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400
    for account in DEMO_ACCOUNTS:
        if account["username"] == username and account["password"] == password:
            has_cal = get_thickness_state().get("calibration_active", False)
            return jsonify({
                "message": "Login successful",
                "username": username,
                "role": account["role"],
                "has_calibration": has_cal,
            }), 200
    return jsonify({"error": "Invalid username or password"}), 401

# ==========================================
# APIS - DATA INGEST (from Pi cameras)
# ==========================================
@app.route('/ingest/readings', methods=['POST'])
@app.route('/ingest/data', methods=['POST'])
def ingest_data():
    """Receive sensor data from an agent / Pi client.

    Two paths:
      • Multi-tenant: request carries X-Device-Id + X-Device-Key headers. The key
        is validated against the devices table and the reading is processed and
        stored tagged with that device_id (isolated per customer).
      • Legacy single-tenant: no device headers — original behaviour, unchanged,
        feeding the global stream loop (data is stored under dev_legacy).
    """
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400
    # Normalize keys: accept sensor_A, sensor_B, sensor_C OR A, B, C
    a = data.get("A") if "A" in data else data.get("sensor_A")
    b = data.get("B") if "B" in data else data.get("sensor_B")
    c = data.get("C") if "C" in data else data.get("sensor_C")
    now = datetime.datetime.now()

    # --- Multi-tenant device path ---
    dev_id = request.headers.get('X-Device-Id')
    if dev_id:
        row = _verify_device(dev_id, request.headers.get('X-Device-Key'))
        if not row:
            return jsonify({"error": "Invalid device credentials"}), 401
        if dev_id != LEGACY_DEVICE_ID:
            af = float(a) if a is not None else None
            bf = float(b) if b is not None else None
            cf = float(c) if c is not None else None
            _process_device_reading(dev_id, af, bf, cf, now)
            _touch_last_seen(dev_id)
            return jsonify({"message": "Data received", "device_id": dev_id,
                            "timestamp": now.isoformat()}), 200
        # dev_legacy posting with headers → fall through to the legacy pipeline.

    # --- Legacy single-tenant path (original behaviour) ---
    auth_header = request.headers.get('Authorization', '')
    expected_token = f"Bearer {INGEST_API_KEY}"
    # Accept with or without Bearer prefix, and from any source (local proxy may strip)
    token_only = auth_header.replace("Bearer ", "").replace("bearer ", "").strip()
    expected_only = expected_token.replace("Bearer ", "").strip()
    if token_only != expected_only:
        # Also accept requests from localhost without auth (internal nginx proxy)
        if request.remote_addr not in ('127.0.0.1', '::1'):
            return jsonify({"error": "Unauthorized"}), 401
    last_ingest_reading["A"] = float(a) if a is not None else None
    last_ingest_reading["B"] = float(b) if b is not None else None
    last_ingest_reading["C"] = float(c) if c is not None else None
    # Mark data freshness so the stream loop can detect when sensors go offline.
    if a is not None or b is not None or c is not None:
        global last_ingest_monotonic
        last_ingest_monotonic = time.monotonic()
    return jsonify({"message": "Data received", "timestamp": now.isoformat()}), 200


@app.route('/sensors/status', methods=['GET'])
def sensors_status():
    """Report whether the sensor feed is currently live (fresh ingest data).

    online = an ingest POST with real data arrived within SENSOR_STALE_SECONDS.
    Lets the frontend show a clear "Sensors disconnected" state.
    """
    age = time.monotonic() - last_ingest_monotonic
    online = (last_ingest_monotonic > 0) and (age <= SENSOR_STALE_SECONDS)
    per = {sid: (last_ingest_reading.get(sid) is not None) for sid in ("A", "B", "C")}
    return jsonify({
        "online": bool(online),
        "stale_seconds": round(age, 2) if last_ingest_monotonic > 0 else None,
        "sensors": per,
    }), 200


# ==========================================
# MULTI-TENANT: provisioning + agent activation
# ==========================================
@app.route('/provision', methods=['POST'])
def provision_device():
    """Admin-only. Mint a new customer (if needed) + device, returning a unique
    device_id and one-time device_key. Only the key HASH is stored — the plaintext
    key is shown here exactly once and put on the customer's activation card."""
    if request.headers.get('X-Admin-Token', '') != PROVISION_ADMIN_TOKEN:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    customer = (data.get("customer") or "").strip()
    mode = (data.get("sensor_mode") or "opposite").strip().lower()
    label = (data.get("label") or "").strip()
    if not customer:
        return jsonify({"error": "customer is required"}), 400
    if mode not in ("opposite", "sbs"):
        return jsonify({"error": "sensor_mode must be 'opposite' or 'sbs'"}), 400

    conn, cur = _db_connect()
    if conn is None:
        return jsonify({"error": "Database unavailable"}), 500
    try:
        cur.execute("INSERT INTO customers (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (customer,))
        cur.execute("SELECT id FROM customers WHERE name=%s", (customer,))
        cid = cur.fetchone()[0]
        # Unique device_id (retry on the astronomically rare collision)
        device_id = None
        for _ in range(5):
            cand = "dev_" + secrets.token_hex(4)
            cur.execute("SELECT 1 FROM devices WHERE device_id=%s", (cand,))
            if not cur.fetchone():
                device_id = cand
                break
        if device_id is None:
            conn.rollback()
            return jsonify({"error": "Could not allocate device_id"}), 500
        device_key = secrets.token_urlsafe(18)
        key_hash = generate_password_hash(device_key)
        cur.execute(
            "INSERT INTO devices (device_id, customer_id, device_key_hash, sensor_mode, label) "
            "VALUES (%s,%s,%s,%s,%s)",
            (device_id, cid, key_hash, mode, label or None))

        # First device for this customer → create their own company SUPERADMIN login
        # (no shared admin/admin123 ever). They log in with company + username +
        # password. Credentials returned once here for the activation card.
        admin_info = None
        cur.execute("SELECT COUNT(*) FROM users WHERE customer_id=%s", (cid,))
        if cur.fetchone()[0] == 0:
            slug = re.sub(r'[^a-z0-9]+', '', customer.lower())[:20] or "customer"
            admin_username = (data.get("admin_username") or "admin").strip()
            admin_email = (data.get("admin_email") or f"admin@{slug}.local").strip().lower()
            admin_password = data.get("admin_password") or secrets.token_urlsafe(9)
            cur.execute(
                "INSERT INTO users (username, email, password_hash, role, customer_id) "
                "VALUES (%s,%s,%s,%s,%s)",
                (admin_username, admin_email, generate_password_hash(admin_password), "superadmin", cid))
            admin_info = {"admin_username": admin_username, "admin_email": admin_email,
                          "admin_password": admin_password, "company": customer}
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

    resp = {
        "device_id": device_id,
        "device_key": device_key,
        "customer": customer,
        "sensor_mode": mode,
        "label": label,
    }
    if admin_info:
        resp.update(admin_info)
    return jsonify(resp), 201


@app.route('/agent/activate', methods=['POST'])
def agent_activate():
    """Called by the agent's setup wizard. Validates the activation code and
    returns the customer details the agent needs (so the customer never types
    their own company/mode — the server is the source of truth)."""
    data = request.json or {}
    device_id = (data.get("device_id") or "").strip()
    device_key = (data.get("device_key") or "").strip()
    row = _verify_device(device_id, device_key)
    if not row:
        return jsonify({"error": "Invalid or revoked activation code"}), 401

    cname = None
    conn, cur = _db_connect()
    if conn is not None:
        try:
            cur.execute("SELECT name FROM customers WHERE id=%s", (row["customer_id"],))
            r = cur.fetchone()
            cname = r[0] if r else None
            cur.execute("UPDATE devices SET last_seen=NOW() WHERE device_id=%s", (device_id,))
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()

    mode = row["sensor_mode"]
    labels = ["A", "B", "C"] if mode == "sbs" else ["A", "B"]
    return jsonify({
        "device_id": device_id,
        "customer_name": cname,
        "sensor_mode": mode,
        "sensor_count": len(labels),
        "sensor_labels": labels,
        "label": row.get("label"),
        "post_rate_hz": 5,
    }), 200


# ==========================================
# AUTH: signed tokens + per-customer user management
# ==========================================
_token_serializer = URLSafeTimedSerializer(AUTH_SECRET, salt="auth-token")
VALID_ROLES = ("superadmin", "admin", "supervisor", "worker")

def _is_global(auth):
    """A 'global' (Rajdeep) account has NO customer (customer_id IS NULL) and can
    see/manage every customer. A company superadmin has role 'superadmin' but a
    customer_id set, so it must NEVER be treated as global — tenant isolation keys
    off customer_id, not the role name."""
    return auth is not None and auth.get("cid") is None

def issue_token(user):
    return _token_serializer.dumps({"uid": user["id"], "cid": user["customer_id"], "role": user["role"]})

def verify_token(tok):
    try:
        return _token_serializer.loads(tok, max_age=AUTH_TOKEN_TTL)
    except (BadSignature, SignatureExpired, Exception):
        return None

def _bearer_token():
    h = request.headers.get("Authorization", "")
    return h[7:].strip() if h.startswith("Bearer ") else None

def require_auth(roles=None):
    """Gate an endpoint behind a valid token. superadmin always passes; otherwise
    the token role must be in `roles` (if given). Sets g.auth = {uid, cid, role}."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            data = verify_token(_bearer_token() or "")
            if not data:
                return jsonify({"error": "Authentication required"}), 401
            if roles and data.get("role") != "superadmin" and data.get("role") not in roles:
                return jsonify({"error": "Forbidden"}), 403
            g.auth = data
            return fn(*a, **kw)
        return wrapper
    return deco

def _customer_name(customer_id):
    if not customer_id:
        return None
    conn, cur = _db_connect()
    if conn is None:
        return None
    try:
        cur.execute("SELECT name FROM customers WHERE id=%s", (customer_id,))
        r = cur.fetchone()
        return r[0] if r else None
    finally:
        conn.close()


@app.route('/auth/login', methods=['POST'])
def auth_login():
    """Company + username + password -> signed token + user info.

    Each company has its own users (usernames are unique only WITHIN a company),
    so the company name disambiguates which user to authenticate. A blank company
    means a GLOBAL (Rajdeep) account (customer_id IS NULL) — e.g. the global
    superadmin — which may log in by username or email.
    """
    data = request.json or {}
    company  = (data.get("company") or "").strip()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    # Back-compat: some callers may still send only an email/identifier.
    ident = username or (data.get("email") or "").strip()
    if not ident or not password:
        return jsonify({"error": "username and password required"}), 400
    conn, cur = _db_connect()
    if conn is None:
        return jsonify({"error": "Database unavailable"}), 500
    try:
        if company:
            # Resolve the company, then match the username within THAT company only.
            cur.execute("SELECT id FROM customers WHERE lower(name)=lower(%s)", (company,))
            crow = cur.fetchone()
            if not crow:
                return jsonify({"error": "Invalid credentials"}), 401
            cur.execute(
                "SELECT id, username, email, password_hash, role, customer_id "
                "FROM users WHERE customer_id=%s AND (username=%s OR lower(email)=lower(%s)) LIMIT 1",
                (crow[0], username, ident))
        else:
            # No company → global (Rajdeep) account by username, with a unique-email
            # fallback for back-compat (old single-field frontend / direct API callers
            # during the deploy window). Email is unique, so no cross-tenant ambiguity.
            cur.execute(
                "SELECT id, username, email, password_hash, role, customer_id "
                "FROM users WHERE (customer_id IS NULL AND username=%s) OR lower(email)=lower(%s) "
                "ORDER BY (customer_id IS NULL) DESC LIMIT 1",
                (username, ident))
        r = cur.fetchone()
    finally:
        conn.close()
    if not r or not check_password_hash(r[3], password):
        return jsonify({"error": "Invalid credentials"}), 401
    user = {"id": r[0], "username": r[1], "email": r[2], "role": r[4], "customer_id": r[5]}
    user["customer_name"] = _customer_name(user["customer_id"])
    return jsonify({"token": issue_token(user), "user": user}), 200


@app.route('/auth/me', methods=['GET'])
@require_auth()
def auth_me():
    return jsonify(g.auth), 200


@app.route('/auth/users', methods=['GET'])
@require_auth(roles=("admin",))
def auth_users_list():
    a = g.auth
    conn, cur = _db_connect()
    if conn is None:
        return jsonify({"error": "Database unavailable"}), 500
    try:
        if _is_global(a):
            cur.execute("SELECT id, username, email, role, customer_id FROM users ORDER BY id")
        else:
            cur.execute("SELECT id, username, email, role, customer_id FROM users WHERE customer_id=%s ORDER BY id", (a["cid"],))
        rows = cur.fetchall()
    finally:
        conn.close()
    return jsonify([{"id": r[0], "username": r[1], "email": r[2], "role": r[3], "customer_id": r[4]} for r in rows]), 200


@app.route('/auth/users', methods=['POST'])
@require_auth(roles=("admin",))
def auth_users_create():
    a = g.auth
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()
    role = (data.get("role") or "worker").strip()
    username = (data.get("username") or (email.split("@")[0] if email else "")).strip()
    if not email or not password:
        return jsonify({"error": "email and password required"}), 400
    if role not in VALID_ROLES:
        return jsonify({"error": f"role must be one of {VALID_ROLES}"}), 400
    # Only a global (Rajdeep) account may create users in an arbitrary customer.
    # A company superadmin can only create users WITHIN their own company.
    if _is_global(a):
        customer_id = data.get("customer_id", a["cid"])
    else:
        customer_id = a["cid"]
    conn, cur = _db_connect()
    if conn is None:
        return jsonify({"error": "Database unavailable"}), 500
    try:
        cur.execute(
            "INSERT INTO users (username, email, password_hash, role, customer_id) "
            "VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (username, email, generate_password_hash(password), role, customer_id))
        uid = cur.fetchone()[0]
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()
    return jsonify({"id": uid, "username": username, "email": email, "role": role, "customer_id": customer_id}), 201


def _assert_same_customer_or_404(cur, uid, auth):
    cur.execute("SELECT customer_id, role FROM users WHERE id=%s", (uid,))
    r = cur.fetchone()
    if not r:
        return None, (jsonify({"error": "not found"}), 404)
    if not _is_global(auth) and r[0] != auth["cid"]:
        return None, (jsonify({"error": "Forbidden"}), 403)
    return r, None


@app.route('/auth/users/<int:uid>', methods=['DELETE'])
@require_auth(roles=("admin",))
def auth_users_delete(uid):
    a = g.auth
    conn, cur = _db_connect()
    if conn is None:
        return jsonify({"error": "Database unavailable"}), 500
    try:
        r, err = _assert_same_customer_or_404(cur, uid, a)
        if err:
            return err
        cur.execute("DELETE FROM users WHERE id=%s", (uid,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"deleted": uid}), 200


@app.route('/auth/users/<int:uid>/password', methods=['POST'])
@require_auth(roles=("admin",))
def auth_users_password(uid):
    a = g.auth
    pw = ((request.json or {}).get("password") or "").strip()
    if not pw:
        return jsonify({"error": "password required"}), 400
    conn, cur = _db_connect()
    if conn is None:
        return jsonify({"error": "Database unavailable"}), 500
    try:
        r, err = _assert_same_customer_or_404(cur, uid, a)
        if err:
            return err
        cur.execute("UPDATE users SET password_hash=%s WHERE id=%s", (generate_password_hash(pw), uid))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"updated": uid}), 200


@app.route('/auth/devices', methods=['GET'])
@require_auth()
def auth_devices():
    """List devices visible to the caller. superadmin → all; others → own customer."""
    a = g.auth
    conn, cur = _db_connect()
    if conn is None:
        return jsonify({"error": "Database unavailable"}), 500
    try:
        base = ("SELECT d.device_id, d.label, d.sensor_mode, d.revoked, d.last_seen, c.name "
                "FROM devices d LEFT JOIN customers c ON c.id=d.customer_id ")
        if _is_global(a):
            cur.execute(base + "ORDER BY d.created_at")
        else:
            cur.execute(base + "WHERE d.customer_id=%s ORDER BY d.created_at", (a["cid"],))
        rows = cur.fetchall()
    finally:
        conn.close()
    return jsonify([{
        "device_id": r[0], "label": r[1], "sensor_mode": r[2], "revoked": r[3],
        "last_seen": r[4].isoformat() if r[4] else None, "customer_name": r[5],
    } for r in rows]), 200


def _device_visible_to_auth(device_id, auth):
    """True if the token's customer owns device_id (superadmin sees all)."""
    if not auth:
        return False
    if _is_global(auth):
        return True
    row = _device_lookup(device_id)
    return bool(row and row.get("customer_id") == auth.get("cid"))


# ==========================================
# APIS - CONFIG READ/WRITE (REST for CD22 sensor configuration)
# ==========================================
def get_next_db_id(cursor, table_name, max_rows):
    cursor.execute(f"SELECT COALESCE(MAX(id), 0) + 1 FROM {table_name}")
    next_id = cursor.fetchone()[0]
    # If next_id would exceed max_rows, wrap to 1 (oldest records overwritten)
    if next_id > max_rows:
        return 1
    return next_id

@app.route('/config/read', methods=['POST'])
def config_read():
    """Read sensor config register(s). Accepts JSON with 'command' (int) and optional 'sensor'."""
    data = request.json or {}
    command = data.get("command")
    sensor_id = data.get("sensor", "A").upper()
    if command is None:
        return jsonify({"error": "Command is required"}), 400
    if sensor_id not in active_sensors_map:
        return jsonify({"error": f"Sensor {sensor_id} not found"}), 404
    sensor = active_sensors_map[sensor_id]
    cmd_int = int(command)
    lsb = cmd_int & 0xFF
    msb = (cmd_int >> 8) & 0xFF
    cmd_bytes = bytes([STX, 0x4A, msb, lsb, ETX, (0x4A ^ msb ^ lsb)])
    try:
        with sensor.lock:
            if not sensor.connected and not sensor.connect():
                return jsonify({"error": f"Cannot connect to sensor {sensor_id}"}), 500
            sensor.sock.settimeout(0.1)
            sensor.sock.sendall(cmd_bytes)
            resp = sensor.sock.recv(8)
            sensor.sock.settimeout(SENSOR_TIMEOUT)
        if resp and len(resp) >= 6 and resp[1] == 0x06:
            raw = (resp[2] << 8) | resp[3]
            if raw > 32767: raw -= 65536
            return jsonify({"sensor": sensor_id, "command": command, "value": raw}), 200
        return jsonify({"error": "Invalid response from sensor"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/config/write', methods=['POST'])
def config_write():
    """Write sensor config register(s).
    On server mode, this queues commands for pi_client to poll and execute.
    Accepts two input formats:
      1) {"sensor":"A", "command":int, "value":int}
      2) {"sensor":"A", "addr_h":"0x40", "addr_l":"0x06", "val_h":"0x00", "val_l":"0x03"}
    """
    data = request.json or {}
    sensor_id = data.get("sensor", "A").upper()
    if sensor_id.upper() not in {"A", "B", "C"}:
        return jsonify({"error": f"Invalid sensor {sensor_id}. Must be A, B, or C."}), 400

    # Determine command and value from either format
    command = data.get("command")
    value = data.get("value")
    if command is None or value is None:
        # Try addr_h/addr_l/val_h/val_l hex format
        addr_h = data.get("addr_h")
        addr_l = data.get("addr_l")
        val_h = data.get("val_h", "0x00")
        val_l = data.get("val_l")
        if addr_h is None or addr_l is None or val_l is None:
            return jsonify({"error": "Provide either 'command'+'value' or 'addr_h'+'addr_l'+'val_l' (with optional 'val_h')"}), 400
        try:
            cmd_int = (int(addr_h, 16) << 8) | int(addr_l, 16)
            val_int = (int(val_h, 16) << 8) | int(val_l, 16)
        except (ValueError, TypeError) as e:
            return jsonify({"error": f"Invalid hex value: {e}"}), 400
    else:
        try:
            cmd_int = int(command) & 0xFFFF
            val_int = int(value) & 0xFFFF
        except (ValueError, TypeError) as e:
            return jsonify({"error": f"Invalid command/value: {e}"}), 400

    # Queue the command for pi_client to pick up
    cmd_entry = {
        "sensor": sensor_id,
        "addr_h": f"0x{(cmd_int >> 8) & 0xFF:02X}",
        "addr_l": f"0x{cmd_int & 0xFF:02X}",
        "val_h": f"0x{(val_int >> 8) & 0xFF:02X}",
        "val_l": f"0x{val_int & 0xFF:02X}",
    }
    pending_config_commands.append(cmd_entry)

    return jsonify({
        "sensor": sensor_id,
        "command": cmd_int,
        "value": val_int,
        "success": True,
        "message": f"Write queued - reg 0x{cmd_int:04X} = 0x{val_int:04X}"
    }), 200

# ==========================================
# APIS - STREAM TRIMMING
# ==========================================
@app.route('/stream/trim', methods=['POST'])
def stream_trim():
    data = request.json or {}
    target_rate = data.get("target_rate_hz", 5.0)
    stream_state["target_rate_hz"] = float(target_rate)
    return jsonify({"message": f"Stream rate set to {target_rate} Hz"}), 200

@app.route('/stream/config', methods=['POST'])
def stream_config():
    data = request.json or {}
    # Accept both 'rate' (frontend format) and 'target_rate_hz' (internal format)
    if "rate" in data:
        stream_state["target_rate_hz"] = float(data["rate"])
    elif "target_rate_hz" in data:
        stream_state["target_rate_hz"] = float(data["target_rate_hz"])
    return jsonify({"message": "Stream config updated", "target_rate_hz": stream_state["target_rate_hz"]}), 200

# ==========================================
# PI CLIENT CONFIG POLL ENDPOINTS
# ==========================================
# pi_client polls /config/poll every 3 seconds for sensor write commands
# Note: pending_config_commands is declared above (global scope)

@app.route('/config/poll', methods=['GET'])
def config_poll():
    """Return pending config commands for pi_client to execute on sensors."""
    cmd_list = list(pending_config_commands)
    pending_config_commands.clear()
    return jsonify({"commands": cmd_list}), 200

@app.route('/config/result', methods=['POST'])
def config_result():
    """Receive result of a config command execution from pi_client."""
    data = request.json or {}
    # Log or process the result as needed
    return jsonify({"status": "received"}), 200

# ==========================================
# EXISTING PAGES + STATIC FILES
# ==========================================
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_react(path):
    """Serve the React frontend for any unmatched route."""
    static_dir = os.path.join(BASE_DIR, '..', 'dist')
    index_path = os.path.join(static_dir, 'index.html')
    if not os.path.exists(index_path):
        return jsonify({"error": "Frontend not built. Run 'npm run build' in the project root."}), 404
    full_path = os.path.join(static_dir, path)
    if path and os.path.exists(full_path) and os.path.isfile(full_path):
        return send_from_directory(static_dir, path)
    return send_from_directory(static_dir, 'index.html')

# ==========================================
# INGEST STREAMING (WebSocket)
# ==========================================
def poll_local_sensors():
    """Poll locally-connected CD22 sensors directly and return readings dict.
    
    Only used when NOT in CLOUD_MODE — polls sensors on the local LAN.
    In CLOUD_MODE, sensors are not reachable (they're on 192.168.5.x LAN).
    """
    with sensors_lock:
        if not active_sensors_map:
            return {}
        readings = {}
        for sid in sorted(active_sensors_map.keys()):
            sensor = active_sensors_map[sid]
            reading = sensor.get_single_measurement()
            if reading is not None:
                readings[sid] = reading
        return readings

def _db_connect():
    """Open a psycopg2 connection; returns (conn, cur) or (None, None) on failure."""
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
        return conn, conn.cursor()
    except Exception as e:
        print(f"[Stream DB] connect failed: {e}")
        return None, None

def _compute_thickness(a, b, state):
    """Opposite-mode thickness from sensors a & b, using the same rules as the
    stream loop: gap-based when a gap is calibrated, else the calibration-baseline
    fallback. Returns None when thickness can't be computed."""
    if a is None or b is None:
        return None
    gap = state.get("gap_distance", 0.0)
    if gap > 0:
        return calculate_opposite_thickness(a, b)
    cal_baselines = state.get("calibration_baseline_readings", {})
    bA = cal_baselines.get("A")
    bB = cal_baselines.get("B")
    if state.get("calibration_active") and bA is not None and bB is not None:
        try:
            ref = float(state.get("calibration_reference_thickness", 0.0) or 0.0)
            delta_A = float(bA) - float(a)
            delta_B = float(bB) - float(b)
            return round(ref + delta_A + delta_B, 3)
        except (TypeError, ValueError):
            return None
    return None


def _db_write(conn, cur, now, raw, filt, raw_thickness, filt_thickness, device_id=LEGACY_DEVICE_ID):
    """INSERT one reading into all four sensor tables, then commit.

    raw  -> unfiltered/raw tables (instantaneous values)
    filt -> filtered tables (moving-average values)
    device_id -> tenant tag; defaults to the legacy single-tenant install.
    """
    ra, rb, rc = raw.get("A"), raw.get("B"), raw.get("C")
    fa, fb, fc = filt.get("A"), filt.get("B"), filt.get("C")
    # Filtered (moving-average) SBS table
    cur.execute(
        f"INSERT INTO {DB_TABLE_FILTERED} (timestamp, sensor_a, sensor_b, sensor_c, device_id) VALUES (%s,%s,%s,%s,%s)",
        (now, fa, fb, fc, device_id)
    )
    # Unfiltered (raw) SBS table
    cur.execute(
        f"INSERT INTO {DB_TABLE_UNFILTERED} (timestamp, sensor_a, sensor_b, sensor_c, device_id) VALUES (%s,%s,%s,%s,%s)",
        (now, ra, rb, rc, device_id)
    )
    if ra is not None and rb is not None:
        # Filtered opposite-thickness table (moving-average sensors + thickness)
        cur.execute(
            f"INSERT INTO {DB_TABLE_THICKNESS} (timestamp, sensor_a, sensor_b, thickness, device_id) VALUES (%s,%s,%s,%s,%s)",
            (now, fa, fb, filt_thickness, device_id)
        )
        # Raw opposite-thickness table (instantaneous sensors + thickness)
        cur.execute(
            f"INSERT INTO {DB_TABLE_THICKNESS_RAW} (timestamp, sensor_a, sensor_b, thickness, device_id) VALUES (%s,%s,%s,%s,%s)",
            (now, ra, rb, raw_thickness, device_id)
        )
    conn.commit()

def _db_trim(cur, conn):
    """Delete oldest rows when any table exceeds its row limit."""
    for table, limit in [
        (DB_TABLE_FILTERED,      LIMIT_FILTERED),
        (DB_TABLE_UNFILTERED,    LIMIT_UNFILTERED),
        (DB_TABLE_THICKNESS,     LIMIT_THICKNESS),
        (DB_TABLE_THICKNESS_RAW, LIMIT_THICKNESS_RAW),
    ]:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        count = cur.fetchone()[0]
        if count > limit:
            excess = count - limit
            cur.execute(
                f"DELETE FROM {table} WHERE id IN (SELECT id FROM {table} ORDER BY id ASC LIMIT %s)",
                (excess,)
            )
    conn.commit()

def _emit_sensor_status(online):
    """Broadcast sensor online/offline status over WebSocket. Throttled to ~1 Hz,
    but always emits immediately on a transition so the UI updates promptly."""
    global _last_status_emit_mono, _last_status_online
    now_mono = time.monotonic()
    if online == _last_status_online and (now_mono - _last_status_emit_mono) < 1.0:
        return
    _last_status_online = online
    _last_status_emit_mono = now_mono
    try:
        socketio.emit("sensor_status", {"online": bool(online)})
    except Exception:
        pass


# ==========================================
# MULTI-TENANT: device auth + per-device ingest pipeline
# ==========================================
_device_cache = {}            # device_id -> {"row": dict|None, "ts": monotonic}
_DEVICE_CACHE_TTL = 30.0
device_state = {}             # device_id -> {"windows":..., "thickness":..., "n":int}
_device_last_seen_push = {}   # device_id -> monotonic (throttle last_seen UPDATE)

def _device_lookup(device_id):
    """Return device row dict from a short-lived cache or the DB; None if unknown."""
    now = time.monotonic()
    ent = _device_cache.get(device_id)
    if ent and now - ent["ts"] < _DEVICE_CACHE_TTL:
        return ent["row"]
    conn, cur = _db_connect()
    if conn is None:
        return ent["row"] if ent else None
    try:
        cur.execute(
            "SELECT device_id, customer_id, device_key_hash, sensor_mode, label, revoked "
            "FROM devices WHERE device_id=%s", (device_id,))
        r = cur.fetchone()
    except Exception:
        return ent["row"] if ent else None
    finally:
        conn.close()
    row = None
    if r:
        row = {"device_id": r[0], "customer_id": r[1], "device_key_hash": r[2],
               "sensor_mode": r[3], "label": r[4], "revoked": r[5]}
    _device_cache[device_id] = {"row": row, "ts": now}
    return row

def _verify_device(device_id, device_key):
    """Validate a device_id + key pair. Returns the device row or None."""
    if not device_id or not device_key:
        return None
    row = _device_lookup(device_id)
    if not row or row.get("revoked"):
        return None
    if not check_password_hash(row["device_key_hash"], device_key):
        return None
    return row

def _load_device_calibration(device_id):
    """Load a device's persisted calibration JSON from the DB; default if none."""
    conn, cur = _db_connect()
    if conn is None:
        return default_thickness_state()
    try:
        cur.execute("SELECT calibration FROM devices WHERE device_id=%s", (device_id,))
        r = cur.fetchone()
    except Exception:
        return default_thickness_state()
    finally:
        conn.close()
    if r and r[0]:
        try:
            st = default_thickness_state()
            st.update(r[0] if isinstance(r[0], dict) else json.loads(r[0]))
            return st
        except Exception:
            return default_thickness_state()
    return default_thickness_state()

def _save_device_calibration(device_id, state):
    """Persist a device's calibration JSON so it survives a server restart."""
    conn, cur = _db_connect()
    if conn is None:
        return
    try:
        cur.execute("UPDATE devices SET calibration=%s WHERE device_id=%s",
                    (json.dumps(state), device_id))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()

def _get_device_state(device_id):
    st = device_state.get(device_id)
    if st is None:
        st = {"windows": {sid: deque(maxlen=FILTER_WINDOW) for sid in ("A", "B", "C")},
              "thickness": _load_device_calibration(device_id), "n": 0,
              "last_raw": {}, "latest": None, "seq": 0}
        device_state[device_id] = st
    return st

def _touch_last_seen(device_id):
    """Update devices.last_seen, throttled to once / 5 s per device."""
    now = time.monotonic()
    if now - _device_last_seen_push.get(device_id, 0.0) < 5.0:
        return
    _device_last_seen_push[device_id] = now
    conn, cur = _db_connect()
    if conn is None:
        return
    try:
        cur.execute("UPDATE devices SET last_seen=NOW() WHERE device_id=%s", (device_id,))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()

def _db_trim_device(cur, conn, device_id):
    """Per-device row cap: never let one tenant evict another's history."""
    for table in (DB_TABLE_FILTERED, DB_TABLE_UNFILTERED, DB_TABLE_THICKNESS, DB_TABLE_THICKNESS_RAW):
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE device_id=%s", (device_id,))
        count = cur.fetchone()[0]
        if count > PER_DEVICE_ROW_CAP:
            excess = count - PER_DEVICE_ROW_CAP
            cur.execute(
                f"DELETE FROM {table} WHERE id IN "
                f"(SELECT id FROM {table} WHERE device_id=%s ORDER BY id ASC LIMIT %s)",
                (device_id, excess))
    conn.commit()

def _process_device_reading(device_id, a, b, c, now):
    """Inline per-device pipeline for a provisioned device: moving-average filter,
    thickness, DB write tagged with device_id, and emit to that device's room."""
    st = _get_device_state(device_id)
    reading = {"A": a, "B": b, "C": c}
    filtered = {}
    for sid in ("A", "B", "C"):
        rv = reading.get(sid)
        if rv is None:
            filtered[sid] = None
            continue
        win = st["windows"][sid]
        win.append(float(rv))
        filtered[sid] = round(sum(win) / len(win), 3)

    tstate = st["thickness"]
    raw_thickness = _compute_thickness(a, b, tstate)
    filt_thickness = _compute_thickness(filtered.get("A"), filtered.get("B"), tstate)

    # Stash the latest reading; the stream loop emits it to this device's room at a
    # steady 5 Hz (emitting from this HTTP worker thread was choppy in threading mode).
    st["last_raw"] = {k: v for k, v in reading.items() if v is not None}
    st["latest"] = {
        "timestamp": now.isoformat(),
        "device_id": device_id,
        "distance_A": a, "distance_B": b, "distance_C": c,
        "thickness": raw_thickness,
    }
    st["seq"] = st.get("seq", 0) + 1

    conn, cur = _db_connect()
    if conn is not None:
        try:
            _db_write(conn, cur, now, reading, filtered, raw_thickness, filt_thickness, device_id=device_id)
            st["n"] += 1
            if st["n"] % 2000 == 0:   # trim ~every 2000 inserts (~7 min @ 5 Hz)
                _db_trim_device(cur, conn, device_id)
        except Exception as e:
            print(f"[ingest dev {device_id}] DB error: {e}", flush=True)
        finally:
            conn.close()


@socketio.on('join_device')
def on_join_device(data):
    """Dashboard subscribes to a single device's live readings."""
    did = (data or {}).get('device_id')
    if did:
        join_room(did)


def stream_ingest_loop():
    """Background thread: emits sensor readings via WebSocket and writes them to the DB.

    First tries to use data received via HTTP ingest (/ingest/readings from pi_client).
    If no ingest data is available and NOT in CLOUD_MODE, falls back to polling
    local CD22 sensors directly.

    In CLOUD_MODE (KVM server), only uses ingest data — the cloud server can't
    reach sensors on the 192.168.5.x LAN.
    """
    consecutive_empty_readings = 0
    db_conn, db_cur = _db_connect()
    inserts_since_trim = 0
    # Rolling buffers for the moving-average filter (one per sensor)
    filter_windows = {sid: deque(maxlen=FILTER_WINDOW) for sid in ("A", "B", "C")}
    # Last emitted seq per device room (so we only emit fresh readings).
    device_emit_seq = {}

    while True:
        if not stream_state["active"]:
            time.sleep(0.1)
            continue

        # Try to get readings from ingest (pi_client HTTP POST)
        reading = {k: v for k, v in last_ingest_reading.items() if v is not None}

        # Sensor freshness / offline detection (CLOUD_MODE). If no fresh ingest
        # within SENSOR_STALE_SECONDS, the sensors (or the pi_client link) are
        # down. Don't keep re-emitting the last values as if they were live —
        # clear them so the UI can show "Sensors disconnected" and the graph
        # stops advancing on stale data.
        if CLOUD_MODE and (time.monotonic() - last_ingest_monotonic) > SENSOR_STALE_SECONDS:
            reading = {}
            last_ingest_reading["A"] = None
            last_ingest_reading["B"] = None
            last_ingest_reading["C"] = None

        # If no ingest data available AND not CLOUD_MODE AND there are active local
        # sensors, poll them directly. Skip in CLOUD_MODE — cloud server can't
        # reach sensors on the 192.168.5.x LAN and TCP connections would block.
        if not reading and not CLOUD_MODE and active_sensors_map:
            local_readings = poll_local_sensors()
            if local_readings:
                # Update last_ingest_reading so other endpoints can use the data too
                for sid, val in local_readings.items():
                    last_ingest_reading[sid] = val
                reading = local_readings
                consecutive_empty_readings = 0
            else:
                consecutive_empty_readings += 1
                # Only log every 100 consecutive failures to avoid spamming
                if consecutive_empty_readings % 100 == 1:
                    print(f"[Stream] No sensor data available ({consecutive_empty_readings} consecutive empty reads)")
        elif reading:
            consecutive_empty_readings = 0

        # Tell the frontend whether sensors are live or disconnected.
        _emit_sensor_status(bool(reading))

        if reading:
            now = datetime.datetime.now()
            state = get_thickness_state()

            # Moving-average (filtered) values: push each raw reading into its
            # rolling window and take the mean. Sensors absent from this reading
            # keep their previous window untouched and report None.
            filtered = {}
            for sid in ("A", "B", "C"):
                rv = reading.get(sid)
                if rv is None:
                    filtered[sid] = None
                    continue
                win = filter_windows[sid]
                win.append(float(rv))
                filtered[sid] = round(sum(win) / len(win), 3)

            # Raw and filtered thickness (opposite mode)
            raw_thickness = _compute_thickness(reading.get("A"), reading.get("B"), state)
            filt_thickness = _compute_thickness(filtered.get("A"), filtered.get("B"), state)

            # Check email alert thresholds on the raw thickness reading
            if raw_thickness is not None:
                try:
                    check_thresholds_and_alert(raw_thickness, sensor_id="Opposite Sensors")
                except Exception:
                    pass  # Don't let alert errors disrupt the stream

            payload = {
                "timestamp": now.isoformat(),
                "distance_A": reading.get("A"),
                "distance_B": reading.get("B"),
                "distance_C": reading.get("C"),
                "thickness": raw_thickness,
                "device_id": LEGACY_DEVICE_ID,
            }
            # Room-scoped so the multi-tenant dashboard only receives the device
            # it joined. The legacy single install lives in the dev_legacy room.
            socketio.emit("sensor_reading", payload, room=LEGACY_DEVICE_ID)

            # Write to DB; reconnect once on failure
            if db_conn is None:
                db_conn, db_cur = _db_connect()
            if db_conn is not None:
                try:
                    _db_write(db_conn, db_cur, now, reading, filtered, raw_thickness, filt_thickness)
                    inserts_since_trim += 1
                    if inserts_since_trim >= 10000:
                        _db_trim(db_cur, db_conn)
                        inserts_since_trim = 0
                except Exception as e:
                    print(f"[Stream DB] write error: {e}")
                    try:
                        db_conn.rollback()
                    except Exception:
                        pass
                    db_conn, db_cur = _db_connect()

        # Per-device live emit (smooth, single-threaded). Push each provisioned
        # device's latest reading to its own socket room when a new reading has
        # arrived since the previous tick. Runs at the same 5 Hz as the loop.
        for did, st in list(device_state.items()):
            seq = st.get("seq", 0)
            if st.get("latest") and seq != device_emit_seq.get(did):
                device_emit_seq[did] = seq
                try:
                    socketio.emit("sensor_reading", st["latest"], room=did)
                except Exception:
                    pass

        target_delay = 1.0 / max(stream_state["target_rate_hz"], 1.0)
        time.sleep(target_delay)

def start_background_tasks():
    """Start background threads for streaming."""
    t = threading.Thread(target=stream_ingest_loop, daemon=True)
    t.start()
    from email_alert_routes import start_background_tasks as start_email_tasks
    start_email_tasks()

# ==========================================
# MAIN
# ==========================================
if __name__ == '__main__':
    print(f"========== Starting Merged Server (PORT {SERVER_PORT}) ==========")
    print(f"  Cloud Mode: {CLOUD_MODE}")
    init_config_file()
    init_network_config_file()
    refresh_sensor_configs()
    init_thickness_state_file()
    init_thickness_limit_file()
    init_db()
    start_background_tasks()
    print(f"  Active sensors: {list(active_sensors_map.keys())}")
    print(f"==========================================")
    socketio.run(app, host=SERVER_IP, port=SERVER_PORT, debug=False, allow_unsafe_werkzeug=True)