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
from flask_cors import CORS
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

CORS(app)
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
            return jsonify({
                "message": "Login successful", 
                "username": username,
                "role": user_record[1] 
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
                "distance_A": round(dist_A, 3) if dist_A is not None else None,
                "distance_B": round(dist_B, 3) if dist_B is not None else None,
                "distance_C": round(dist_C, 3) if dist_C is not None else None,
                "sensor_A":   round(sbs_a, 3)  if sbs_a  is not None else None,
                "sensor_B":   round(sbs_b, 3)  if sbs_b  is not None else None,
                "sensor_C":   round(sbs_c, 3)  if sbs_c  is not None else None,
                "thickness":  round(thickness_val, 3) if thickness_val is not None else None,
            })
        except Exception:
            pass

    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
        cur = conn.cursor()

        raw_id = get_next_db_id(cur, DB_TABLE_UNFILTERED, LIMIT_UNFILTERED)
        cur.execute(f"""
            INSERT INTO {DB_TABLE_UNFILTERED} (id, timestamp, sensor_a, sensor_b, sensor_c)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                timestamp = EXCLUDED.timestamp,
                sensor_a = EXCLUDED.sensor_a,
                sensor_b = EXCLUDED.sensor_b,
                sensor_c = EXCLUDED.sensor_c
        """, (raw_id, ts, dist_A, dist_B, dist_C))

        fil_id = get_next_db_id(cur, DB_TABLE_FILTERED, LIMIT_FILTERED)
        cur.execute(f"""
            INSERT INTO {DB_TABLE_FILTERED} (id, timestamp, sensor_a, sensor_b, sensor_c)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                timestamp = EXCLUDED.timestamp,
                sensor_a = EXCLUDED.sensor_a,
                sensor_b = EXCLUDED.sensor_b,
                sensor_c = EXCLUDED.sensor_c
        """, (fil_id, ts, sbs_a, sbs_b, sbs_c))

        thick_raw_id = get_next_db_id(cur, DB_TABLE_THICKNESS_RAW, LIMIT_THICKNESS_RAW)
        cur.execute(f"""
            INSERT INTO {DB_TABLE_THICKNESS_RAW} (id, timestamp, sensor_a, sensor_b, thickness)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                timestamp = EXCLUDED.timestamp,
                sensor_a = EXCLUDED.sensor_a,
                sensor_b = EXCLUDED.sensor_b,
                thickness = EXCLUDED.thickness
        """, (thick_raw_id, ts, dist_A, dist_B, thickness_val))

        thick_id = get_next_db_id(cur, DB_TABLE_THICKNESS, LIMIT_THICKNESS)
        cur.execute(f"""
            INSERT INTO {DB_TABLE_THICKNESS} (id, timestamp, sensor_a, sensor_b, thickness)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                timestamp = EXCLUDED.timestamp,
                sensor_a = EXCLUDED.sensor_a,
                sensor_b = EXCLUDED.sensor_b,
                thickness = EXCLUDED.thickness
        """, (thick_id, ts, dist_A, dist_B, thickness_val))

        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"message": "ingest_ok", "id": raw_id}), 200
    except Exception as e:
        try:
            fallback_path = os.path.join(BASE_DIR, 'ingest_fallback.jsonl')
            with open(fallback_path, 'a') as fh:
                fh.write(json.dumps({"timestamp": ts, "sensor_A": a, "sensor_B": b, "sensor_C": c}) + "\n")
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500

@app.route('/config/poll', methods=['GET'])
def config_poll():
    if INGEST_API_KEY and request.headers.get("X-Api-Key") != INGEST_API_KEY:
        return jsonify({"error": "unauthorized"}), 401
    with pending_config_lock:
        cmds = list(pending_config_commands)
        pending_config_commands.clear()
    return jsonify({"commands": cmds}), 200

