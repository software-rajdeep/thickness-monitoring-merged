"""
Email Alert Routes
Provides Gmail API integration (OAuth) and SMTP fallback for alert notifications.
Only accessible by Super Admin.
"""
import json
import os
import threading
import time
import datetime
import smtplib
import ssl
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Blueprint, request, jsonify

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EMAIL_CONFIG_PATH = os.path.join(BASE_DIR, "email_alert_config.json")
TOKEN_PATH = os.path.join(BASE_DIR, "gmail_token.json")

# Allow overriding the OAuth redirect URI via environment variable (for ngrok HTTPS)
OAUTH_REDIRECT_URI = os.environ.get(
    "OAUTH_REDIRECT_URI",
    "http://localhost:5000/email-alerts/oauth-callback"
)

# ── Google API imports (lazy-loaded so the module works without them) ──
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request as GoogleRequest
    from googleapiclient.discovery import build
    GOOGLE_API_AVAILABLE = True
except ImportError:
    GOOGLE_API_AVAILABLE = False

# If modifying these scopes, delete the file gmail_token.json.
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

# ── Default Configuration ──────────────────────────────────────────────
DEFAULT_CONFIG = {
    "enabled": False,
    "api_type": "gmail_oauth",  # "gmail_oauth", "gmail_smtp", "outlook", "custom_smtp"
    "smtp_config": {
        "host": "smtp.gmail.com",
        "port": 587,
        "use_tls": True,
        "email": "",
        "password": "",
    },
    "recipient_email": "",
    "alerts": {
        "threshold_below_min": True,
        "threshold_above_max": True,
        "threshold_out_of_tolerance": True,
        "sensor_disconnected": True,
        "run_session_start": True,
        "run_session_end": True,
    },
    "cooldown": {
        "enabled": True,
        "minutes": 5,
    },
    "summary_report": {
        "enabled": False,
        "frequency": "daily",
    },
}

# ── In-memory state ────────────────────────────────────────────────────
_email_config = None
_last_alert_times = {}
_alert_queue = []
_alert_queue_lock = threading.Lock()
_email_lock = threading.Lock()

# ── Config Management ──────────────────────────────────────────────────
def load_email_config():
    global _email_config
    if not os.path.exists(EMAIL_CONFIG_PATH):
        _email_config = DEFAULT_CONFIG.copy()
        save_email_config()
        return _email_config
    try:
        with open(EMAIL_CONFIG_PATH, "r") as f:
            loaded = json.load(f)
        merged = DEFAULT_CONFIG.copy()
        for key, value in loaded.items():
            if isinstance(value, dict) and key in merged and isinstance(merged[key], dict):
                merged[key].update(value)
            else:
                merged[key] = value
        _email_config = merged
        return _email_config
    except Exception:
        _email_config = DEFAULT_CONFIG.copy()
        return _email_config

def save_email_config(config=None):
    global _email_config
    if config is not None:
        _email_config = config
    with open(EMAIL_CONFIG_PATH, "w") as f:
        json.dump(_email_config, f, indent=4)

def get_email_config():
    global _email_config
    if _email_config is None:
        load_email_config()
    return _email_config

# ── Cooldown / Spam Prevention ────────────────────────────────────────
def is_alert_on_cooldown(alert_type):
    config = get_email_config()
    if not config.get("cooldown", {}).get("enabled", True):
        return False
    cooldown_minutes = config["cooldown"].get("minutes", 5)
    now = time.time()
    last_time = _last_alert_times.get(alert_type, 0)
    elapsed = (now - last_time) / 60.0
    return elapsed < cooldown_minutes

def mark_alert_sent(alert_type):
    _last_alert_times[alert_type] = time.time()

