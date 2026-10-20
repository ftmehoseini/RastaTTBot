# Rasta Admin Bot

An administrative automation bot for an organization in Iran, with Persian (Jalali) dates and Persian messages. It currently has three modules:

| Module | What employees do | What admins do |
|---|---|---|
| ⏱ Attendance | Check in and check out with confirmation, enter forgotten entries, see today's status and the weekly report (text or Excel) | Reports for today, the week, the month, any date range or one employee, as a summary plus a detailed Excel file |
| 📝 Leave | Request paid or unpaid leave, by the hour or by the day, and follow its status | Two-step approval (secretary, then manager) with a required rejection reason, an inbox, and Excel export |
| 🍽 Food | View the weekly menu, order a meal, order for guests, see a weekly invoice with payment details | Upload the menu from Excel (with preview and versioning), notify everyone, daily summaries, order and invoice Excel files |

The bot also has central role-based access control (RBAC) with an audit log, reminders at 10:00 and 20:00 that never repeat, and an importer for data from the old RastaTT SQLite bot.

## Technology

Python 3.12 · aiogram 3 (Telegram, Bale and Soroush-compatible APIs) · PostgreSQL 16 + SQLAlchemy 2 async + Alembic · Redis (FSM state, locks, Celery broker) · Celery worker + Beat · openpyxl · jdatetime · Pydantic Settings + YAML policy · structlog · pytest · Ruff · Docker Compose.

Architecture: a modular monolith with domain, application, infrastructure and presentation layers. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick start (Docker)

```bash
cp .env.example .env          # set BOT_TOKEN, BOT_PLATFORM, POSTGRES_PASSWORD, BOOTSTRAP_SUPER_ADMIN_IDS
nano config/default.yaml      # organization policy (or create config/production.yaml with overrides)
docker compose up -d --build  # postgres, redis, migrate, bot, worker, beat
docker compose logs -f bot
curl http://127.0.0.1:8080/health
```

The `migrate` service runs `alembic upgrade head` before the bot and worker start.

## Local development (without Docker)

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env                       # point DATABASE_URL / REDIS_URL at local services
.venv/bin/alembic upgrade head             # create the schema
.venv/bin/python -m app.presentation.bot.main            # bot + health endpoint
.venv/bin/celery -A app.infrastructure.tasks.celery_app worker --loglevel=INFO
.venv/bin/celery -A app.infrastructure.tasks.celery_app beat --loglevel=INFO
```

Set `LOG_FORMAT=console` for readable local logs.

## Environment variables

Every variable is listed with a comment in [.env.example](.env.example) and documented in [docs/CONFIGURATION.md](docs/CONFIGURATION.md). The ones you must set:

| Variable | Meaning |
|---|---|
| `BOT_TOKEN` | Bot token from BotFather (Telegram) or the Bale/Soroush bot builder |
| `BOT_PLATFORM` | `telegram`, `bale` or `soroush` |
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@host:5432/db` (set automatically by docker compose) |
| `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | Redis databases 0/1/2 |
| `BOOTSTRAP_SUPER_ADMIN_IDS` | Your numeric user ID; you become `SUPER_ADMIN` when you first press /start |

## Telegram, Bale or Soroush

The bot uses the Telegram Bot API through aiogram. Bale and Soroush Plus expose the same API at a different address, so switching platform is configuration only:

```dotenv
BOT_PLATFORM=bale            # https://tapi.bale.ai
# BOT_PLATFORM=soroush       # https://api.splus.ir
# BOT_API_BASE_URL=https://your-proxy.example   # optional explicit override
```

Messages are sent as plain text, without `parse_mode`. Long messages are split below the platform limit. Delivery failures are logged with the method, the chat and the payload size. Platform user IDs differ between Telegram, Bale and Soroush, so users register again after a switch.

## Database migrations

```bash
alembic upgrade head                                   # apply
alembic revision --autogenerate -m "add something"     # after changing models
alembic downgrade -1                                   # roll back one step
docker compose run --rm migrate                        # in Docker
```

The initial migration also seeds the four roles.

## Workers and scheduled jobs

| Process | Command | Job |
|---|---|---|
| `worker` | `celery -A app.infrastructure.tasks.celery_app worker` | Runs reminders, menu broadcasts and file cleanup |
| `beat` | `celery -A app.infrastructure.tasks.celery_app beat` | Triggers reminder tasks every 10 minutes and cleanup hourly. Run **exactly one** beat |

The reminder tasks check the configured time themselves and catch up for `reminder_catchup_minutes` after any downtime. Every recipient is claimed once per day in `notification_logs`, so restarts and multiple workers never send a duplicate.

## Tests and code quality

```bash
.venv/bin/pytest                                   # SQLite in memory
TEST_DATABASE_URL=postgresql+asyncpg://u:p@localhost:5432/rasta_bot_test .venv/bin/pytest   # real PostgreSQL
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

There are 93 tests: Jalali boundaries, calculation formulas, duplicate prevention, corrections, reminder selection and idempotency, leave state machine, menu Excel validation, food cutoff, guests and invoices, RBAC, and end-to-end bot dialogs through the real aiogram dispatcher.

## Creating the first super admin

Use either method:

1. Put your numeric ID in `BOOTSTRAP_SUPER_ADMIN_IDS` and send `/start` to the bot. Send `/myid` to any bot to see your ID.
2. Or run `docker compose run --rm bot python -m app.cli create-super-admin --user-id 123456789 --name "نام مدیر"`.

Then open **🛠 پنل مدیریت → 👥 کاربران و نقش‌ها** and assign `ADMIN_1_SECRETARY`, `ADMIN_2_MANAGER` and other roles. `/user <id>` opens a user directly.

## Moving from the old RastaTT bot

```bash
python -m app.cli import-legacy-sqlite /path/to/attendance.sqlite3 --dry-run
python -m app.cli import-legacy-sqlite /path/to/attendance.sqlite3 --admin-ids 111,222
```

See [docs/INITIAL_AUDIT.md](docs/INITIAL_AUDIT.md) for what is merged and why.

## Deploying on an Ubuntu VPS

Short version (full guide, backups and launch checklist in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)):

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
sudo git clone <this repository> /opt/rasta-bot && cd /opt/rasta-bot
sudo cp .env.example .env && sudo nano .env && sudo chmod 600 .env
sudo docker compose up -d --build
sudo crontab -e   # add: 30 23 * * * /opt/rasta-bot/docker/backup_postgres.sh >> /opt/rasta-bot/backups/backup.log 2>&1
```

## Documentation

| Document | Contents |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layers, data flows, extension points |
| [DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) | How to add a module, button, permission or workflow |
| [WORKFLOWS.md](docs/WORKFLOWS.md) | Every user flow and state machine |
| [CONFIGURATION.md](docs/CONFIGURATION.md) | Every environment variable and policy option |
| [EXCEL_TEMPLATES.md](docs/EXCEL_TEMPLATES.md) | Menu template and report columns |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | VPS setup, backups, retention, launch checklist |
| [ASSUMPTIONS.md](docs/ASSUMPTIONS.md) | Decisions to confirm |
| [INITIAL_AUDIT.md](docs/INITIAL_AUDIT.md) | Review of the original bot |
