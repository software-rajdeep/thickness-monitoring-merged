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
from psycopg2 import extras
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, request, jsonify, send_from_directory
# flask_cors removed — CORS handled exclusively by Traefik to avoid duplicate headers
from flask_socketio import SocketIO
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

# --- FILE CONFIG ---
CONFIG_FILE_PATH = os.path.join(BASE_DIR, "sensor_config.json")
NETWORK_CONFIG_FILE_PATH = os.path.join(BASE_DIR, "sensor_network.json")
THICKNESS_STATE_FILE_PATH = os.path.join(BASE_DIR, "thickness_state.json")

# --- ZERO OFFSET ---
ZERO_OFFSET_MM = 35.0

STX = 0x02
ETX = 0x03

# ==========================================
# CONFIG FILE HELPERS
# ==========================================
def init_config_file():
    """Creates a default JSON config file if one doesn't exist."""
    try:
        if not os.path.exists(CONFIG_FILE_PATH):
            print("--- Creating default sensor_config.json ---")
            default_config = {
                "A": {"ip": "192.168.5.200", "port": 8234, "name": "Sensor A"},
                "B": {"ip": "192.168.5.201", "port": 8234, "name": "Sensor B"},
                "C": {"ip": "192.168.5.202", "port": 8234, "name": "Sensor C"},
            }
            with open(CONFIG_FILE_PATH, 'w') as f:
                json.dump(default_config, f, indent=4)
    except Exception as e:
        print(f"Warning: Could not init config file: {e}")

def init_network_config_file():
    """Creates a default network config JSON file if one doesn't exist."""
    try:
        if not os.path.exists(NETWORK_CONFIG_FILE_PATH):
            print("--- Creating default sensor_network.json ---")
            default_config = {
                "A": {"ip": "192.168.5.200", "port": 8234, "name": "Sensor A", "sensor_type": "cd22"},
                "B": {"ip": "192.168.5.201", "port": 8234, "name": "Sensor B", "sensor_type": "cd22"},
                "C": {"ip": "192.168.5.202", "port": 8234, "name": "Sensor C", "sensor_type": "cd22"},
            }
            with open(NETWORK_CONFIG_FILE_PATH, 'w') as f:
                json.dump(default_config, f, indent=4)
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
            config = SENSOR_CONFIGS
        for sensor_id, cfg in config.items():
            sid_upper = sensor_id.upper()
            if sid_upper not in {"A", "B", "C"}:
                continue
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
            SENSOR_CONFIGS = {
                "A": {"ip": "192.168.5.200", "port": 8234, "name": "Sensor A"},
                "B": {"ip": "192.168.5.201", "port": 8234, "name": "Sensor B"},
                "C": {"ip": "192.168.5.202", "port": 8234, "name": "Sensor C"},
            }
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

        # Create default users if table is empty
        cur.execute(f"SELECT COUNT(*) FROM {DB_TABLE_USERS}")
        count = cur.fetchone()[0]
        if count == 0:
            default_users = [
                ("superadmin", "superadmin123", "superadmin"),
                ("admin",      "admin123",      "admin"),
                ("supervisor", "super123",      "supervisor"),
                ("worker",     "worker123",     "worker"),
            ]
            for uname, pwd, role in default_users:
                pw_hash = generate_password_hash(pwd)
                cur.execute(
                    f"INSERT INTO {DB_TABLE_USERS} (username, password_hash, role) VALUES (%s, %s, %s)",
                    (uname, pw_hash, role)
                )
            conn.commit()
            print("--- Created default users ---")

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

# Register email alert routes
from email_alert_routes import email_alerts_bp
app.register_blueprint(email_alerts_bp)

# CORS(app) removed — handled by Traefik middleware
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

active_sensors_map = {}
sensors_lock = threading.Lock()
thickness_state = load_thickness_state()