@app.route('/config/result', methods=['POST'])
def config_result():
    if INGEST_API_KEY and request.headers.get("X-Api-Key") != INGEST_API_KEY:
        return jsonify({"error": "unauthorized"}), 401
    payload = request.json or {}
    with pending_config_lock:
        config_results.append(payload)
    print(f"[CONFIG RESULT] sensor={payload.get('sensor')} id={payload.get('id')} success={payload.get('success')}")
    return jsonify({"message": "result received"}), 200

# ==========================================
# API - SENSOR STATUS
# ==========================================
@app.route('/sensors/status', methods=['GET'])
def sensors_status():
    if CLOUD_MODE:
        active_ids = [sid for sid, val in last_ingest_reading.items() if val is not None]
    else:
        with sensors_lock:
            active_ids = list(active_sensors_map.keys())
    status = {}
    all_sids = list(SENSOR_CONFIGS.keys()) if SENSOR_CONFIGS else list(last_ingest_reading.keys())
    for sid in all_sids:
        config = SENSOR_CONFIGS.get(sid, {})
        status[sid] = {
            "ip":     config.get("ip", ""),
            "port":   config.get("port", 8234),
            "name":   config.get("name", f"Sensor {sid}"),
            "online": sid in active_ids,
        }
    return jsonify(status), 200

# ==========================================
# API - DOWNLOAD ROUTES
# ==========================================
@app.route('/download/filtered', methods=['POST'])
def download_filtered():
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
        cur  = conn.cursor()
        cur.execute(f"""
            SELECT id, timestamp, sensor_a, sensor_b, sensor_c
            FROM {DB_TABLE_FILTERED}
            ORDER BY timestamp ASC
        """)
        rows = cur.fetchall()
        rows = sorted(rows, key=lambda x: x[1])
        cur.close()
        conn.close()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "timestamp", "sensor_a_thickness", "sensor_b_thickness", "sensor_c_thickness"])
        writer.writerows(rows)
        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=filtered_thickness_data.csv"}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/download/raw', methods=['POST'])
def download_raw():
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
        cur  = conn.cursor()
        cur.execute(f"""
            SELECT id, timestamp, sensor_a, sensor_b, sensor_c
            FROM {DB_TABLE_UNFILTERED}
            ORDER BY timestamp ASC
        """)
        rows = cur.fetchall()
        rows = sorted(rows, key=lambda x: x[1])
        cur.close()
        conn.close()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "timestamp", "sensor_a_thickness", "sensor_b_thickness", "sensor_c_thickness"])
        writer.writerows(rows)
        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=unfiltered_thickness_data.csv"}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/download/thickness/raw', methods=['GET'])
def download_thickness_raw():
    """Export opposite_thickness_raw_readings table as CSV."""
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
        cur  = conn.cursor()
        cur.execute(f"""
            SELECT id, timestamp, sensor_a, sensor_b, thickness
            FROM {DB_TABLE_THICKNESS_RAW}
            ORDER BY timestamp ASC
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["#", "Timestamp", "Sensor A (mm)", "Sensor B (mm)", "Thickness (mm)"])
        writer.writerows(rows)
        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=thickness_raw_data.csv"}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/download/thickness', methods=['GET'])
def download_thickness():
    """Export opposite_thickness_readings table as CSV."""
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
        cur  = conn.cursor()
        cur.execute(f"""
            SELECT id, timestamp, sensor_a, sensor_b, thickness
            FROM {DB_TABLE_THICKNESS}
            ORDER BY timestamp ASC
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["#", "Timestamp", "Sensor A (mm)", "Sensor B (mm)", "Thickness (mm)"])
        writer.writerows(rows)
        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=thickness_data.csv"}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/db/status', methods=['GET'])
