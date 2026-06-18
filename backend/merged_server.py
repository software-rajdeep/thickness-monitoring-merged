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

# --- PROTOCOL CONSTANTS ---
STX = 0x02
ETX = 0x03
CMD_READ    = 0x52  
CMD_WRITE   = 0x57  

# --- SENSOR ZERO OFFSET ---
ZERO_OFFSET_MM = 35.0

# --- MODE DETECTION ---
MODE_SBS  = "side-by-side"   # 3 sensors (A, B, C)
MODE_OPP  = "opposite"       # 2 sensors (A, B)

# ==========================================
# FILE INITIALIZATION
# ==========================================
def get_default_sensor_configs(mode=None):
    """Return sensor configs based on mode. If mode is None, return both."""
    sbs = {
        "A": {"ip": "192.168.1.7", "port": 8234, "name": "Sensor A"},
        "B": {"ip": "192.168.1.8", "port": 8234, "name": "Sensor B"},
        "C": {"ip": "192.168.1.9", "port": 8234, "name": "Sensor C"}
    }
    opp = {
        "A": {"ip": "192.168.1.7", "port": 8234, "name": "Sensor A"},
        "B": {"ip": "192.168.1.8", "port": 8234, "name": "Sensor B"}
    }
    if mode == MODE_OPP:
        return opp.copy()
    return sbs.copy()  # default to SBS

def init_config_file():
    """Creates a default JSON config file if one doesn't exist."""
    if not os.path.exists(CONFIG_FILE_PATH):
        print("--- Creating default sensor_config.json ---")
        default_config = {
            "global_settings": {
                "stream_rate_hz": 5.0,
                "trim_percentage": 10
            },
            "sensor_A": {
                "sampling_period": "500us",
                "averaging": 128,
                "alarm_output": "N.O.",
                "output_polarity": "Light_ON"
            },
            "sensor_B": {
                "sampling_period": "500us",
                "averaging": 128,
                "alarm_output": "N.O.",
                "output_polarity": "Light_ON"
            },
            "sensor_C": {
                "sampling_period": "500us",
                "averaging": 128,
                "alarm_output": "N.O.",
                "output_polarity": "Light_ON"
            }
        }
        with open(CONFIG_FILE_PATH, 'w') as f:
            json.dump(default_config, f, indent=4)

def init_network_config_file():
    if not os.path.exists(NETWORK_CONFIG_FILE_PATH):
        with open(NETWORK_CONFIG_FILE_PATH, 'w') as file_handle:
            json.dump(get_default_sensor_configs(MODE_SBS), file_handle, indent=4)

def normalize_network_config(payload, base_config=None):
    base = base_config or get_default_sensor_configs(MODE_SBS)
    normalized = {}
    errors = []

    if not isinstance(payload, dict):
        return base.copy(), ["Payload must be a JSON object."]

    for sid, defaults in base.items():
        entry = payload.get(sid)
        if entry is None:
            entry = payload.get(sid.upper())
        if entry is None:
            entry = payload.get(sid.lower())

        if entry is None:
            normalized[sid] = defaults.copy()
            continue

        if not isinstance(entry, dict):
            normalized[sid] = defaults.copy()
            errors.append(f"Sensor {sid} config must be an object.")
            continue

        ip = str(entry.get("ip", defaults["ip"])).strip()
        try:
            port = int(entry.get("port", defaults["port"]))
        except (TypeError, ValueError):
            port = defaults["port"]
            errors.append(f"Sensor {sid} port must be a number.")
        name = str(entry.get("name", defaults["name"])).strip() or defaults["name"]

        normalized[sid] = {"ip": ip, "port": port, "name": name}

    return normalized, errors

def load_network_config():
    if not os.path.exists(NETWORK_CONFIG_FILE_PATH):
        return get_default_sensor_configs(MODE_SBS).copy()
    try:
        with open(NETWORK_CONFIG_FILE_PATH, 'r') as file_handle:
            payload = json.load(file_handle)
        # Detect mode from stored config: if only A & B → opposite mode, otherwise SBS
        keys = set(k.upper() for k in payload.keys() if isinstance(payload[k], dict))
        base = get_default_sensor_configs(MODE_OPP) if keys == {"A", "B"} else get_default_sensor_configs(MODE_SBS)
        normalized, _ = normalize_network_config(payload, base)
        return normalized
    except Exception:
        return get_default_sensor_configs(MODE_SBS).copy()

def save_network_config(config):
    with open(NETWORK_CONFIG_FILE_PATH, 'w') as file_handle:
        json.dump(config, file_handle, indent=4)

