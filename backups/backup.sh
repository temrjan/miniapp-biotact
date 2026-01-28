#!/bin/bash
# PostgreSQL backup script for biotact_db
# Keeps last 14 daily backups

BACKUP_DIR="/var/www/biotact/backups"
DB_NAME="biotact_db"
DB_USER="biotact"
DATE=$(date +%Y-%m-%d_%H%M)
KEEP_DAYS=14

# Create backup
PGPASSWORD="biotact_secure_2024" pg_dump -U "$DB_USER" -h localhost "$DB_NAME" | gzip > "$BACKUP_DIR/${DB_NAME}_${DATE}.sql.gz"

# Check if backup was created successfully
if [ $? -eq 0 ]; then
    echo "[$(date)] Backup created: ${DB_NAME}_${DATE}.sql.gz ($(du -h "$BACKUP_DIR/${DB_NAME}_${DATE}.sql.gz" | cut -f1))"
else
    echo "[$(date)] ERROR: Backup failed!"
    exit 1
fi

# Remove backups older than KEEP_DAYS
find "$BACKUP_DIR" -name "${DB_NAME}_*.sql.gz" -mtime +${KEEP_DAYS} -delete
echo "[$(date)] Old backups cleaned (kept last ${KEEP_DAYS} days)"