# ── Gmail API Helpers ─────────────────────────────────────────────────
def get_gmail_service():
    """
    Get authenticated Gmail API service using OAuth credentials from token file.
    Returns (service, error_message).
    """
    if not GOOGLE_API_AVAILABLE:
        return None, "Google API libraries not installed. Run: pip install google-auth-oauthlib google-api-python-client"

    token_file = TOKEN_PATH
    if not os.path.exists(token_file):
        return None, "Not authenticated with Google. Click 'Sign in with Google' first."

    creds = None
    try:
        with open(token_file, "r") as f:
            token_data = json.load(f)
        creds = Credentials.from_authorized_user_info(token_data, SCOPES)
    except Exception as e:
        return None, f"Failed to load credentials: {str(e)}"

    # If credentials are expired, try to refresh
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleRequest())
            # Save refreshed credentials
            with open(token_file, "w") as f:
                f.write(creds.to_json())
        except Exception as e:
            return None, f"Failed to refresh credentials: {str(e)}. Re-authenticate."

    if not creds or not creds.valid:
        return None, "Credentials invalid. Re-authenticate with Google."

    try:
        service = build("gmail", "v1", credentials=creds)
        return service, None
    except Exception as e:
        return None, f"Failed to build Gmail service: {str(e)}"

def send_via_gmail_api(subject, body_html, body_plain, recipient):
    """Send email using Gmail API."""
    service, error = get_gmail_service()
    if error:
        return False, error

    message = MIMEMultipart("alternative")
    message["To"] = recipient
    message["From"] = "me"  # Gmail API uses "me" for authenticated user
    message["Subject"] = subject
    message.attach(MIMEText(body_plain, "plain"))
    message.attach(MIMEText(body_html, "html"))

    try:
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return True, None
    except Exception as e:
        return False, f"Gmail API error: {str(e)}"

# ── SMTP Email Sending (fallback) ──────────────────────────────────────
def send_via_smtp(subject, body_html, body_plain, recipient):
    """Send email using SMTP (for Gmail SMTP, Outlook, custom)."""
    config = get_email_config()
    smtp_cfg = config.get("smtp_config", {})
    sender_email = smtp_cfg.get("email", "").strip()
    password = smtp_cfg.get("password", "").strip()

    if not sender_email or not password:
        return False, "SMTP credentials not configured"

    host = smtp_cfg.get("host", "smtp.gmail.com")
    port = smtp_cfg.get("port", 587)
    use_tls = smtp_cfg.get("use_tls", True)

    msg = MIMEMultipart("alternative")
    msg["From"] = sender_email
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.attach(MIMEText(body_plain, "plain"))
    msg.attach(MIMEText(body_html, "html"))

    with _email_lock:
        try:
            if use_tls:
                context = ssl.create_default_context()
                with smtplib.SMTP(host, port, timeout=10) as server:
                    server.starttls(context=context)
                    server.login(sender_email, password)
                    server.sendmail(sender_email, recipient, msg.as_string())
            else:
                with smtplib.SMTP_SSL(host, port, timeout=10) as server:
                    server.login(sender_email, password)
                    server.sendmail(sender_email, recipient, msg.as_string())
            return True, None
        except smtplib.SMTPAuthenticationError:
            return False, "SMTP authentication failed. Check email/password."
        except smtplib.SMTPException as e:
            return False, f"SMTP error: {str(e)}"
        except Exception as e:
            return False, f"Failed to send email: {str(e)}"

# ── Email Sending ──────────────────────────────────────────────────────
def build_email_body(subject, body_content):
    """Build HTML and plain text email body."""
    html_body = f"""\
<html>
<body style="font-family: Arial, sans-serif; padding: 20px; color: #333;">
    <div style="max-width: 600px; margin: 0 auto; border: 1px solid #ddd; border-radius: 8px; overflow: hidden;">
        <div style="background: #182456; color: white; padding: 15px 20px; font-size: 18px; font-weight: bold;">
            Thickness Monitoring System - Alert
        </div>
        <div style="padding: 20px;">
            {body_content}
        </div>
        <div style="background: #f5f5f5; padding: 10px 20px; font-size: 12px; color: #999; text-align: center;">
            This is an automated alert from the Thickness Monitoring System.
        </div>
    </div>
</body>
</html>
"""
    # Strip HTML for plain text version
    import re
    plain_text = re.sub(r'<[^>]+>', '', body_content)
    plain_text = plain_text.replace('&nbsp;', ' ').strip()
    return html_body, plain_text

