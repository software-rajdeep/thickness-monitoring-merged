# ==========================================
# DOWNLOAD & FRONTEND ROUTES
# ==========================================
import os
import csv
import io
import psycopg2

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
FRONTEND_DIST_DIR = os.path.join(PROJECT_ROOT, "dist")

DB_HOST = "localhost"
DB_NAME = "sensor_db"
DB_USER = "rapl"
DB_PASS = "rapl2026"


def register_download_routes(app, DB_TABLE_FILTERED, DB_TABLE_UNFILTERED, DB_TABLE_THICKNESS=None, DB_TABLE_THICKNESS_RAW=None):
    from flask import request, jsonify, Response, send_from_directory

    # ============================================================
    # Side-by-Side mode downloads
    # ============================================================

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

    # ============================================================
    # Opposite-Side mode downloads (thickness data)
    # ============================================================

    @app.route('/download/thickness', methods=['GET'])
    def download_thickness():
        if DB_TABLE_THICKNESS is None:
            return jsonify({"error": "Thickness table not configured"}), 400
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
            writer.writerow(["id", "timestamp", "sensor_a", "sensor_b", "thickness"])
            writer.writerows(rows)
            output.seek(0)
            return Response(
                output.getvalue(),
                mimetype="text/csv",
                headers={"Content-Disposition": "attachment; filename=opposite_thickness_data.csv"}
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route('/download/thickness/raw', methods=['GET'])
    def download_thickness_raw():
        if DB_TABLE_THICKNESS_RAW is None:
            return jsonify({"error": "Thickness raw table not configured"}), 400
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
            writer.writerow(["id", "timestamp", "sensor_a", "sensor_b", "thickness"])
            writer.writerows(rows)
            output.seek(0)
            return Response(
                output.getvalue(),
                mimetype="text/csv",
                headers={"Content-Disposition": "attachment; filename=opposite_thickness_raw_data.csv"}
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

            # Opposite-side table counts (if table names provided)
            thickness_count = None
            thickness_raw_count = None
            if DB_TABLE_THICKNESS:
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {DB_TABLE_THICKNESS}")
                    thickness_count = cur.fetchone()[0]
                except:
                    thickness_count = 0
            if DB_TABLE_THICKNESS_RAW:
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {DB_TABLE_THICKNESS_RAW}")
                    thickness_raw_count = cur.fetchone()[0]
                except:
                    thickness_raw_count = 0

            cur.execute("SELECT COUNT(*) FROM users")
            users_count = cur.fetchone()[0]
            cur.close()
            conn.close()
            return jsonify({
                "filtered":        filtered_count,
                "unfiltered":      unfiltered_count,
                "thickness":       thickness_count,
                "thickness_raw":   thickness_raw_count,
                "users":           users_count,
            }), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500