def rebuild_active_sensors():
    global active_sensors_map
    new_map = {}

    for sid, config in SENSOR_CONFIGS.items():
        print(f"Checking {config.get('name', f'Sensor {sid}')} at {config['ip']}...")
        temp_sensor = CD22Sensor(config["ip"], config["port"], config.get("name", f"Sensor {sid}"))
        connected = False
        for _ in range(3):
            if temp_sensor.connect():
                connected = True
                break
            time.sleep(1)
        if connected:
            new_map[sid] = temp_sensor
            print(f"  -> SUCCESS! Added {config.get('name', f'Sensor {sid}')} to active pool.")
        else:
            temp_sensor.disconnect()
            print(f"  -> OFFLINE. Ignoring {config.get('name', f'Sensor {sid}')} for this session.")

    with sensors_lock:
        for sensor in active_sensors_map.values():
            sensor.disconnect()
        active_sensors_map = new_map

    return list(new_map.keys())

def refresh_sensor_configs(new_config=None):
    global SENSOR_CONFIGS
    if new_config is None:
        SENSOR_CONFIGS = load_network_config()
    else:
        SENSOR_CONFIGS = new_config
        save_network_config(SENSOR_CONFIGS)
    return rebuild_active_sensors()

init_network_config_file()
SENSOR_CONFIGS = load_network_config()

def get_current_mode():
    """Determine which mode based on active sensors count."""
    num_sensors = len(SENSOR_CONFIGS)
    return MODE_OPP if num_sensors <= 2 else MODE_SBS

def default_thickness_state_sbs():
    return {
        "setup_ready": False,
        "captured_at": None,
        "reference_readings": {"A": None, "B": None, "C": None},
        "calibration_completed": False,
        "calibration_active": False,
        "calibration_captured_at": None,
        "calibration_reference_thickness": 0.0,
        "calibration_baseline_readings": {"A": None, "B": None, "C": None}
    }

def default_thickness_state_opp():
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
        "thickness_tolerance_max": None
    }

def default_thickness_state():
    mode = get_current_mode()
    if mode == MODE_OPP:
        return default_thickness_state_opp()
    return default_thickness_state_sbs()

def normalize_sensor_readings(raw_readings):
    mode = get_current_mode()
    if mode == MODE_OPP:
        normalized = {"A": None, "B": None}
    else:
        normalized = {"A": None, "B": None, "C": None}
    if not isinstance(raw_readings, dict):
        return normalized
    for sensor_id in normalized.keys():
        value = raw_readings.get(sensor_id)
        normalized[sensor_id] = float(value) if value is not None else None
    return normalized

def load_thickness_state():
    if not os.path.exists(THICKNESS_STATE_FILE_PATH):
        return default_thickness_state()
    try:
        with open(THICKNESS_STATE_FILE_PATH, 'r') as file_handle:
            loaded_state = json.load(file_handle)
    except Exception:
        return default_thickness_state()
    state = default_thickness_state()
    state["setup_ready"] = bool(loaded_state.get("setup_ready", False))
    state["captured_at"] = loaded_state.get("captured_at")
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
    state["reference_readings"] = normalize_sensor_readings(loaded_state.get("reference_readings", {}))
    state["calibration_baseline_readings"] = normalize_sensor_readings(
        loaded_state.get("calibration_baseline_readings", {})
    )
    # Load opposite-specific fields
    gap_distance = loaded_state.get("gap_distance", 0.0)
    try:
        state["gap_distance"] = float(gap_distance)
    except (TypeError, ValueError):
        state["gap_distance"] = 0.0
    state["auto_gap_active"] = bool(loaded_state.get("auto_gap_active", False))
    obj_thickness = loaded_state.get("object_thickness")
    if obj_thickness is not None:
        try:
            state["object_thickness"] = float(obj_thickness)
        except (TypeError, ValueError):
            state["object_thickness"] = None
    tol_min = loaded_state.get("thickness_tolerance_min")
    if tol_min is not None:
        try:
            state["thickness_tolerance_min"] = float(tol_min)
        except (TypeError, ValueError):
            state["thickness_tolerance_min"] = None
    tol_max = loaded_state.get("thickness_tolerance_max")
    if tol_max is not None:
        try:
            state["thickness_tolerance_max"] = float(tol_max)
        except (TypeError, ValueError):
            state["thickness_tolerance_max"] = None
    return state

def save_thickness_state(state):
    with open(THICKNESS_STATE_FILE_PATH, 'w') as file_handle:
        json.dump(state, file_handle, indent=4)

def init_thickness_state_file():
    if not os.path.exists(THICKNESS_STATE_FILE_PATH):
        save_thickness_state(default_thickness_state())