def send_email(subject, body_content):
    """
    Send an email using the configured method.
    Returns (success: bool, error_message: str or None)
    """
    config = get_email_config()
    if not config.get("enabled", False):
        return False, "Email alerts are disabled"

    recipient = config.get("recipient_email", "").strip()
    if not recipient:
        return False, "No recipient email configured"

    html_body, plain_text = build_email_body(subject, body_content)
    api_type = config.get("api_type", "gmail_oauth")

    if api_type == "gmail_oauth":
        return send_via_gmail_api(subject, html_body, plain_text, recipient)
    else:
        return send_via_smtp(subject, html_body, plain_text, recipient)

# ── Alert Triggering API ──────────────────────────────────────────────
def trigger_alert(alert_type, title, details):
    config = get_email_config()
    if not config.get("enabled", False):
        return {"success": False, "reason": "Email alerts disabled"}

    alert_toggles = config.get("alerts", {})
    if not alert_toggles.get(alert_type, True):
        return {"success": False, "reason": f"Alert type '{alert_type}' is disabled"}

    if is_alert_on_cooldown(alert_type):
        with _alert_queue_lock:
            _alert_queue.append({
                "type": alert_type,
                "title": title,
                "details": details,
                "timestamp": datetime.datetime.now().isoformat(),
            })
        return {"success": False, "reason": "Alert on cooldown, queued for grouping"}

    body = f"""
    <p><strong>Alert Type:</strong> {alert_type}</p>
    <p><strong>Time:</strong> {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    <p><strong>Details:</strong></p>
    <p>{details}</p>
    """
    success, error = send_email(f"[Thickness Monitor] {title}", body)
    if success:
        mark_alert_sent(alert_type)
        return {"success": True, "reason": "Alert sent successfully"}
    else:
        return {"success": False, "reason": error}

def flush_queued_alerts():
    with _alert_queue_lock:
        if not _alert_queue:
            return
        alerts_to_send = _alert_queue[:]
        _alert_queue.clear()

    if not alerts_to_send:
        return

    config = get_email_config()
    if not config.get("enabled", False):
        return

    subject = f"[Thickness Monitor] {len(alerts_to_send)} Alert(s) - Grouped Report"
    body_parts = []
    for alert in alerts_to_send:
        body_parts.append(f"""
        <div style="border-left: 4px solid #ff9800; padding: 10px; margin: 10px 0; background: #fff8e1;">
            <h4 style="margin: 0 0 5px 0;">{alert['title']}</h4>
            <p style="margin: 0; color: #666;">Type: {alert['type']} | Time: {alert['timestamp']}</p>
            <p style="margin: 5px 0 0 0;">{alert['details']}</p>
        </div>
        """)

    body = f"""
    <h3>Grouped Alerts Summary</h3>
    <p>The following {len(alerts_to_send)} alert(s) were triggered:</p>
    {''.join(body_parts)}
    """
    send_email(subject, body)

    for alert in alerts_to_send:
        mark_alert_sent(alert["type"])

# ── Summary Report Generation ──────────────────────────────────────────
def generate_summary_report(report_type="daily"):
    config = get_email_config()
    if not config.get("summary_report", {}).get("enabled", False):
        return

    now = datetime.datetime.now()
    if report_type == "daily":
        period = "Daily"
        start_time = (now - datetime.timedelta(days=1)).isoformat()
    else:
        period = "Weekly"
        start_time = (now - datetime.timedelta(days=7)).isoformat()

    subject = f"[Thickness Monitor] {period} Summary Report - {now.strftime('%Y-%m-%d')}"

    try:
        import psycopg2
        conn = psycopg2.connect(
            host="localhost", database="sensor_db",
            user="rapl", password="rapl2026"
        )
        cur = conn.cursor()
        cur.execute("""
            SELECT MIN(thickness), MAX(thickness), AVG(thickness), COUNT(*)
            FROM opposite_thickness_readings
            WHERE timestamp >= %s
        """, (start_time,))
        row = cur.fetchone()
        min_thick = round(row[0], 3) if row[0] is not None else "N/A"
        max_thick = round(row[1], 3) if row[1] is not None else "N/A"
        avg_thick = round(row[2], 3) if row[2] is not None else "N/A"
        total_readings = row[3] if row[3] is not None else 0
        cur.close()
        conn.close()
    except Exception:
        min_thick = max_thick = avg_thick = "N/A"
        total_readings = 0

    body = f"""
    <h3>{period} Summary Report</h3>
    <table style="width: 100%; border-collapse: collapse; margin: 15px 0;">
        <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>Period</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{period}</td></tr>
        <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>Date</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{now.strftime('%Y-%m-%d')}</td></tr>
        <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>Min Thickness</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{min_thick} mm</td></tr>
        <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>Max Thickness</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{max_thick} mm</td></tr>
        <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>Avg Thickness</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{avg_thick} mm</td></tr>
        <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>Total Readings</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{total_readings}</td></tr>
    </table>
    <p><em>Report generated automatically by Thickness Monitoring System.</em></p>
    """
    send_email(subject, body)

