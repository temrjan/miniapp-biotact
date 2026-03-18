# biotact-main Server

**Host:** vmi3025384 (95.111.224.251:2222)
**OS:** Ubuntu 24.04 LTS
**Resources:** 11GB RAM, 193GB disk (9% used), 4GB swap
**SSH:** Port 2222, 7 authorized keys

---

## Services

### PM2 (7 processes)

| Name | Namespace | Path | Description |
|------|-----------|------|-------------|
| api | biotact | /var/www/biotact/packages/api | FastAPI 0.124, port 3001 |
| bot | biotact | /var/www/biotact/packages/bot | aiogram 3.23 Telegram bot |
| ref-bot | biotact | /var/www/biotact/packages/ref-bot | Referral bot |
| multicard-backend | bots | /opt/bots/multicard | Multicard payment backend, port 3000 |
| multicard-telegram | bots | /opt/bots/multicard | Multicard Telegram bot |
| csv2txt | bots | /opt/bots/csv2txt | CSV to text converter |
| protocols-bot | bots | /opt/bots/protocols-bot | Protocols document bot |

### Systemd

| Service | Port | Status |
|---------|------|--------|
| PostgreSQL 16 | 5432 (localhost) | enabled |
| Redis | 6379 (localhost) | enabled |
| Nginx | 80, 443 | enabled |
| SSH | 2222 | enabled |

---

## Domains & Nginx

| Domain | Target | SSL |
|--------|--------|-----|
| app.biotact.uz | Frontend SPA + /api/* -> :3001 | Certbot (expires 2026-06-15) |
| admin.biotact.uz | Admin Panel + /api/* -> :3001 | Certbot |
| multicard.biotact.uz | Multicard backend :3000 | Certbot |

Rate limiting configured for /api/v1/admin/auth/ and /api/v1/uploads/.

---

## Databases

| DB | Owner | Used by |
|----|-------|---------|
| biotact_db | biotact | api, bot, ref-bot |
| protocols_bot (SQLite) | - | protocols-bot (/opt/bots/protocols-bot/db/) |

Redis: db0 (68 keys) - sessions, cache.

---

## Project Structure

```
/var/www/biotact/              # Main biotact project (git repo)
+-- packages/
|   +-- api/                   # FastAPI backend (Python 3.12)
|   |   +-- .env               # API secrets
|   +-- bot/                   # Telegram Mini App bot (aiogram)
|   |   +-- .env
|   +-- frontend/              # React 19 Mini App (Vite)
|   |   +-- .env
|   +-- ref-bot/               # Referral bot (aiogram)
+-- admin-panel-v2/            # React 19 admin panel (Vite)
+-- backups/                   # DB backup scripts
|   +-- backup.sh
+-- ecosystem.config.js        # PM2 config (biotact namespace)
+-- CLAUDE.md

/opt/bots/                     # Standalone bots
+-- multicard/                 # Multicard payment integration
|   +-- ecosystem.config.js
+-- csv2txt/                   # CSV converter
+-- protocols-bot/             # Document protocols bot (Python)
    +-- scripts/backup.sh
    +-- db/                    # SQLite database
```

---

## Cron Jobs

| Schedule | Script | Description |
|----------|--------|-------------|
| 03:00 daily | /opt/bots/protocols-bot/scripts/backup.sh | Protocols bot DB backup |
| 03:00 daily | /var/www/biotact/backups/backup.sh | Biotact PostgreSQL backup |

---

## Firewall (UFW)

```
2222/tcp  SSH
80/tcp    HTTP (-> HTTPS redirect)
443/tcp   HTTPS
```

All other ports closed. PostgreSQL and Redis listen on localhost only.

---

## Stack

| Component | Version |
|-----------|---------|
| Node.js | 20.20.1 |
| Python | 3.12.3 |
| PostgreSQL | 16 |
| Redis | latest |
| Nginx | latest |
| PM2 | latest + pm2-logrotate |
| Certbot | auto-renewal |

---

## Quick Commands

```bash
# PM2
pm2 list                    # All services
pm2 logs api --lines 50     # API logs
pm2 restart api             # Restart API
pm2 restart all             # Restart everything

# Deploy biotact
cd /var/www/biotact
git pull origin main
cd packages/api && pip install -r requirements.txt
cd ../frontend && npm ci && npm run build
cd ../../admin-panel-v2 && npm ci && npm run build
pm2 restart api bot ref-bot

# Database
sudo -u postgres psql biotact_db
redis-cli

# Nginx
nginx -t && systemctl reload nginx

# SSL
certbot certificates
certbot renew --dry-run

# Logs
tail -f /var/log/nginx/access.log
pm2 logs --lines 100

# Backups
/var/www/biotact/backups/backup.sh
ls -la /var/www/biotact/backups/

# System
apt update && apt upgrade -y
reboot  # PM2 auto-restarts all services
```

---

Updated: 2026-03-17