def get_thickness_state():
    return thickness_state

def set_thickness_state(new_state):
    global thickness_state
    thickness_state = new_state
    save_thickness_state(thickness_state)

def capture_starting_readings():
    captured_readings, failures = capture_active_sensor_readings()
    if not captured_readings:
        return None, failures
    updated_state = default_thickness_state()
    updated_state["setup_ready"] = True
    updated_state["captured_at"] = datetime.datetime.now().isoformat()
    for sensor_id, reading in captured_readings.items():
        updated_state["reference_readings"][sensor_id] = reading
    set_thickness_state(updated_state)
    return captured_readings, failures

def capture_active_sensor_readings():
    captured_readings = {}
    failures = []
    for sensor_id, sensor in active_sensors_map.items():
        reading = sensor.get_single_measurement()
        if reading is None:
            failures.append(sensor_id)
            continue
        captured_readings[sensor_id] = round(float(reading), 3)
    return captured_readings, failures

def capture_calibration(reference_thickness):
    captured_readings, failures = capture_active_sensor_readings()
    if not captured_readings:
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
    for sensor_id, reading in captured_readings.items():
        updated_state["calibration_baseline_readings"][sensor_id] = reading
    set_thickness_state(updated_state)
    return captured_readings, failures

def reset_calibration_state():
    updated_state = get_thickness_state().copy()
    updated_state["calibration_completed"] = False
    updated_state["calibration_active"] = False
    updated_state["calibration_captured_at"] = None
    updated_state["calibration_reference_thickness"] = 0.0
    mode = get_current_mode()
    if mode == MODE_OPP:
        updated_state["calibration_baseline_readings"] = {"A": None, "B": None}
        updated_state["gap_distance"] = 0.0
        updated_state["auto_gap_active"] = False
        updated_state["object_thickness"] = None
        updated_state["thickness_tolerance_min"] = None
        updated_state["thickness_tolerance_max"] = None
    else:
        updated_state["calibration_baseline_readings"] = {"A": None, "B": None, "C": None}
    set_thickness_state(updated_state)
    return updated_state

def calculate_opposite_thickness(dist_A, dist_B):
    """Calculate thickness when sensors are on opposite sides."""
    state = get_thickness_state()
    gap = state.get("gap_distance", 0.0)
    if gap <= 0:
        return None
    if dist_A is None or dist_B is None:
        return None
    actual_dist_A = ZERO_OFFSET_MM + float(dist_A)
    actual_dist_B = ZERO_OFFSET_MM + float(dist_B)
    thickness = gap - actual_dist_A - actual_dist_B
    if thickness < 0:
        thickness = 0.0
    return round(thickness, 3)

def calculate_thickness_sbs(sensor_id, current_reading):
    """Calculate thickness for side-by-side mode (per-sensor)."""
    state = get_thickness_state()
    if state.get("calibration_completed"):
        baseline_reading = state.get("calibration_baseline_readings", {}).get(sensor_id)
        reference_thickness = state.get("calibration_reference_thickness", 0.0)
        if baseline_reading is None:
            return round(float(reference_thickness), 3)
        thickness = float(reference_thickness) + (float(baseline_reading) - float(current_reading))
        return round(thickness, 3)
    reference_reading = state["reference_readings"].get(sensor_id)
    if reference_reading is None:
        return round(float(current_reading), 3)
    thickness = float(reference_reading) - float(current_reading)
    return round(max(thickness, 0.0), 3)

