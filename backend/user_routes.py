"""
User Routes
Provides user CRUD operations and database status.
"""
import psycopg2
from werkzeug.security import generate_password_hash
from flask import Blueprint, request, jsonify

DB_HOST = "localhost"
DB_NAME = "sensor_db"
DB_USER = "rapl"
DB_PASS = "rapl2026"
DB_TABLE_USERS = "users"

users_bp = Blueprint("users", __name__)

@users_bp.route("/users", methods=["GET"])
def get_users():
    """Get all users."""
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
        cur = conn.cursor()
        cur.execute(f"SELECT id, username, role FROM {DB_TABLE_USERS} ORDER BY id")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify([{"id": r[0], "username": r[1], "role": r[2]} for r in rows]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@users_bp.route("/users", methods=["POST"])
def add_user():
    """Add a new user."""
    data = request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    role = data.get("role", "worker").strip()
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400
    try:
        pw_hash = generate_password_hash(password)
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO {DB_TABLE_USERS} (username, password_hash, role) VALUES (%s, %s, %s)",
            (username, pw_hash, role)
        )
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"message": f"User '{username}' created"}), 201
    except psycopg2.errors.UniqueViolation:
        return jsonify({"error": "Username already exists"}), 409
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@users_bp.route("/users/<int:user_id>", methods=["DELETE"])
def delete_user(user_id):
    """Delete a user by ID."""
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
        cur = conn.cursor()
        cur.execute(f"DELETE FROM {DB_TABLE_USERS} WHERE id = %s", (user_id,))
        deleted = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        if deleted:
            return jsonify({"message": "User deleted"}), 200
        return jsonify({"error": "User not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@users_bp.route("/db/status", methods=["GET"])
def db_status():
    """Get row counts for all tables."""
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM sensor_filtered_readings")
        filtered = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM sensor_unfiltered_readings")
        unfiltered = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM opposite_thickness_readings")
        thickness = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM opposite_thickness_raw_readings")
        thickness_raw = cur.fetchone()[0]

        cur.execute(f"SELECT COUNT(*) FROM {DB_TABLE_USERS}")
        users = cur.fetchone()[0]

        cur.close()
        conn.close()

        return jsonify({
            "filtered": filtered,
            "unfiltered": unfiltered,
            "thickness": thickness,
            "thickness_raw": thickness_raw,
            "users": users,
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def register_user_routes(app):
    """Register the user blueprint on the app."""
    app.register_blueprint(users_bp)