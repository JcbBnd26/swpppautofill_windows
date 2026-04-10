#!/usr/bin/env bash
# Daily SQLite backup for Tools platform.
# Run as the 'tools' user via cron (installed by deploy.sh).
set -euo pipefail

DATA_DIR="/opt/tools/data"
BACKUP_DIR="/opt/tools/backups"
RETAIN_DAYS=30
DATE_STAMP=$(date +%Y%m%d)

mkdir -p "$BACKUP_DIR"

# Back up each database using SQLite's safe .backup command
for db in auth.db swppp_sessions.db; do
    src="$DATA_DIR/$db"
    if [ -f "$src" ]; then
        dest="$BACKUP_DIR/${db%.db}_${DATE_STAMP}.db"
        sqlite3 "$src" ".backup '$dest'"
        echo "Backed up $src → $dest"
    else
        echo "Skipping $db (not found at $src)"
    fi
done

# Prune backups older than retention period
find "$BACKUP_DIR" -name "*.db" -type f -mtime +$RETAIN_DAYS -delete
echo "Pruned backups older than $RETAIN_DAYS days"