# ==========================================
# DATABASE INITIALIZATION
# ==========================================
def init_db():
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
        cur = conn.cursor()
        
        # SBS tables (sensor_a, sensor_b, sensor_c)
        for table in [DB_TABLE_FILTERED, DB_TABLE_UNFILTERED]:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {table} (
                    id INTEGER PRIMARY KEY,
                    timestamp TIMESTAMP,
                    sensor_a REAL,
                    sensor_b REAL,
                    sensor_c REAL
                )
            """)
        
        # Opposite tables (sensor_a, sensor_b, thickness)
        for table in [DB_TABLE_THICKNESS, DB_TABLE_THICKNESS_RAW]:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {table} (
                    id INTEGER PRIMARY KEY,
                    timestamp TIMESTAMP,
                    sensor_a REAL,
                    sensor_b REAL,
                    thickness REAL
                )
            """)
        
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {DB_TABLE_USERS} (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                role VARCHAR(50) NOT NULL
            )
        """)
        
        # User calibrations table — per-user calibration state
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {DB_TABLE_USER_CALIBRATIONS} (
                username VARCHAR(50) PRIMARY KEY,
                calibration_json TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cur.execute(f"SELECT COUNT(*) FROM {DB_TABLE_USERS}")
        if cur.fetchone()[0] == 0:
            print("--- Seeding Default Users into Database ---")
            default_users = [
                ('superadmin', generate_password_hash('superadmin123'), 'superadmin'),
                ('admin', generate_password_hash('admin123'), 'admin'),
                ('supervisor', generate_password_hash('super123'), 'supervisor'),
                ('worker', generate_password_hash('worker123'), 'worker')
            ]
            extras.execute_values(
                cur, 
                f"INSERT INTO {DB_TABLE_USERS} (username, password_hash, role) VALUES %s", 
                default_users
            )
            
        conn.commit()
        cur.close()
        conn.close()
        print("--- PostgreSQL Database Initialized Successfully ---")
    except Exception as e:
        print(f"!!! Database Init Failed: {e} !!!")

def get_next_db_id(cursor, table_name, max_rows):
    cursor.execute(f"SELECT COUNT(*), MAX(id) FROM {table_name}")
    count, max_id = cursor.fetchone()
    if count == 0: return 1
    elif count < max_rows: return max_id + 1
    else:
        cursor.execute(f"SELECT id FROM {table_name} ORDER BY timestamp ASC LIMIT 1")
        res = cursor.fetchone()
        return res[0] if res else 1

# ==========================================
# USER CALIBRATION HELPERS
# ==========================================
def save_user_calibration_to_db(username, calibration_state):
    """Save the given calibration state to the user's DB record."""
    try:
        # Strip out runtime-only fields before saving
        save_data = {
            "setup_ready": calibration_state.get("setup_ready", False),
            "captured_at": calibration_state.get("captured_at"),
            "reference_readings": calibration_state.get("reference_readings", {}),
            "calibration_completed": calibration_state.get("calibration_completed", False),
            "calibration_active": calibration_state.get("calibration_active", False),
            "calibration_captured_at": calibration_state.get("calibration_captured_at"),
            "calibration_reference_thickness": calibration_state.get("calibration_reference_thickness", 0.0),
            "calibration_baseline_readings": calibration_state.get("calibration_baseline_readings", {}),
            "gap_distance": calibration_state.get("gap_distance", 0.0),
            "auto_gap_active": calibration_state.get("auto_gap_active", False),
            "object_thickness": calibration_state.get("object_thickness"),
            "thickness_tolerance_min": calibration_state.get("thickness_tolerance_min"),
            "thickness_tolerance_max": calibration_state.get("thickness_tolerance_max"),
        }
        cal_json = json.dumps(save_data)
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
        cur = conn.cursor()
        cur.execute(f"""
            INSERT INTO {DB_TABLE_USER_CALIBRATIONS} (username, calibration_json, updated_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (username) 
            DO UPDATE SET calibration_json = %s, updated_at = CURRENT_TIMESTAMP
        """, (username, cal_json, cal_json))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"!!! Failed to save user calibration for {username}: {e} !!!")
        return False

