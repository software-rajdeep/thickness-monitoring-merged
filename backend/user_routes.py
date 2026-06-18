# ==========================================
# USER MANAGEMENT ROUTES
# ==========================================
import psycopg2
from werkzeug.security import generate_password_hash

DB_HOST = "localhost"
DB_NAME = "sensor_db"
DB_USER = "rapl"
DB_PASS = "rapl2026"


def register_user_routes(app):

    @app.route('/users', methods=['GET'])
    def get_users():
        from flask import jsonify
        try:
            conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
            cur  = conn.cursor()
            cur.execute("SELECT id, username, role FROM users ORDER BY id ASC")
            rows = cur.fetchall()
            cur.close()
            conn.close()
            return jsonify([{"id": r[0], "username": r[1], "role": r[2]} for r in rows]), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route('/users', methods=['POST'])
    def add_user():
        from flask import request, jsonify
        data     = request.json
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        role     = data.get('role', '').strip()
        if not username or not password or not role:
            return jsonify({"error": "Username, password and role required"}), 400
        if role not in ['superadmin', 'admin', 'supervisor', 'worker']:
            return jsonify({"error": "Invalid role"}), 400
        try:
            conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
            cur  = conn.cursor()
            hashed = generate_password_hash(password)
            cur.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s) RETURNING id",
                (username, hashed, role)
            )
            new_id = cur.fetchone()[0]
            conn.commit()
            cur.close()
            conn.close()
            return jsonify({"id": new_id, "username": username, "role": role}), 201
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route('/users/<int:user_id>', methods=['DELETE'])
    def delete_user(user_id):
        from flask import jsonify
        try:
            conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
            cur  = conn.cursor()
            cur.execute("SELECT username FROM users WHERE id = %s", (user_id,))
            user = cur.fetchone()
            if not user:
                return jsonify({"error": "User not found"}), 404
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
            conn.commit()
            cur.close()
            conn.close()
            return jsonify({"message": "User deleted successfully"}), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500