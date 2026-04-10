# Tools Platform — Deployment Guide

## Prerequisites

- **VPS**: Ubuntu 24.04 LTS (DigitalOcean, Linode, etc.) with root SSH access
- **Domain**: DNS A record pointing to the VPS IP (e.g. `tools.yourdomain.com`)
- **SSH key**: Configured for root login (disable password auth after setup)
- **Git access**: The repo must be cloneable from the VPS (public or deploy key)

## First-Time Deploy

### 1. SSH into the VPS

```bash
ssh root@YOUR_VPS_IP
```

### 2. Run the deploy script

If the repo is already cloned locally:

```bash
git clone https://github.com/JcbBnd26/swpppautofill_windows.git /opt/tools/repo
bash /opt/tools/repo/web/scripts/deploy.sh
```

Or let the script clone it (set `TOOLS_REPO_URL` if using a private repo):

```bash
# Download just the deploy script first
curl -o /tmp/deploy.sh https://raw.githubusercontent.com/JcbBnd26/swpppautofill_windows/main/web/scripts/deploy.sh
bash /tmp/deploy.sh
```

The script will:
- Create a `tools` system user
- Install system packages (Python, Nginx, certbot, fail2ban, UFW)
- Configure the firewall (ports 22, 80, 443)
- Clone/pull the repo to `/opt/tools/repo/`
- Create a Python venv at `/opt/tools/venv/` and install dependencies
- Install systemd services and Nginx config
- Initialize the database and print the admin invite code
- Set up daily backup and temp-file cleanup crons

### 3. Configure your domain in Nginx

```bash
# Replace the placeholder domain
sed -i 's/tools.example.com/tools.yourdomain.com/g' /etc/nginx/sites-available/tools.conf
nginx -t && systemctl reload nginx
```

### 4. Get an SSL certificate

```bash
certbot --nginx -d tools.yourdomain.com
```

Certbot will update the Nginx config with real certificate paths and set up auto-renewal.

### 5. Claim the admin invite

Open `https://tools.yourdomain.com/auth/login?code=YOUR_CODE` in a browser and create the admin account. The invite code was printed during the deploy step.

### 6. Verify

- Visit `https://tools.yourdomain.com/` — portal should load
- Log in → navigate to SWPPP → fill a form → generate → download ZIP
- Check service status: `systemctl status tools-auth tools-swppp`

## Subsequent Deploys (Updates)

```bash
ssh root@YOUR_VPS_IP
cd /opt/tools/repo
git pull --ff-only

# Re-install deps (only needed if pyproject.toml changed)
/opt/tools/venv/bin/pip install --quiet .
/opt/tools/venv/bin/pip install --quiet fastapi "uvicorn[standard]" gunicorn python-multipart

# Restart services
systemctl restart tools-auth tools-swppp
```

Or simply re-run the deploy script — it's idempotent:

```bash
bash /opt/tools/repo/web/scripts/deploy.sh
```

## File Layout on Server

```
/opt/tools/
├── repo/               # Git clone of the repository
│   ├── app/            # Core SWPPP fill logic
│   ├── web/
│   │   ├── auth/       # Auth FastAPI app
│   │   ├── swppp_api/  # SWPPP FastAPI app
│   │   ├── frontend/   # Static HTML (served by Nginx)
│   │   └── scripts/    # Deploy, backup, configs
│   └── assets/         # PDF template
├── venv/               # Python virtual environment
├── data/               # SQLite databases (auth.db, swppp_sessions.db)
└── backups/            # Daily SQLite backups (30-day retention)

/var/log/tools/         # Gunicorn access + error logs, backup log
/etc/systemd/system/    # tools-auth.service, tools-swppp.service
/etc/nginx/sites-available/tools.conf
/etc/cron.d/tools-backup
/etc/cron.d/tools-tmp-cleanup
```

## Service Management

```bash
# Check status
systemctl status tools-auth
systemctl status tools-swppp

# View logs
journalctl -u tools-auth -f
journalctl -u tools-swppp -f
tail -f /var/log/tools/auth-access.log

# Restart
systemctl restart tools-auth tools-swppp

# Stop
systemctl stop tools-auth tools-swppp
```

## Backups

Backups run daily at 02:00 via cron (`/etc/cron.d/tools-backup`).

- Location: `/opt/tools/backups/`
- Format: `auth_YYYYMMDD.db`, `swppp_sessions_YYYYMMDD.db`
- Retention: 30 days

To manually trigger a backup:

```bash
sudo -u tools /opt/tools/repo/web/scripts/backup.sh
```

To restore from a backup:

```bash
systemctl stop tools-auth tools-swppp
cp /opt/tools/backups/auth_20260409.db /opt/tools/data/auth.db
cp /opt/tools/backups/swppp_sessions_20260409.db /opt/tools/data/swppp_sessions.db
systemctl start tools-auth tools-swppp
```

## SSL Certificate Renewal

Certbot auto-renews via its systemd timer. Verify with:

```bash
certbot renew --dry-run
```

## Troubleshooting

| Symptom | Check |
|---|---|
| 502 Bad Gateway | `systemctl status tools-auth tools-swppp` — are services running? |
| Services won't start | `journalctl -u tools-auth -n 50` — check for import or config errors |
| Database locked | A long-running request may hold the lock — restart the service |
| Can't claim invite | Re-run `init_admin.py` manually (see below) |

To regenerate the admin invite:

```bash
sudo -u tools \
    PYTHONPATH=/opt/tools/repo \
    TOOLS_DATA_DIR=/opt/tools/data \
    TOOLS_DEV_MODE=0 \
    /opt/tools/venv/bin/python /opt/tools/repo/web/scripts/init_admin.py
```