def load_user_calibration_from_db(username):
    """Load saved calibration state for a user from DB. Returns None if none exists."""
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
        cur = conn.cursor()
        cur.execute(
            f"SELECT calibration_json FROM {DB_TABLE_USER_CALIBRATIONS} WHERE username = %s",
            (username,)
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            return json.loads(row[0])
        return None
    except Exception as e:
        print(f"!!! Failed to load user calibration for {username}: {e} !!!")
        return None

def delete_user_calibration_from_db(username):
    """Delete saved calibration state for a user from DB."""
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
        cur = conn.cursor()
        cur.execute(
            f"DELETE FROM {DB_TABLE_USER_CALIBRATIONS} WHERE username = %s",
            (username,)
        )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"!!! Failed to delete user calibration for {username}: {e} !!!")
        return False

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
            new_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            new_sock.settimeout(SENSOR_TIMEOUT)
            new_sock.connect((self.ip, self.port))
            with self.lock:
                self.sock = new_sock
                self.connected = True
            return True
        except Exception:
            with self.lock: self.connected = False
            return False

    def disconnect(self):
        with self.lock:
            if self.sock:
                try: self.sock.close()
                except: pass
                self.sock = None
                self.connected = False

    def _flush_buffer(self):
        try:
            self.sock.settimeout(0.1)
            while True:
                data = self.sock.recv(1024)
                if not data: break
        except: pass
        finally:
            if self.sock: self.sock.settimeout(SENSOR_TIMEOUT)

    def send_frame_raw(self, cmd, d1, d2):
        bcc = cmd ^ d1 ^ d2
        frame = bytes([STX, cmd, d1, d2, ETX, bcc])
        try:
            self._flush_buffer()
            self.sock.sendall(frame)
            return True
        except Exception:
            self.connected = False
            return False

    def read_response_raw(self, length=6):
        try:
            resp = self.sock.recv(length)
            return resp if len(resp) == length else None
        except Exception:
            self.connected = False
            return None

    def generic_read(self, addr_h, addr_l):
        with self.lock:
            if not self.connect(): return None
            if not self.send_frame_raw(CMD_READ, addr_h, addr_l): return None
            resp = self.read_response_raw()
            if resp and resp[1] == 0x06: return (resp[2], resp[3])
            return None

    def generic_write(self, addr_h, addr_l, val_h, val_l):
        with self.lock:
            if not self.connect(): return False
            if not self.send_frame_raw(CMD_READ, addr_h, addr_l): return False
            time.sleep(0.05)
            self.read_response_raw() 
            if not self.send_frame_raw(CMD_WRITE, val_h, val_l): return False
            resp = self.read_response_raw()
            if resp and resp[1] == 0x06: return True
            return False

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
    data = request.json or {}
    try:
        reference_thickness = float(data.get("reference_thickness"))
    except (TypeError, ValueError):
        return jsonify({"error": "A valid reference thickness is required."}), 400
    if reference_thickness < 0:
        return jsonify({"error": "Reference thickness must be zero or greater."}), 400

    if CLOUD_MODE:
        reading = {k: v for k, v in last_ingest_reading.items() if v is not None}
        if not reading:
            return jsonify({"error": "Waiting for sensor readings. Ensure Pi client is running."}), 400
        captured_readings = {k: round(float(v), 3) for k, v in reading.items()}
        failures = []
        current_state = get_thickness_state()
        updated_state = default_thickness_state()
        updated_state["setup_ready"] = current_state.get("setup_ready", False)
        updated_state["captured_at"] = current_state.get("captured_at")
        updated_state["reference_readings"] = normalize_sensor_readings(current_state.get("reference_readings", {}))
        updated_state["calibration_completed"] = True
        updated_state["calibration_active"] = True
        updated_state["calibration_captured_at"] = datetime.datetime.now().isoformat()
        updated_state["calibration_reference_thickness"] = round(float(reference_thickness), 3)
        for sensor_id, reading_val in captured_readings.items():
            updated_state["calibration_baseline_readings"][sensor_id] = reading_val
        set_thickness_state(updated_state)
    else:
        if not active_sensors_map:
            return jsonify({"error": "No active sensors available."}), 400
        captured_readings, failures = capture_calibration(reference_thickness)
        if not captured_readings:
            return jsonify({"error": "Unable to capture calibration readings."}), 500

    # Save to user's calibration in DB if username provided
    username = data.get("username")
    if username:
        save_user_calibration_to_db(username, get_thickness_state())

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
    
    # Save to user's calibration in DB if username provided
    username = data.get("username")
    if username:
        save_user_calibration_to_db(username, get_thickness_state())
    
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
    
    # Save to user's calibration in DB if username provided
    username = data.get("username")
    if username:
        save_user_calibration_to_db(username, get_thickness_state())
    
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
    # Also delete user's calibration from DB if username provided
    data = request.json or {}
    username = data.get("username")
    if username:
        delete_user_calibration_from_db(username)
    return jsonify({
        "message": "Calibration reset successfully.",
        "calibration_active": updated_state.get("calibration_active", False),
        "calibration_reference_thickness": updated_state.get("calibration_reference_thickness", 0.0),
        "calibration_baseline_readings": updated_state.get("calibration_baseline_readings", {}),
        "calibration_captured_at": updated_state.get("calibration_captured_at"),
    }), 200

@app.route('/thickness/user-calibration', methods=['GET'])
def get_user_calibration():
    """Get saved calibration state for a specific user."""
    username = request.args.get("username")
    if not username:
        return jsonify({"error": "username query parameter is required"}), 400
    cal_data = load_user_calibration_from_db(username)
    if cal_data:
        return jsonify(cal_data), 200
    return jsonify({"calibration_found": False}), 200

