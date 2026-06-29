"""
Deploy merged project:
  KVM  (194.164.148.145): Flask backend (CLOUD_MODE) on port 5002, nginx on 8082
  Ubuntu (192.168.5.13):  pi_client.py as systemd service → posts to KVM:8082
"""
import paramiko
import os
import stat

# ---- helpers ----
def run(client, cmd, check=True):
    stdin, stdout, stderr = client.exec_command(cmd)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    if check and err.strip():
        safe = err.strip().encode('ascii', errors='replace').decode('ascii')
        print(f"  [stderr] {safe}")
    return out, err

def upload_file(sftp, local_path, remote_path):
    sftp.put(local_path, remote_path)
    print(f"  uploaded {os.path.basename(local_path)} -> {remote_path}")

def upload_text(sftp, content, remote_path):
    with sftp.file(remote_path, 'w') as f:
        f.write(content)
    print(f"  wrote -> {remote_path}")

BASE = os.path.dirname(os.path.abspath(__file__))

# ============================================================
# 1. DEPLOY TO KVM
# ============================================================
print("\n" + "=" * 55)
print("STEP 1: Deploying backend + frontend to KVM")
print("=" * 55)

kvm = paramiko.SSHClient()
kvm.set_missing_host_key_policy(paramiko.AutoAddPolicy())
kvm.connect('194.164.148.145', username='root', password='Federer7roger@', timeout=15)

# Create directory structure
print("\n[KVM] Creating directory structure...")
run(kvm, "mkdir -p /opt/merged/backend /opt/merged/dist")
run(kvm, "chown -R www-data:www-data /opt/merged")

# Upload backend files
print("\n[KVM] Uploading backend files...")
sftp = kvm.open_sftp()
backend_files = [
    "merged_server.py",
    "user_routes.py",
    "download_routes.py",
    "email_alert_routes.py",
    "sensor_config.json",
    "sensor_network.json",
]
for fname in backend_files:
    local = os.path.join(BASE, "backend", fname)
    if os.path.exists(local):
        upload_file(sftp, local, f"/opt/merged/backend/{fname}")
    else:
        print(f"  SKIP (not found): {fname}")

# Upload frontend dist
print("\n[KVM] Uploading frontend dist...")
dist_dir = os.path.join(BASE, "dist")
run(kvm, "mkdir -p /opt/merged/dist/assets")

for root, dirs, files in os.walk(dist_dir):
    for fname in files:
        local_path  = os.path.join(root, fname)
        rel_path    = os.path.relpath(local_path, dist_dir).replace("\\", "/")
        remote_path = f"/opt/merged/dist/{rel_path}"
        # ensure remote dir exists
        remote_dir = os.path.dirname(remote_path)
        run(kvm, f"mkdir -p {remote_dir}", check=False)
        upload_file(sftp, local_path, remote_path)

# Set up Python venv
print("\n[KVM] Setting up Python venv...")
out, err = run(kvm, "test -d /opt/merged/venv && echo EXISTS || echo MISSING")
if "MISSING" in out:
    out, err = run(kvm, "python3 -m venv /opt/merged/venv 2>&1")
    print(f"  venv created: {out.strip()}")
else:
    print("  venv already exists")

print("\n[KVM] Installing Python dependencies...")
out, err = run(kvm, "/opt/merged/venv/bin/pip install --quiet Flask Flask-Cors Flask-SocketIO psycopg2-binary Werkzeug 2>&1")
print(f"  pip: {out.strip()[-100:] if out.strip() else 'done'}")

# Install systemd service
print("\n[KVM] Installing systemd service...")
upload_file(sftp, os.path.join(BASE, "thickness-monitor.service"), "/etc/systemd/system/merged.service")

sftp.close()

# Set ownership
run(kvm, "chown -R www-data:www-data /opt/merged")

# Enable and start service
print("\n[KVM] Enabling and starting merged service...")
run(kvm, "systemctl daemon-reload")
run(kvm, "systemctl stop merged 2>/dev/null || true")
run(kvm, "systemctl enable merged")
out, err = run(kvm, "systemctl start merged")
import time; time.sleep(3)
out, _ = run(kvm, "systemctl is-active merged")
print(f"  merged service status: {out.strip()}")

# Install nginx config
print("\n[KVM] Installing nginx config...")
sftp = kvm.open_sftp()
upload_file(sftp, os.path.join(BASE, "nginx_merged.conf"), "/etc/nginx/sites-available/merged")
sftp.close()
run(kvm, "ln -sf /etc/nginx/sites-available/merged /etc/nginx/sites-enabled/merged")
run(kvm, "rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true")
out, err = run(kvm, "nginx -t 2>&1")
print(f"  nginx test: {out.strip()} {err.strip()}")
run(kvm, "systemctl reload nginx")
print("  nginx reloaded")