def db_status():
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
        cur  = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {DB_TABLE_FILTERED}")
        filtered_count = cur.fetchone()[0]
        cur.execute(f"SELECT COUNT(*) FROM {DB_TABLE_UNFILTERED}")
        unfiltered_count = cur.fetchone()[0]
        cur.execute(f"SELECT COUNT(*) FROM {DB_TABLE_THICKNESS}")
        thickness_count = cur.fetchone()[0]
        cur.execute(f"SELECT COUNT(*) FROM {DB_TABLE_THICKNESS_RAW}")
        thickness_raw_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM users")
        users_count = cur.fetchone()[0]
        cur.close()
        conn.close()
        return jsonify({
            "filtered":   filtered_count,
            "unfiltered": unfiltered_count,
            "thickness":  thickness_count,
            "thickness_raw": thickness_raw_count,
            "users":      users_count,
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==========================================
# API - SERVER CONFIGURATION
# ==========================================
@app.route('/server/config', methods=['GET'])
def server_config():
    return jsonify({
        "sensor_configs":     SENSOR_CONFIGS,
        "server_port":        SERVER_PORT,
        "sensor_timeout":     SENSOR_TIMEOUT,
        "limit_filtered":     LIMIT_FILTERED,
        "limit_unfiltered":   LIMIT_UNFILTERED,
        "limit_thickness":    LIMIT_THICKNESS,
        "limit_thickness_raw": LIMIT_THICKNESS_RAW,
        "db_host":            DB_HOST,
        "db_name":            DB_NAME,
        "thickness_state":    get_thickness_state(),
    }), 200

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_frontend(path):
    """Serve the built React frontend from dist/ directory."""
    from flask import send_from_directory
    dist_dir = os.path.join(BASE_DIR, '..', 'dist')
    if path.startswith('socket.io'):
        from flask import abort
        abort(404)
    if path and os.path.exists(os.path.join(dist_dir, path)):
        return send_from_directory(dist_dir, path)
    return send_from_directory(dist_dir, 'index.html')

@app.route('/server/network', methods=['GET', 'POST'])
def server_network():
    if request.method == 'GET':
        return jsonify(SENSOR_CONFIGS), 200
    payload = request.json or {}
    updated, errors = normalize_network_config(payload, SENSOR_CONFIGS)
    if errors:
        return jsonify({"error": "Invalid network config", "details": errors}), 400
    active_ids = refresh_sensor_configs(updated)
    return jsonify({
        "message": "Network config updated",
        "active_sensors": active_ids,
        "sensor_configs": SENSOR_CONFIGS,
    }), 200

# ==========================================
# WEBSOCKET & DB STREAMING
# ==========================================
def calculate_filtered_average(data_batch, trim_pct=10):
    if not data_batch: return None
    n = len(data_batch)
    if n < 3: return sum(data_batch) / n
    sorted_data = sorted(data_batch)
    trim_count = max(1, int(n * (trim_pct / 100))) if n > 2 and trim_pct > 0 else 0
    filtered_data = sorted_data[trim_count:-trim_count] if trim_count > 0 else sorted_data
    return sum(filtered_data) / len(filtered_data) if filtered_data else 0.0

def background_stream_task():
    print(">>> Stream Task Started (with PostgreSQL Logging)")

    if CLOUD_MODE:
        print(">>> CLOUD_MODE: sensor data arrives via /ingest/readings — stream task idling.")
        while True:
            socketio.sleep(1)
        return

    db_conn = None
    try:
        db_conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
        db_cur = db_conn.cursor()
        unf_id = get_next_db_id(db_cur, DB_TABLE_UNFILTERED, LIMIT_UNFILTERED)
        fil_id = get_next_db_id(db_cur, DB_TABLE_FILTERED, LIMIT_FILTERED)
        thick_id = get_next_db_id(db_cur, DB_TABLE_THICKNESS, LIMIT_THICKNESS)
        thick_raw_id = get_next_db_id(db_cur, DB_TABLE_THICKNESS_RAW, LIMIT_THICKNESS_RAW)
    except Exception as e:
        print(f"!!! DB Connection Failed in Stream Task: {e}")
        return

    sbs_insert = """
        INSERT INTO {table} (id, timestamp, sensor_a, sensor_b, sensor_c)
        VALUES %s
        ON CONFLICT (id) DO UPDATE SET 
            timestamp = EXCLUDED.timestamp,
            sensor_a = EXCLUDED.sensor_a,
            sensor_b = EXCLUDED.sensor_b,
            sensor_c = EXCLUDED.sensor_c
    """
    opp_insert = """
        INSERT INTO {table} (id, timestamp, sensor_a, sensor_b, thickness)
        VALUES %s
        ON CONFLICT (id) DO UPDATE SET 
            timestamp = EXCLUDED.timestamp,
            sensor_a = EXCLUDED.sensor_a,
            sensor_b = EXCLUDED.sensor_b,
            thickness = EXCLUDED.thickness
    """
    
    unf_query = sbs_insert.format(table=DB_TABLE_UNFILTERED)
    fil_query = sbs_insert.format(table=DB_TABLE_FILTERED)
    thick_query = opp_insert.format(table=DB_TABLE_THICKNESS)
    thick_raw_query = opp_insert.format(table=DB_TABLE_THICKNESS_RAW)

    def get_trim_pct():
        try:
            with open(CONFIG_FILE_PATH, 'r') as f:
                cfg = json.load(f)
            return cfg.get("global_settings", {}).get("trim_percentage", 10)
        except:
            return 10

    batches = {sid: [] for sid in active_sensors_map.keys()}
    raw_db_buffer = []
    thick_raw_db_buffer = []
    last_emit_time = time.time()

    while True:
        if not stream_state["active"]:
            time.sleep(1)
            continue

        with sensors_lock:
            sensors_snapshot = dict(active_sensors_map)

        if set(sensors_snapshot.keys()) != set(batches.keys()):
            batches = {sid: batches.get(sid, []) for sid in sensors_snapshot.keys()}

        all_sensors_failed = True
        raw_snapshot = {}

        for sid, sensor in sensors_snapshot.items():
            val = sensor.get_single_measurement()
            if val is not None:
                # Store raw distance for batches (used for opposite mode distance_X and SBS thickness)
                batches[sid].append(val)
                raw_snapshot[sid] = val
                all_sensors_failed = False
        
        if all_sensors_failed:
            time.sleep(0.01)
        else:
            mode = get_current_mode()
            raw_ts = datetime.datetime.now()
            
            # Calculate thickness values for both modes
            sbs_a = calculate_thickness_sbs("A", raw_snapshot.get("A")) if "A" in raw_snapshot else None
            sbs_b = calculate_thickness_sbs("B", raw_snapshot.get("B")) if "B" in raw_snapshot else None
            sbs_c = calculate_thickness_sbs("C", raw_snapshot.get("C")) if "C" in raw_snapshot else None
            
            # For opposite mode: calculate combined thickness
            opp_ab = raw_snapshot.get("A"), raw_snapshot.get("B")
            opp_thickness = calculate_opposite_thickness(opp_ab[0], opp_ab[1])
            
            raw_db_buffer.append((
                unf_id, 
                raw_ts, 
                raw_snapshot.get("A"), 
                raw_snapshot.get("B"), 
                raw_snapshot.get("C")
            ))
            unf_id = (unf_id % LIMIT_UNFILTERED) + 1
            
            thick_raw_db_buffer.append((
                thick_raw_id,
                raw_ts,
                raw_snapshot.get("A"),
                raw_snapshot.get("B"),
                opp_thickness
            ))
            thick_raw_id = (thick_raw_id % LIMIT_THICKNESS_RAW) + 1

        current_time = time.time()
        if current_time - last_emit_time >= (1.0 / stream_state["target_rate_hz"]):
            # Build payload with ALL fields that both modes need
            payload = {"timestamp": datetime.datetime.now().isoformat()}
            has_data = False
            
            trim_pct = get_trim_pct()
            for sid in sensors_snapshot.keys():
                if batches[sid]:
                    # Raw distance (for opposite mode)
                    distance_val = round(calculate_filtered_average(batches[sid], trim_pct), 3)
                    payload[f"distance_{sid}"] = distance_val
                    # Thickness (for SBS mode) - calculated from raw measurement
                    thickness_val = calculate_thickness_sbs(sid, distance_val)
                    payload[f"sensor_{sid}"] = round(thickness_val, 3) if thickness_val is not None else distance_val
                    batches[sid] = []
                    has_data = True
                else:
                    payload[f"sensor_{sid}"] = None
                    payload[f"distance_{sid}"] = None

            if has_data:
                # Calculate thickness for opposite mode
                dist_a = payload.get("distance_A")
                dist_b = payload.get("distance_B")
                payload["thickness"] = calculate_opposite_thickness(dist_a, dist_b)

                fil_ts = datetime.datetime.now()
                
                # Write to SBS tables
                fil_tuple_sbs = [(
                    fil_id,
                    fil_ts,
                    payload.get("sensor_A"),
                    payload.get("sensor_B"),
                    payload.get("sensor_C")
                )]
                
                # Write to opposite tables
                fil_tuple_opp = [(
                    fil_id,
                    fil_ts,
                    dist_a,
                    dist_b,
                    payload.get("thickness")
                )]
                
                try:
                    extras.execute_values(db_cur, fil_query, fil_tuple_sbs)
                    fil_id = (fil_id % LIMIT_FILTERED) + 1
                    
                    extras.execute_values(db_cur, thick_query, fil_tuple_opp)
                    thick_id = (thick_id % LIMIT_THICKNESS) + 1
                    
                    if raw_db_buffer:
                        extras.execute_values(db_cur, unf_query, raw_db_buffer)
                        raw_db_buffer = []
                    
                    if thick_raw_db_buffer:
                        extras.execute_values(db_cur, thick_raw_query, thick_raw_db_buffer)
                        thick_raw_db_buffer = []
                        
                    db_conn.commit()
                except Exception as e:
                    print(f"DB Write Error: {e}")
                    db_conn.rollback()

                socketio.emit('sensor_reading', payload)

            last_emit_time = current_time
        
        socketio.sleep(0.001)

# --- CONNECTION HANDLING ---
@socketio.on('connect')
def handle_connect():
    stream_state["connected_clients"] += 1
    print(f">>> Client Connected: {request.sid} | Total Clients: {stream_state['connected_clients']}")
    stream_state["active"] = True
    
    if stream_state["thread"] is None:
        stream_state["thread"] = socketio.start_background_task(background_stream_task)

@socketio.on('disconnect')
def handle_disconnect():
    stream_state["connected_clients"] = max(0, stream_state["connected_clients"] - 1)
    print(f"<<< Client Disconnected: {request.sid} | Total Clients: {stream_state['connected_clients']}")
    if stream_state["connected_clients"] == 0:
        print("--- No active clients. Pausing sensor stream. ---")
        stream_state["active"] = False

if __name__ == '__main__':
    print("==================================================")
    print("        STARTING MERGED API SERVER")
    print("        Supports both Side-by-Side & Opposite modes")
    print("==================================================")
    
    init_db()
    init_config_file()
    init_thickness_state_file()
    init_network_config_file()

    if CLOUD_MODE:
        print("CLOUD_MODE=true — skipping local sensor detection. Pi will POST data via /ingest/readings.")
    else:
        print("Detecting hardware configuration...")
        refresh_sensor_configs()
        mode = get_current_mode()
        print(f"Detected mode: {mode.upper()}")
        print(f"Active Sensors: {list(active_sensors_map.keys())}")

    print(f"CLOUD_MODE: {CLOUD_MODE}")
    print(f"Listening on {SERVER_IP}:{SERVER_PORT}")
    print("==================================================")

    try:
        socketio.run(app, host=SERVER_IP, port=SERVER_PORT, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)
    except KeyboardInterrupt:
        for sensor in active_sensors_map.values():
            sensor.disconnect()