@app.route('/thickness/load-user-calibration', methods=['POST'])
def load_user_calibration():
    """Load a user's saved calibration into the runtime thickness state."""
    data = request.json or {}
    username = data.get("username")
    if not username:
        return jsonify({"error": "username is required"}), 400
    cal_data = load_user_calibration_from_db(username)
    if cal_data is None:
        # No saved calibration - reset to defaults
        reset_calibration_state()
        return jsonify({
            "message": f"No saved calibration found for '{username}'.",
            "calibration_loaded": False,
            "calibration_active": False,
        }), 200
    # Merge saved calibration into current runtime state
    current = get_thickness_state()
    for key, value in cal_data.items():
        if key in current:
            current[key] = value
    set_thickness_state(current)
    return jsonify({
        "message": f"Calibration loaded for '{username}'.",
        "calibration_loaded": True,
        "calibration_active": current.get("calibration_active", False),
        "calibration_reference_thickness": current.get("calibration_reference_thickness", 0.0),
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
            # Check if user has saved calibration
            has_calibration = load_user_calibration_from_db(username) is not None
            return jsonify({
                "message": "Login successful", 
                "username": username,
                "role": user_record[1],
                "has_calibration": has_calibration,
            }), 200
        else:
            return jsonify({"error": "Invalid username or password"}), 401
    except Exception as e:
        print(f"Login DB Error: {e}")
        return jsonify({"error": "Internal server error connecting to database"}), 500

# ==========================================
# APIS - SENSOR HARDWARE CONFIG
# ==========================================
@app.route('/config/read', methods=['POST'])
def read_setting():
    data = request.json
    target = str(data.get("sensor", "A")).upper()
    if target not in active_sensors_map:
        return jsonify({"error": f"Sensor '{target}' is not online."}), 400
    sensor = active_sensors_map[target]
    try:
        addr_h = int(str(data.get("addr_h")), 16) if isinstance(data.get("addr_h"), str) else int(data.get("addr_h"))
        addr_l = int(str(data.get("addr_l")), 16) if isinstance(data.get("addr_l"), str) else int(data.get("addr_l"))
        result = sensor.generic_read(addr_h, addr_l)
        if result:
            return jsonify({
                "sensor": target, "message": "Read Success",
                "addr_h": hex(addr_h), "addr_l": hex(addr_l),
                "val_h": hex(result[0]), "val_l": hex(result[1])
            }), 200
        else:
            return jsonify({"error": f"{sensor.name} Read Failed"}), 500
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid format"}), 400

@app.route('/config/write', methods=['POST'])
def write_setting():
    data = request.json
    target = str(data.get("sensor", "A")).upper()

    if CLOUD_MODE:
        cmd_id = int(time.time() * 1000)
        with pending_config_lock:
            pending_config_commands.append({
                "id":     cmd_id,
                "sensor": target,
                "addr_h": data.get("addr_h"),
                "addr_l": data.get("addr_l"),
                "val_h":  data.get("val_h", "0x00"),
                "val_l":  data.get("val_l"),
            })
        return jsonify({"message": f"Write queued for Pi relay (id={cmd_id})", "status": "pending", "id": cmd_id}), 200

    if target not in active_sensors_map:
        return jsonify({"error": f"Sensor '{target}' is not online."}), 400
    sensor = active_sensors_map[target]
    try:
        addr_h = int(str(data.get("addr_h")), 16) if isinstance(data.get("addr_h"), str) else int(data.get("addr_h"))
        addr_l = int(str(data.get("addr_l")), 16) if isinstance(data.get("addr_l"), str) else int(data.get("addr_l"))
        val_h  = int(str(data.get("val_h")), 16)  if isinstance(data.get("val_h"), str)  else int(data.get("val_h"))
        val_l  = int(str(data.get("val_l")), 16)  if isinstance(data.get("val_l"), str)  else int(data.get("val_l"))
        success = sensor.generic_write(addr_h, addr_l, val_h, val_l)
        if success:
            return jsonify({
                "sensor": target, "message": "Write Success", 
                "addr": f"{hex(addr_h)} {hex(addr_l)}", "val": f"{hex(val_h)} {hex(val_l)}"
            }), 200
        else:
            return jsonify({"error": f"{sensor.name} Write Failed"}), 500
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid format"}), 400

@app.route('/stream/config', methods=['POST'])
def config_stream():
    data = request.json
    try:
        rate = float(data.get("rate", 5.0))
        if not (0.1 <= rate <= 10.0):
             return jsonify({"error": "Invalid rate."}), 400
        stream_state["target_rate_hz"] = rate
        return jsonify({"message": f"Stream rate updated to {rate} Hz"}), 200
    except ValueError:
        return jsonify({"error": "Invalid rate"}), 400

@app.route('/stream/trim', methods=['POST'])
def config_trim():
    """Trim percentage (opposite mode)."""
    data = request.json
    try:
        trim_pct = int(data.get("trim_pct", 10))
        if not (0 <= trim_pct <= 20):
            return jsonify({"error": "Trim percentage must be 0-20."}), 400
        try:
            with open(CONFIG_FILE_PATH, 'r') as f:
                cfg = json.load(f)
            if "global_settings" not in cfg:
                cfg["global_settings"] = {}
            cfg["global_settings"]["trim_percentage"] = trim_pct
            with open(CONFIG_FILE_PATH, 'w') as f:
                json.dump(cfg, f, indent=4)
        except Exception as e:
            print(f"Could not persist trim_percentage to config file: {e}")
        return jsonify({"message": f"Trim percentage set to {trim_pct}%", "trim_pct": trim_pct}), 200
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid trim_pct value"}), 400
    
# ==========================================
# APIS - EXTERNAL INGEST (Ubuntu/Pi POST)
# ==========================================
@app.route('/ingest/readings', methods=['POST'])
def ingest_readings():
    if INGEST_API_KEY and request.headers.get("X-Api-Key") != INGEST_API_KEY:
        return jsonify({"error": "unauthorized"}), 401

    payload = request.json or {}
    a = payload.get('sensor_A')
    b = payload.get('sensor_B')
    c = payload.get('sensor_C')
    ts = payload.get('timestamp') or datetime.datetime.now().isoformat()

    global last_ingest_reading
    if a is not None: last_ingest_reading["A"] = float(a)
    if b is not None: last_ingest_reading["B"] = float(b)
    if c is not None: last_ingest_reading["C"] = float(c)

    dist_A = float(a) if a is not None else last_ingest_reading.get("A")
    dist_B = float(b) if b is not None else last_ingest_reading.get("B")
    dist_C = float(c) if c is not None else last_ingest_reading.get("C")

    thickness_val = calculate_opposite_thickness(dist_A, dist_B)
    sbs_a = calculate_thickness_sbs("A", dist_A) if dist_A is not None else None
    sbs_b = calculate_thickness_sbs("B", dist_B) if dist_B is not None else None
    sbs_c = calculate_thickness_sbs("C", dist_C) if dist_C is not None else None

    global _last_ingest_emit_time
    _now = time.time()
    _due = False
    with _ingest_emit_lock:
        if (_now - _last_ingest_emit_time) >= (1.0 / stream_state["target_rate_hz"]):
            _last_ingest_emit_time = _now
            _due = True

    if _due:
        try:
            socketio.emit('sensor_reading', {
                "timestamp": ts,
                "sensor_A": sbs_a,
                "sensor_B": sbs_b,
                "sensor_C": sbs_c,
                "distance_A": dist_A,
                "distance_B": dist_B,
                "distance_C": dist_C,
                "thickness": thickness_val,
            })
        except Exception as e:
            print(f"Socket emit error: {e}")

    return jsonify({"status": "ok"}), 200

@app.route('/config/poll', methods=['GET'])
def poll_config():
    with pending_config_lock:
        cmds = list(pending_config_commands)
        pending_config_commands.clear()
    if cmds:
        return jsonify({"commands": cmds, "interval_s": 0.5, "ingest_api_key": INGEST_API_KEY}), 200
    return jsonify({"commands": [], "interval_s": 2.0}), 200

@app.route('/config/result', methods=['POST'])
def post_config_result():
    data = request.json
    if data:
        with pending_config_lock:
            config_results.append({
                "id": data.get("id"),
                "sensor": data.get("sensor"),
                "status": data.get("status"),
                "error": data.get("error"),
            })
    return jsonify({"status": "ok"}), 200

# ==========================================
# DATABASE STREAM WRITER
# ==========================================
def stream_to_database():
    """Background thread: batch-writes sensor readings to PostgreSQL."""
    BATCH_SIZE = 50
    FLUSH_INTERVAL = 2.0  # seconds
    batch_filtered = []
    batch_unfiltered = []
    batch_thickness = []
    batch_thickness_raw = []
    last_flush = time.time()

    while True:
        time.sleep(0.1)
        now = time.time()
        if (len(batch_filtered) < BATCH_SIZE and
            len(batch_unfiltered) < BATCH_SIZE and
            len(batch_thickness) < BATCH_SIZE and
            len(batch_thickness_raw) < BATCH_SIZE and
            now - last_flush < FLUSH_INTERVAL):
            continue

        if batch_filtered:
            try:
                conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
                cur = conn.cursor()
                cur.execute(f"SELECT MAX(id) FROM {DB_TABLE_FILTERED}")
                max_id = cur.fetchone()[0]
                if max_id is None: max_id = 0
                start_id = max_id + 1
                rows_to_insert = []
                for i, row in enumerate(batch_filtered):
                    rows_to_insert.append((
                        start_id + i,
                        row['ts'],
                        row.get('a'),
                        row.get('b'),
                        row.get('c')
                    ))
                if rows_to_insert:
                    extras.execute_values(
                        cur,
                        f"INSERT INTO {DB_TABLE_FILTERED} (id, timestamp, sensor_a, sensor_b, sensor_c) VALUES %s",
                        rows_to_insert
                    )
                    print(f"DB: wrote {len(rows_to_insert)} filtered rows")
                conn.commit()
                cur.close()
                conn.close()
            except Exception as e:
                print(f"DB Write Error (filtered): {e}")
            batch_filtered = []

        if batch_unfiltered:
            try:
                conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
                cur = conn.cursor()
                cur.execute(f"SELECT MAX(id) FROM {DB_TABLE_UNFILTERED}")
                max_id = cur.fetchone()[0]
                if max_id is None: max_id = 0
                start_id = max_id + 1
                rows_to_insert = []
                for i, row in enumerate(batch_unfiltered):
                    rows_to_insert.append((
                        start_id + i,
                        row['ts'],
                        row.get('a'),
                        row.get('b'),
                        row.get('c')
                    ))
                if rows_to_insert:
                    extras.execute_values(
                        cur,
                        f"INSERT INTO {DB_TABLE_UNFILTERED} (id, timestamp, sensor_a, sensor_b, sensor_c) VALUES %s",
                        rows_to_insert
                    )
                    print(f"DB: wrote {len(rows_to_insert)} unfiltered rows")
                conn.commit()
                cur.close()
                conn.close()
            except Exception as e:
                print(f"DB Write Error (unfiltered): {e}")
            batch_unfiltered = []

        if batch_thickness:
            try:
                conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
                cur = conn.cursor()
                cur.execute(f"SELECT MAX(id) FROM {DB_TABLE_THICKNESS}")
                max_id = cur.fetchone()[0]
                if max_id is None: max_id = 0
                start_id = max_id + 1
                rows_to_insert = []
                for i, row in enumerate(batch_thickness):
                    rows_to_insert.append((
                        start_id + i,
                        row['ts'],
                        row.get('a'),
                        row.get('b'),
                        row.get('thickness')
                    ))
                if rows_to_insert:
                    extras.execute_values(
                        cur,
                        f"INSERT INTO {DB_TABLE_THICKNESS} (id, timestamp, sensor_a, sensor_b, thickness) VALUES %s",
                        rows_to_insert
                    )
                    print(f"DB: wrote {len(rows_to_insert)} thickness rows")
                conn.commit()
                cur.close()
                conn.close()
            except Exception as e:
                print(f"DB Write Error (thickness): {e}")
            batch_thickness = []

        if batch_thickness_raw:
            try:
                conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
                cur = conn.cursor()
                cur.execute(f"SELECT MAX(id) FROM {DB_TABLE_THICKNESS_RAW}")
                max_id = cur.fetchone()[0]
                if max_id is None: max_id = 0
                start_id = max_id + 1
                rows_to_insert = []
                for i, row in enumerate(batch_thickness_raw):
                    rows_to_insert.append((
                        start_id + i,
                        row['ts'],
                        row.get('a'),
                        row.get('b'),
                        row.get('thickness')
                    ))
                if rows_to_insert:
                    extras.execute_values(
                        cur,
                        f"INSERT INTO {DB_TABLE_THICKNESS_RAW} (id, timestamp, sensor_a, sensor_b, thickness) VALUES %s",
                        rows_to_insert
                    )
                    print(f"DB: wrote {len(rows_to_insert)} thickness_raw rows")
                conn.commit()
                cur.close()
                conn.close()
            except Exception as e:
                print(f"DB Write Error (thickness_raw): {e}")
            batch_thickness_raw = []

        last_flush = now

# ==========================================
# MAIN
# ==========================================
if __name__ == "__main__":
    print("--- Initializing thickness state... ---")
    init_thickness_state_file()
    
    print("--- Initializing databases... ---")
    init_db()
    
    if not CLOUD_MODE:
        print("--- Refreshing sensor configs... ---")
        online_sensors = refresh_sensor_configs()
        print(f"--- Online sensors: {online_sensors} ---")
        
        print("--- Starting database stream writer thread... ---")
        db_thread = threading.Thread(target=stream_to_database, daemon=True)
        db_thread.start()
    else:
        print("--- Running in CLOUD_MODE ---")
    
    print(f"--- Starting Flask + SocketIO on 0.0.0.0:{SERVER_PORT} ---")
    socketio.run(app, host=SERVER_IP, port=SERVER_PORT, debug=False, allow_unsafe_werkzeug=True)