stream_state = {
    "active": False,
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

@app.route('/thickness/state', methods=['GET'])
def thickness_state_api():
    return jsonify(get_thickness_state()), 200

@app.route('/thickness/setup-ready', methods=['POST'])
def thickness_setup_ready():
    if CLOUD_MODE or not active_sensors_map:
        reading = {k: v for k, v in last_ingest_reading.items() if v is not None}
        if not reading:
            return jsonify({"error": "No sensor reading received yet. Ensure the Pi client is running and sending data."}), 400
        updated_state = default_thickness_state()
        updated_state["setup_ready"] = True
        updated_state["captured_at"] = datetime.datetime.now().isoformat()
        for sid, val in reading.items():
            updated_state["reference_readings"][sid] = round(float(val), 3)
        set_thickness_state(updated_state)
        return jsonify({
            "message": "Starting readings captured successfully.",
            "setup_ready": True,
            "captured_at": updated_state.get("captured_at"),
            "reference_readings": updated_state.get("reference_readings", {}),
            "captured_readings": reading,
        }), 200
    captured_readings, failures = capture_starting_readings()
    if not captured_readings:
        return jsonify({"error": "Unable to capture starting readings."}), 500
    response_payload = {
        "message": "Starting readings captured successfully.",
        "setup_ready": True,
        "captured_at": get_thickness_state().get("captured_at"),
        "reference_readings": get_thickness_state().get("reference_readings", {}),
        "captured_readings": captured_readings,
    }
    if failures:
        response_payload["warnings"] = [f"Sensor {sensor_id} did not return a reading." for sensor_id in failures]
    return jsonify(response_payload), 200

@app.route('/thickness/calibration', methods=['POST'])
def thickness_calibration():
    if CLOUD_MODE:
        data = request.json or {}
        try:
            reference_thickness = float(data.get("reference_thickness"))
        except (TypeError, ValueError):
            return jsonify({"error": "A valid reference thickness is required."}), 400
        if reference_thickness < 0:
            return jsonify({"error": "Reference thickness must be zero or greater."}), 400
        reading = {k: v for k, v in last_ingest_reading.items() if v is not None}
        if not reading:
            return jsonify({"error": "No sensor reading received yet. Ensure the Pi client is running and sending data."}), 400
        current_state = get_thickness_state()
        updated_state = default_thickness_state()
        updated_state["setup_ready"] = current_state.get("setup_ready", False)
        updated_state["captured_at"] = current_state.get("captured_at")
        updated_state["reference_readings"] = normalize_sensor_readings(current_state.get("reference_readings", {}))
        updated_state["calibration_completed"] = True
        updated_state["calibration_active"] = True
        updated_state["calibration_captured_at"] = datetime.datetime.now().isoformat()
        updated_state["calibration_reference_thickness"] = round(float(reference_thickness), 3)
        for sensor_id, reading_val in reading.items():
            updated_state["calibration_baseline_readings"][sensor_id] = reading_val
        set_thickness_state(updated_state)
        captured_readings = reading
        failures = []
    else:
        if not active_sensors_map:
            return jsonify({"error": "No active sensors available."}), 400
        data = request.json or {}
        try:
            reference_thickness = float(data.get("reference_thickness"))
        except (TypeError, ValueError):
            return jsonify({"error": "A valid reference thickness is required."}), 400
        if reference_thickness < 0:
            return jsonify({"error": "Reference thickness must be zero or greater."}), 400
        captured_readings, failures = capture_calibration(reference_thickness)
        if not captured_readings:
            return jsonify({"error": "Unable to capture calibration readings."}), 500

    response_payload = {
        "message": "Calibration saved successfully.",
        "calibration_active": True,
        "calibration_captured_at": get_thickness_state().get("calibration_captured_at"),
        "calibration_reference_thickness": get_thickness_state().get("calibration_reference_thickness", 0.0),
        "calibration_baseline_readings": get_thickness_state().get("calibration_baseline_readings", {}),
        "captured_readings": captured_readings,
    }
    if failures:
        response_payload["warnings"] = [f"Sensor {sensor_id} did not return a reading." for sensor_id in failures]
    return jsonify(response_payload), 200

@app.route('/thickness/gap', methods=['POST'])
def thickness_gap_set():
    """Set the distance between the two sensor faces (opposite mode)."""
    data = request.json or {}
    try:
        gap_distance = float(data.get("gap_distance"))
    except (TypeError, ValueError):
        return jsonify({"error": "A valid gap distance is required."}), 400
    if gap_distance <= 0:
        return jsonify({"error": "Gap distance must be greater than zero."}), 400
    current_state = get_thickness_state()
    current_state["gap_distance"] = round(gap_distance, 3)
    current_state["calibration_completed"] = True
    current_state["calibration_active"] = True
    set_thickness_state(current_state)

    return jsonify({
        "message": "Gap distance set successfully.",
        "gap_distance": current_state["gap_distance"],
        "calibration_active": True
    }), 200

@app.route('/thickness/auto-gap', methods=['POST'])
def thickness_auto_gap_set():
    """Auto-calculate gap distance using object thickness (opposite mode)."""
    if not CLOUD_MODE and not active_sensors_map:
        return jsonify({"error": "No active sensors available."}), 400
    data = request.json or {}
    try:
        object_thickness = float(data.get("object_thickness"))
    except (TypeError, ValueError):
        return jsonify({"error": "A valid object thickness is required."}), 400
    if object_thickness <= 0:
        return jsonify({"error": "Object thickness must be greater than zero."}), 400
    if CLOUD_MODE:
        reading = {k: v for k, v in last_ingest_reading.items() if v is not None}
        if len(reading) < 2:
            return jsonify({"error": "Waiting for readings from both sensors. Ensure Pi client is running."}), 400
        captured_readings = {k: round(float(v), 3) for k, v in reading.items()}
        failures = []
    else:
        captured_readings, failures = capture_active_sensor_readings()
    if not captured_readings or len(captured_readings) < 2:
        return jsonify({"error": "Unable to capture readings from both sensors."}), 500
    dist_A = captured_readings.get("A")
    dist_B = captured_readings.get("B")
    if dist_A is None or dist_B is None:
        return jsonify({"error": "Both sensor readings are required."}), 500
    total_gap = 2 * ZERO_OFFSET_MM + float(dist_A) + float(dist_B) + object_thickness
    tol_min = data.get("thickness_tolerance_min")
    tol_max = data.get("thickness_tolerance_max")
    if tol_min is not None:
        try: tol_min = float(tol_min)
        except: tol_min = None
    if tol_max is not None:
        try: tol_max = float(tol_max)
        except: tol_max = None
    current_state = get_thickness_state()
    current_state["gap_distance"] = round(total_gap, 3)
    current_state["calibration_completed"] = True
    current_state["calibration_active"] = True
    current_state["auto_gap_active"] = True
    current_state["object_thickness"] = object_thickness
    current_state["thickness_tolerance_min"] = tol_min
    current_state["thickness_tolerance_max"] = tol_max
    set_thickness_state(current_state)

    response = {
        "message": "Auto-gap setup completed successfully.",
        "gap_distance": round(total_gap, 3),
        "calibration_active": True,
        "auto_gap_active": True,
        "object_thickness": object_thickness,
        "captured_readings": captured_readings,
        "thickness_tolerance_min": tol_min,
        "thickness_tolerance_max": tol_max,
    }
    if failures:
        response["warnings"] = [f"Sensor {sensor_id} did not return a reading." for sensor_id in failures]
    return jsonify(response), 200

@app.route('/thickness/calibration/reset', methods=['POST'])
def thickness_calibration_reset():
    updated_state = reset_calibration_state()
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
    """Receive sensor data from Pi client."""
    auth_header = request.headers.get('Authorization', '')
    expected_token = f"Bearer {INGEST_API_KEY}"
    # Accept with or without Bearer prefix, and from any source (local proxy may strip)
    token_only = auth_header.replace("Bearer ", "").replace("bearer ", "").strip()
    expected_only = expected_token.replace("Bearer ", "").strip()
    if token_only != expected_only:
        # Also accept requests from localhost without auth (internal nginx proxy)
        if request.remote_addr not in ('127.0.0.1', '::1'):
            return jsonify({"error": "Unauthorized"}), 401
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400
    # Normalize keys: accept sensor_A, sensor_B, sensor_C OR A, B, C
    a = data.get("A") if "A" in data else data.get("sensor_A")
    b = data.get("B") if "B" in data else data.get("sensor_B")
    c = data.get("C") if "C" in data else data.get("sensor_C")
    now = datetime.datetime.now()
    last_ingest_reading["A"] = float(a) if a is not None else None
    last_ingest_reading["B"] = float(b) if b is not None else None
    last_ingest_reading["C"] = float(c) if c is not None else None
    return jsonify({"message": "Data received", "timestamp": now.isoformat()}), 200

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
    """Write sensor config register(s). Accepts JSON with 'command' (int), 'value' (int), optional 'sensor'."""
    data = request.json or {}
    command = data.get("command")
    value = data.get("value")
    sensor_id = data.get("sensor", "A").upper()
    if command is None or value is None:
        return jsonify({"error": "Both 'command' and 'value' are required"}), 400
    if sensor_id not in active_sensors_map:
        return jsonify({"error": f"Sensor {sensor_id} not found"}), 404
    sensor = active_sensors_map[sensor_id]
    cmd_int = int(command)
    val_int = int(value) & 0xFFFF
    cmd_lsb = cmd_int & 0xFF
    cmd_msb = (cmd_int >> 8) & 0xFF
    val_lsb = val_int & 0xFF
    val_msb = (val_int >> 8) & 0xFF
    cmd_bytes = bytes([STX, 0x42, cmd_msb, cmd_lsb, val_msb, val_lsb, ETX, (0x42 ^ cmd_msb ^ cmd_lsb ^ val_msb ^ val_lsb)])
    try:
        with sensor.lock:
            if not sensor.connected and not sensor.connect():
                return jsonify({"error": f"Cannot connect to sensor {sensor_id}"}), 500
            sensor.sock.settimeout(0.1)
            sensor.sock.sendall(cmd_bytes)
            resp = sensor.sock.recv(6)
            sensor.sock.settimeout(SENSOR_TIMEOUT)
        if resp and len(resp) == 6 and resp[1] == 0x06:
            return jsonify({"sensor": sensor_id, "command": command, "value": value, "success": True}), 200
        return jsonify({"error": "Write command failed"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
    if "target_rate_hz" in data:
        stream_state["target_rate_hz"] = float(data["target_rate_hz"])
    return jsonify({"message": "Stream config updated", "target_rate_hz": stream_state["target_rate_hz"]}), 200

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
def stream_ingest_loop():
    """Background thread that emits sensor readings via WebSocket."""
    while True:
        if not stream_state["active"]:
            time.sleep(0.1)
            continue
        reading = {k: v for k, v in last_ingest_reading.items() if v is not None}
        if reading:
            now = datetime.datetime.now()
            thickness_val = None
            state = get_thickness_state()
            gap = state.get("gap_distance", 0.0)
            if gap > 0 and "A" in reading and "B" in reading:
                thickness_val = calculate_opposite_thickness(reading["A"], reading["B"])
            payload = {
                "timestamp": now.isoformat(),
                "distance_A": reading.get("A"),
                "distance_B": reading.get("B"),
                "distance_C": reading.get("C"),
                "thickness": thickness_val,
            }
            socketio.emit("sensor_reading", payload)
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
    init_db()
    start_background_tasks()
    print(f"  Active sensors: {list(active_sensors_map.keys())}")
    print(f"==========================================")
    socketio.run(app, host=SERVER_IP, port=SERVER_PORT, debug=False, allow_unsafe_werkzeug=True)