# ── Background Thread ──────────────────────────────────────────────────
_alert_flush_thread = None
_alert_flush_running = False

def _background_flush_loop():
    global _alert_flush_running
    last_summary_date = None
    last_weekly_summary_date = None

    while _alert_flush_running:
        try:
            flush_queued_alerts()
            config = get_email_config()
            summary_cfg = config.get("summary_report", {})
            if summary_cfg.get("enabled", False):
                now = datetime.datetime.now()
                today = now.date()
                if summary_cfg.get("frequency") == "daily":
                    if last_summary_date != today and now.hour == 0 and now.minute < 5:
                        generate_summary_report("daily")
                        last_summary_date = today
                elif summary_cfg.get("frequency") == "weekly":
                    if now.weekday() == 0:
                        if last_weekly_summary_date != today and now.hour == 0 and now.minute < 5:
                            generate_summary_report("weekly")
                            last_weekly_summary_date = today
        except Exception:
            pass
        time.sleep(30)

def start_background_tasks():
    global _alert_flush_thread, _alert_flush_running
    if _alert_flush_thread is not None and _alert_flush_thread.is_alive():
        return
    _alert_flush_running = True
    _alert_flush_thread = threading.Thread(target=_background_flush_loop, daemon=True)
    _alert_flush_thread.start()

# ── Flask Blueprint Routes ─────────────────────────────────────────────
email_alerts_bp = Blueprint("email_alerts", __name__)

@email_alerts_bp.route("/email-alerts/config", methods=["GET"])
def get_email_alert_config():
    config = get_email_config()
    safe_config = json.loads(json.dumps(config))
    if "smtp_config" in safe_config:
        safe_config["smtp_config"]["password"] = "********" if safe_config["smtp_config"].get("password") else ""
    # Check if Gmail OAuth is authenticated
    safe_config["gmail_authenticated"] = os.path.exists(TOKEN_PATH)
    return jsonify(safe_config), 200

@email_alerts_bp.route("/email-alerts/config", methods=["POST"])
def update_email_alert_config():
    data = request.json or {}
    config = get_email_config()

    if "enabled" in data:
        config["enabled"] = bool(data["enabled"])
    if "api_type" in data:
        config["api_type"] = str(data["api_type"])
    if "recipient_email" in data:
        config["recipient_email"] = str(data["recipient_email"]).strip()

    if "smtp_config" in data:
        smtp = data["smtp_config"]
        if "host" in smtp:
            config["smtp_config"]["host"] = str(smtp["host"])
        if "port" in smtp:
            config["smtp_config"]["port"] = int(smtp["port"])
        if "use_tls" in smtp:
            config["smtp_config"]["use_tls"] = bool(smtp["use_tls"])
        if "email" in smtp:
            config["smtp_config"]["email"] = str(smtp["email"]).strip()
        if "password" in smtp and smtp["password"] and smtp["password"] != "********":
            config["smtp_config"]["password"] = smtp["password"]

    if "alerts" in data:
        for key in config["alerts"]:
            if key in data["alerts"]:
                config["alerts"][key] = bool(data["alerts"][key])

    if "cooldown" in data:
        if "enabled" in data["cooldown"]:
            config["cooldown"]["enabled"] = bool(data["cooldown"]["enabled"])
        if "minutes" in data["cooldown"]:
            config["cooldown"]["minutes"] = int(data["cooldown"]["minutes"])

    if "summary_report" in data:
        if "enabled" in data["summary_report"]:
            config["summary_report"]["enabled"] = bool(data["summary_report"]["enabled"])
        if "frequency" in data["summary_report"]:
            config["summary_report"]["frequency"] = str(data["summary_report"]["frequency"])

    save_email_config(config)
    return jsonify({"message": "Email alert configuration updated successfully"}), 200