# Install nginx watchdog (auto-repairs port 8082 every 5 min if it goes missing)
print("\n[KVM] Installing nginx watchdog...")
NGINX_WATCHDOG_SCRIPT = (
    '#!/bin/bash\n'
    '# Watchdog: ensure nginx merged site (port 8082) is always active.\n'
    'if ! ss -tlnp | grep -q \':8082 \'; then\n'
    '    ln -sf /etc/nginx/sites-available/merged /etc/nginx/sites-enabled/merged\n'
    '    nginx -t && systemctl reload nginx\n'
    '    logger "nginx-watchdog: port 8082 was down -- re-enabled merged site and reloaded nginx"\n'
    'fi\n'
)
NGINX_WATCHDOG_SERVICE = (
    '[Unit]\nDescription=Nginx port 8082 watchdog\n\n'
    '[Service]\nType=oneshot\nExecStart=/usr/local/bin/nginx-watchdog.sh\n'
)
NGINX_WATCHDOG_TIMER = (
    '[Unit]\nDescription=Run nginx port 8082 watchdog every 5 minutes\n\n'
    '[Timer]\nOnBootSec=60\nOnUnitActiveSec=5min\nUnit=nginx-watchdog.service\n\n'
    '[Install]\nWantedBy=timers.target\n'
)
sftp = kvm.open_sftp()
upload_text(sftp, NGINX_WATCHDOG_SCRIPT, '/usr/local/bin/nginx-watchdog.sh')
upload_text(sftp, NGINX_WATCHDOG_SERVICE, '/etc/systemd/system/nginx-watchdog.service')
upload_text(sftp, NGINX_WATCHDOG_TIMER,   '/etc/systemd/system/nginx-watchdog.timer')
sftp.close()
run(kvm, 'chmod +x /usr/local/bin/nginx-watchdog.sh')
run(kvm, 'systemctl daemon-reload && systemctl enable --now nginx-watchdog.timer')
print("  nginx watchdog timer enabled (runs every 5 min)")

# Verify
out, _ = run(kvm, "ss -tlnp | grep -E '5002|8082|:443|:80'")
print(f"\n[KVM] Listening ports:\n{out}")

kvm.close()

# ============================================================
# 2. DEPLOY PI CLIENT TO UBUNTU DEVICE
# ============================================================
print("\n" + "=" * 55)
print("STEP 2: Deploying pi_client to Ubuntu (192.168.5.13)")
print("=" * 55)

ubuntu = paramiko.SSHClient()
ubuntu.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ubuntu.connect('192.168.5.13', username='linux', password='linux', timeout=15)

print("\n[Ubuntu] Uploading backend files to merged-version...")
sftp2 = ubuntu.open_sftp()
# Ensure add-kvm-route.sh exists so ExecStartPre in the service works
route_sh = (
    '#!/bin/bash\n'
    '# Bring up wired sensor interface (enp3s0) if not already active.\n'
    '# Sensors A and B are on 192.168.5.x via the wired switch — WiFi cannot reach them.\n'
    'nmcli connection show --active | grep -q "Wired connection 1" \\\n'
    '  || nmcli connection up "Wired connection 1" 2>/dev/null\n'
    '# Ensure route to KVM cloud server goes via WiFi gateway.\n'
    '/sbin/ip route add 194.164.148.145/32 via 192.168.5.1 dev wlx002e2d1034b9 2>/dev/null || true\n'
    'exit 0\n'
)
upload_text(sftp2, route_sh, '/home/linux/add-kvm-route.sh')
run(ubuntu, 'chmod +x /home/linux/add-kvm-route.sh')
for fname in ["pi_client.py", "sensor_network.json", "merged_server.py",
              "user_routes.py", "download_routes.py", "email_alert_routes.py"]:
    local = os.path.join(BASE, "backend", fname)
    if os.path.exists(local):
        upload_file(sftp2, local, f"/home/linux/merged-version/backend/{fname}")

# Install service pointing directly at merged-version
service_content = open(os.path.join(BASE, "backend", "pi_merged.service")).read()
upload_text(sftp2, service_content, "/home/linux/merged-version/backend/pi_merged.service")
sftp2.close()

print("\n[Ubuntu] Installing and starting pi-merged-client service...")
run(ubuntu, "sudo cp /home/linux/merged-version/backend/pi_merged.service /etc/systemd/system/pi-merged-client.service")
run(ubuntu, "sudo systemctl daemon-reload")
run(ubuntu, "sudo systemctl enable pi-merged-client")
run(ubuntu, "sudo systemctl restart pi-merged-client")
time.sleep(3)
out, _ = run(ubuntu, "sudo systemctl is-active pi-merged-client")
print(f"  pi-merged-client status: {out.strip()}")

out, _ = run(ubuntu, "sudo systemctl status pi-merged-client --no-pager -n 10 2>&1")
print(f"\n[Ubuntu] Service status:\n{out}")

# Install pi-client watchdog cron (restarts service if no HTTP 200 in last 2 min)
print("\n[Ubuntu] Installing pi-client watchdog cron...")
PI_WATCHDOG = (
    '#!/bin/bash\n'
    '# Watchdog: restart pi-merged-client if running but not posting successfully.\n'
    'if systemctl is-active --quiet pi-merged-client; then\n'
    '    if ! journalctl -u pi-merged-client --since "2 minutes ago" --no-pager -q | grep -q "HTTP 200"; then\n'
    '        systemctl restart pi-merged-client\n'
    '        logger "pi-client-watchdog: no successful POST in 2 min -- restarted pi-merged-client"\n'
    '    fi\n'
    'fi\n'
)
sftp2 = ubuntu.open_sftp()
upload_text(sftp2, PI_WATCHDOG, '/tmp/pi_watchdog.sh')
sftp2.close()
run(ubuntu, 'sudo cp /tmp/pi_watchdog.sh /usr/local/bin/pi-client-watchdog.sh')
run(ubuntu, 'sudo chmod +x /usr/local/bin/pi-client-watchdog.sh')
run(ubuntu, 'sudo bash -c "(crontab -l 2>/dev/null | grep -v pi-client-watchdog; echo \'*/5 * * * * /usr/local/bin/pi-client-watchdog.sh\') | crontab -"')
print("  pi-client watchdog cron enabled (runs every 5 min)")

ubuntu.close()

print("\n" + "=" * 55)
print("DEPLOYMENT COMPLETE")
print("  LAN access  -> http://194.164.148.145:8082")
print("  Vercel HTTPS -> https://194-164-148-145.sslip.io")
print("=" * 55)
