#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# Tools Platform — Deploy / Provision Script
# Target: Ubuntu 24.04 LTS — run as root on a fresh or existing VPS.
# Idempotent — safe to re-run for updates (pulls latest code, restarts).
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_URL="${TOOLS_REPO_URL:-https://github.com/JcbBnd26/swpppautofill_windows.git}"
REPO_DIR="/opt/tools/repo"
VENV_DIR="/opt/tools/venv"
DATA_DIR="/opt/tools/data"
LOG_DIR="/var/log/tools"
BACKUP_DIR="/opt/tools/backups"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "══════════════════════════════════════════════════════════"
echo "  Tools Platform — Deploy"
echo "══════════════════════════════════════════════════════════"

# ── 1. System user ────────────────────────────────────────────────────
if ! id -u tools &>/dev/null; then
    useradd --system --shell /usr/sbin/nologin --home-dir /opt/tools tools
    echo "[+] Created system user: tools"
else
    echo "[=] System user 'tools' already exists"
fi

# ── 2. System packages ───────────────────────────────────────────────
echo "[*] Installing system packages..."
apt-get update -qq
apt-get install -y -qq \
    python3 python3-venv python3-pip \
    nginx certbot python3-certbot-nginx \
    fail2ban ufw sqlite3 git \
    unattended-upgrades

# ── 3. Firewall (UFW) ────────────────────────────────────────────────
echo "[*] Configuring firewall..."
ufw default deny incoming >/dev/null 2>&1 || true
ufw default allow outgoing >/dev/null 2>&1 || true
ufw allow 22/tcp >/dev/null 2>&1 || true
ufw allow 80/tcp >/dev/null 2>&1 || true
ufw allow 443/tcp >/dev/null 2>&1 || true
ufw --force enable >/dev/null 2>&1 || true
echo "[+] UFW enabled (22, 80, 443)"

# ── 4. Fail2ban ──────────────────────────────────────────────────────
systemctl enable --now fail2ban >/dev/null 2>&1
echo "[+] Fail2ban enabled"

# ── 5. Unattended upgrades ───────────────────────────────────────────
dpkg-reconfigure -plow unattended-upgrades >/dev/null 2>&1 || true
echo "[+] Unattended upgrades enabled"

# ── 6. Clone or update repo ──────────────────────────────────────────
if [ -d "$REPO_DIR/.git" ]; then
    echo "[*] Pulling latest code..."
    git -C "$REPO_DIR" pull --ff-only
else
    echo "[*] Cloning repository..."
    mkdir -p "$(dirname "$REPO_DIR")"
    git clone "$REPO_URL" "$REPO_DIR"
fi

# ── 7. Python virtual environment ────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    echo "[*] Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

echo "[*] Installing Python dependencies..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet "$REPO_DIR"
"$VENV_DIR/bin/pip" install --quiet \
    fastapi "uvicorn[standard]" gunicorn python-multipart

# ── 8. Directories ───────────────────────────────────────────────────
mkdir -p "$DATA_DIR" "$LOG_DIR" "$BACKUP_DIR"
chown tools:tools "$DATA_DIR" "$LOG_DIR" "$BACKUP_DIR"
echo "[+] Data/log/backup directories ready"

# ── 9. Systemd units ─────────────────────────────────────────────────
echo "[*] Installing systemd units..."
cp "$REPO_DIR/web/scripts/systemd/tools-auth.service" /etc/systemd/system/
cp "$REPO_DIR/web/scripts/systemd/tools-swppp.service" /etc/systemd/system/
systemctl daemon-reload

systemctl enable tools-auth tools-swppp
systemctl restart tools-auth tools-swppp
echo "[+] Services enabled and (re)started"

# ── 10. Nginx ─────────────────────────────────────────────────────────
echo "[*] Installing Nginx config..."
cp "$REPO_DIR/web/scripts/nginx/tools.conf" /etc/nginx/sites-available/tools.conf
ln -sf /etc/nginx/sites-available/tools.conf /etc/nginx/sites-enabled/tools.conf
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl reload nginx
echo "[+] Nginx configured and reloaded"

# ── 11. Bootstrap admin (first run only) ──────────────────────────────
if [ ! -f "$DATA_DIR/auth.db" ]; then
    echo "[*] Initializing database and admin invite..."
    sudo -u tools \
        PYTHONPATH="$REPO_DIR" \
        TOOLS_DATA_DIR="$DATA_DIR" \
        TOOLS_DEV_MODE=0 \
        "$VENV_DIR/bin/python" "$REPO_DIR/web/scripts/init_admin.py"
else
    echo "[=] Database already exists — skipping admin init"
fi

# ── 12. Backup cron ──────────────────────────────────────────────────
echo "[*] Installing backup cron..."
cat > /etc/cron.d/tools-backup <<'CRON'
# Daily SQLite backup at 02:00
0 2 * * * tools /opt/tools/repo/web/scripts/backup.sh >> /var/log/tools/backup.log 2>&1
CRON
chmod 644 /etc/cron.d/tools-backup

# ── 13. Temp file cleanup cron ────────────────────────────────────────
cat > /etc/cron.d/tools-tmp-cleanup <<'CRON'
# Sweep stale PDF generation dirs every hour
0 * * * * root find /tmp -maxdepth 1 -name 'swppp_gen_*' -type d -mmin +60 -exec rm -rf {} +
CRON
chmod 644 /etc/cron.d/tools-tmp-cleanup

echo ""
echo "══════════════════════════════════════════════════════════"
echo "  Deploy complete!"
echo ""
echo "  Next steps:"
echo "    1. Edit /etc/nginx/sites-available/tools.conf"
echo "       → replace 'tools.example.com' with your domain"
echo "    2. sudo certbot --nginx -d yourdomain.com"
echo "    3. sudo nginx -t && sudo systemctl reload nginx"
echo "    4. Claim the admin invite code (printed above on first run)"
echo "══════════════════════════════════════════════════════════"
