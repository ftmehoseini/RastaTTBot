# Deployment (Ubuntu VPS)

Tested layout: one Ubuntu 22.04/24.04 server running everything with Docker Compose: PostgreSQL, Redis, a one-shot migration, the bot (long polling + health endpoint), a Celery worker and Celery Beat. No inbound ports are needed. The health endpoint is bound to 127.0.0.1.

## 1. Server preparation

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y docker.io docker-compose-v2 git ufw
sudo systemctl enable --now docker
sudo ufw allow OpenSSH && sudo ufw enable
sudo timedatectl set-timezone UTC          # the app converts to Asia/Tehran itself
```

If Docker Hub or PyPI is blocked from the server, configure a registry mirror in `/etc/docker/daemon.json` and pass `PIP_INDEX_URL=<mirror>/simple` in `.env`, which the build reads. For Telegram from inside Iran, set `BOT_API_BASE_URL` to a reachable proxy. Bale and Soroush work directly.

## 2. Install

```bash
sudo git clone <repository-url> /opt/rasta-bot
cd /opt/rasta-bot
sudo cp .env.example .env
sudo nano .env                 # BOT_TOKEN, BOT_PLATFORM, POSTGRES_PASSWORD (strong), BOOTSTRAP_SUPER_ADMIN_IDS, APP_ENV=production
sudo chmod 600 .env
sudo nano config/production.yaml   # your overrides: payment.shaba_number, holidays, schedule… (git-ignored)
sudo docker compose up -d --build
sudo docker compose ps         # migrate = exited (0); bot, worker, beat, postgres, redis = running/healthy
curl -s http://127.0.0.1:8080/health
sudo docker compose logs -f bot worker beat
```

Send `/start` to the bot from the account listed in `BOOTSTRAP_SUPER_ADMIN_IDS`, then assign roles in **🛠 پنل مدیریت → 👥 کاربران و نقش‌ها**.

## 3. Updating

```bash
cd /opt/rasta-bot
sudo docker/backup_postgres.sh          # always back up first
sudo git pull
sudo docker compose up -d --build       # migrate runs before bot/worker start
sudo docker compose logs --tail=100 bot
```

Rollback: `git checkout <previous tag>`, `docker compose run --rm migrate alembic downgrade <revision>` if the schema changed, then `docker compose up -d --build`.

## 4. Backups

| What | How | Retention |
|---|---|---|
| PostgreSQL (all business data) | `docker/backup_postgres.sh`: `pg_dump` → `backups/<db>-<UTC time>.sql.gz`, run nightly from cron | `KEEP_DAYS` (default 30) |
| Off-server copy | `rsync`/`rclone` the `backups/` directory to another machine or object storage daily | per organization policy |
| Uploaded menu files | only kept if `food.retain_menu_files: true` (in the `appdata` volume, `/app/data/uploads/menus`). The menu contents are always in the database | with the volume |
| Generated reports | temporary. Deleted after sending and swept hourly after `reports.retention_hours` | 24 h |
| Redis | FSM state and queues only; can be lost safely (users restart a half-finished flow) | none needed |

```bash
sudo mkdir -p /opt/rasta-bot/backups
sudo crontab -e
# 30 23 * * * /opt/rasta-bot/docker/backup_postgres.sh >> /opt/rasta-bot/backups/backup.log 2>&1
```

Restore drill (do it once before launch):

```bash
sudo docker compose stop bot worker beat
sudo docker/restore_postgres.sh backups/rasta_bot-YYYYMMDD-HHMMSS.sql.gz
sudo docker compose up -d
```

Restore into an empty database (drop and recreate it first, or restore on a fresh server).

## 5. Monitoring

- `GET http://127.0.0.1:8080/health` → 200 when the database and Redis respond. Point an uptime checker or a cron `curl --fail` at it.
- Docker healthchecks: bot (HTTP), worker (`celery inspect ping`), postgres, redis. `restart: unless-stopped` restarts crashed services.
- Logs are JSON on stdout: `docker compose logs --since 1h bot | grep '"level": "error"'`. Configure Docker log rotation in `/etc/docker/daemon.json` (`"log-opts": {"max-size": "20m", "max-file": "5"}`).
- `notification_logs` with status `FAILED` show undelivered reminders (usually users who blocked the bot).

## 6. Moving data from the old RastaTT bot

```bash
sudo cp /opt/rastattbot/data/attendance.sqlite3 /opt/rasta-bot/backups/legacy.sqlite3
sudo docker compose run --rm -v /opt/rasta-bot/backups:/import bot \
  python -m app.cli import-legacy-sqlite /import/legacy.sqlite3 --dry-run
sudo docker compose run --rm -v /opt/rasta-bot/backups:/import bot \
  python -m app.cli import-legacy-sqlite /import/legacy.sqlite3 --admin-ids 111111,222222
sudo systemctl disable --now rastattbot   # stop the old bot so two bots don't poll the same token
```

## 7. Production launch checklist

- [ ] `.env`: real `BOT_TOKEN`, correct `BOT_PLATFORM`, strong `POSTGRES_PASSWORD`, `APP_ENV=production`, `chmod 600`
- [ ] Only one process polls the token (old bot stopped; exactly one `bot` and one `beat` container)
- [ ] `config/production.yaml`: workdays, start/end times, grace minutes, reminder times, food deadline, holidays for the year, payment details
- [ ] `python -m app.cli check-config` output reviewed
- [ ] Staff list uploaded (admin panel → «📋 فهرست کارکنان») before people are told to register
- [ ] First SUPER_ADMIN logged in; ADMIN_1 (secretary) and ADMIN_2 (manager) roles assigned; each tried an approval
- [ ] Test employee: check-in, check-out, forgotten entry, weekly report, leave request through both approvals, food order before and after the deadline, guest order
- [ ] Menu template downloaded, filled and uploaded; broadcast sent to a test group first
- [ ] Reminders observed at the configured times on a workday (check `notification_logs`)
- [ ] Backup cron installed, first backup file exists, restore drill done, off-server copy configured
- [ ] Health check monitored; Docker log rotation configured
- [ ] Assumptions in `docs/ASSUMPTIONS.md` confirmed with the organization