@email_alerts_bp.route("/email-alerts/test", methods=["POST"])
def test_email_alert():
    config = get_email_config()
    recipient = config.get("recipient_email", "").strip()
    if not recipient:
        return jsonify({"error": "No recipient email configured"}), 400

    subject = "[Thickness Monitor] Test Email"
    body = """
    <p>This is a test email from the Thickness Monitoring System.</p>
    <p>If you received this, your email configuration is working correctly.</p>
    """

    success, error = send_email(subject, body)
    if success:
        return jsonify({"message": "Test email sent successfully"}), 200
    else:
        return jsonify({"error": error}), 500

@email_alerts_bp.route("/email-alerts/api-options", methods=["GET"])
def get_api_options():
    options = [
        {
            "id": "gmail_oauth",
            "name": "Gmail (OAuth - Recommended)",
            "description": "Sign in with Google - no password needed. Most secure.",
            "is_oauth": True,
            "available": GOOGLE_API_AVAILABLE,
            "authenticated": os.path.exists(TOKEN_PATH),
        },
        {
            "id": "gmail_smtp",
            "name": "Gmail (App Password)",
            "description": "Use Gmail SMTP with App Password (smtp.gmail.com:587)",
            "is_oauth": False,
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "requires_app_password": True,
        },
        {
            "id": "outlook",
            "name": "Outlook / Office 365",
            "description": "Use Outlook SMTP (smtp-mail.outlook.com:587)",
            "is_oauth": False,
            "smtp_host": "smtp-mail.outlook.com",
            "smtp_port": 587,
            "requires_app_password": True,
        },
        {
            "id": "yahoo",
            "name": "Yahoo Mail",
            "description": "Use Yahoo SMTP (smtp.mail.yahoo.com:587)",
            "is_oauth": False,
            "smtp_host": "smtp.mail.yahoo.com",
            "smtp_port": 587,
            "requires_app_password": True,
        },
        {
            "id": "sendgrid",
            "name": "SendGrid",
            "description": "Use SendGrid SMTP (smtp.sendgrid.net:587)",
            "is_oauth": False,
            "smtp_host": "smtp.sendgrid.net",
            "smtp_port": 587,
            "requires_app_password": False,
        },
        {
            "id": "custom_smtp",
            "name": "Custom SMTP",
            "description": "Use your own SMTP server",
            "is_oauth": False,
            "smtp_host": "",
            "smtp_port": 587,
            "requires_app_password": False,
        },
    ]
    return jsonify(options), 200

@email_alerts_bp.route("/email-alerts/auth-url", methods=["GET"])
def get_auth_url():
    """Get Google OAuth URL for the user to sign in."""
    global _pending_flow
    if not GOOGLE_API_AVAILABLE:
        return jsonify({"error": "Google API libraries not installed"}), 500

    client_config = {
        "web": {
            "client_id": "676970971720-mdce53i1i4agalvvrn72psnmnvvroer0.apps.googleusercontent.com",
            "project_id": "thickness-monitor-alerts",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": "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",
            "redirect_uris": [OAUTH_REDIRECT_URI]
        }
    }

    try:
        flow = InstalledAppFlow.from_client_config(
            client_config, SCOPES,
            redirect_uri=OAUTH_REDIRECT_URI
        )
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent",
            include_granted_scopes="true"
        )
        # Store the flow for callback
        _pending_flow = flow
        return jsonify({"auth_url": auth_url}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to generate auth URL: {str(e)}"}), 500

# Store pending flow in memory for callback
_pending_flow = None

@email_alerts_bp.route("/email-alerts/oauth-callback", methods=["GET"])
def oauth_callback():
    """Handle the OAuth callback from Google."""
    global _pending_flow
    if not GOOGLE_API_AVAILABLE:
        return jsonify({"error": "Google API libraries not installed"}), 500

    authorization_response = request.url
    # When behind ngrok, request.url uses http://localhost:5000 but
    # Google's OAuth requires HTTPS matching the registered redirect URI.
    # Replace the local base with the OAUTH_REDIRECT_URI base.
    try:
        from urllib.parse import urlparse
        parsed_local = urlparse(authorization_response)
        parsed_https = urlparse(OAUTH_REDIRECT_URI)
        if parsed_local.netloc == "localhost:5000" and parsed_https.scheme == "https":
            authorization_response = authorization_response.replace(
                f"http://localhost:5000",
                f"{parsed_https.scheme}://{parsed_https.netloc}",
                1
            )
    except Exception:
        pass

    try:
        flow = _pending_flow
        if flow is None:
            # Pending flow was lost (e.g. server restart, multiple tabs).
            # Reconstruct from known client config and the state in the URL.
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(authorization_response)
            params = parse_qs(parsed.query)
            state_val = params.get("state", [None])[0]

            client_config = {
                "web": {
                    "client_id": "676970971720-mdce53i1i4agalvvrn72psnmnvvroer0.apps.googleusercontent.com",
                    "project_id": "thickness-monitor-alerts",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                    "client_secret": "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",
                    "redirect_uris": [OAUTH_REDIRECT_URI]
                }
            }
            flow = InstalledAppFlow.from_client_config(
                client_config, SCOPES,
                redirect_uri=OAUTH_REDIRECT_URI
            )
            # Restore the state from the callback URL so fetch_token can validate it
            if state_val:
                flow.oauth2session.state = state_val

        flow.fetch_token(authorization_response=authorization_response)
        creds = flow.credentials

        # Save credentials to token file
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())

        _pending_flow = None

        # Auto-update config to use gmail_oauth
        config = get_email_config()
        config["api_type"] = "gmail_oauth"
        save_email_config(config)

        # Return HTML so the popup window shows a friendly message (not raw JSON)
        html_response = f"""\
<html><head><meta charset="utf-8"><title>Authentication Successful</title>
<style>
body {{ font-family: Arial, sans-serif; display: flex; justify-content: center;
       align-items: center; height: 100vh; margin: 0; background: #f5f5f5; }}
.card {{ background: white; padding: 40px; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,0.15);
        text-align: center; max-width: 420px; }}
h2 {{ color: #2e7d32; margin-top: 0; }} .check {{ font-size: 64px; color: #2e7d32; }}
p {{ color: #555; line-height: 1.5; }}
</style></head><body>
<div class="card">
<div class="check">&#10003;</div>
<h2>Authentication Successful!</h2>
<p>Your Google account is now connected.<br>
You can close this window and return to the application.</p>
</div></body></html>"""
        return html_response, 200, {"Content-Type": "text/html"}
    except Exception as e:
        _pending_flow = None
        html_error = f"""\
<html><head><meta charset="utf-8"><title>Authentication Failed</title>
<style>
body {{ font-family: Arial, sans-serif; display: flex; justify-content: center;
       align-items: center; height: 100vh; margin: 0; background: #f5f5f5; }}
.card {{ background: white; padding: 40px; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,0.15);
        text-align: center; max-width: 420px; }}
h2 {{ color: #c62828; margin-top: 0; }} .x {{ font-size: 64px; color: #c62828; }}
p {{ color: #555; line-height: 1.5; }}
</style></head><body>
<div class="card">
<div class="x">&#10007;</div>
<h2>Authentication Failed</h2>
<p style="color:#c62828;">{str(e)}</p>
<p>Please close this window and try signing in again from the application.</p>
</div></body></html>"""
        return html_error, 200, {"Content-Type": "text/html"}

@email_alerts_bp.route("/email-alerts/trigger/<alert_type>", methods=["POST"])
def trigger_alert_api(alert_type):
    data = request.json or {}
    title = data.get("title", f"Alert: {alert_type}")
    details = data.get("details", "Triggered via API")
    result = trigger_alert(alert_type, title, details)
    if result.get("success"):
        return jsonify({"message": result["reason"]}), 200
    else:
        return jsonify({"message": result["reason"], "queued": "queued" in result.get("reason", "")}), 200

# ── Load config on import ──────────────────────────────────────────────
load_email_config()
start_background_tasks()