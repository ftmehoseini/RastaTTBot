# Initial audit of the existing RastaTT bot

Source inspected: <https://github.com/ftmehoseini/RastaTTBot> (`main`, commit `aeb5ce6`).

## What currently exists

The repository root has a `README.md`, `.env.example`, `requirements*.txt`, a systemd unit and a crontab example. A file named `bot` is empty (a single newline). The actual code is only inside `RastaTTBot.zip` (one commit, "Office attendance bot for Soroush Plus"), which also contains a full `.git` directory and `__pycache__` files.

| Area | Current state |
|---|---|
| Platform | Soroush Plus Bot API (`https://api.splus.ir/bot<token>`), Telegram-shaped |
| Framework | None. Hand-written `requests` client (`soroush_api.py`) and a synchronous long-polling loop |
| Language/runtime | Python 3, synchronous, one process with a scheduler thread |
| Database | SQLite (`data/attendance.sqlite3`), schema created at startup with `CREATE TABLE IF NOT EXISTS`, no migrations |
| Tables | `users(chat_id, full_name, created_at, active)`, `sessions(id, chat_id, check_in, check_out, manual_in, manual_out)`, `user_state`, `job_runs` |
| Time storage | Naive ISO strings in Tehran local time |
| Features | Check-in, check-out (night shifts up to `MAX_SHIFT_HOURS`), forgotten entry (month → day → hour → typed minute), weekly/monthly text reports, Excel export, 10:00 missing-checkout reminder, Thursday weekly report |
| Roles | `ADMIN_IDS` comma-separated list in `.env`, checked with `str(chat_id) in admin_ids` |
| Scheduler | Thread polling every 30 s, `job_runs(job, run_date)` prevents re-runs, 3 h catch-up window |
| Deployment | systemd unit + venv under `/opt/rastattbot`, cron backup of the SQLite file |
| Tests | pytest: Jalali helpers, attendance rules, handlers with a fake client, scheduler, API client, Excel |

## Known bugs and risks found

1. **`sendMessage: 400 INVALID INPUT`** (reported by the user). Every keyboard is sent as JSON `reply_markup`. The request shape matches the community Soroush libraries, so the most likely cause is a payload Soroush rejects (the `resize_keyboard` reply keyboard, a message longer than the API limit, or an unknown `chat_id` in `ADMIN_IDS`). The code only logs `method: code description` and never logs which message failed, so the cause cannot be confirmed from logs. The new transport logs the method, chat and payload size (never the text) for every failure.
2. **Naive local timestamps.** Times are stored without a timezone. This works only while the server and `TIMEZONE` agree and breaks any future UTC-based processing.
3. **One process, one thread, SQLite.** Fine for a small office, but there is no way to run more than one worker, and the scheduler would double-send if two copies run, because `job_runs` is only per job and day, not per recipient.
4. **Admins are configuration, not data.** Changing a manager means editing `.env` and restarting. No audit of admin actions.
5. **Handlers contain business logic.** `handlers.py` (≈15 KB) mixes UI, state machine and rules. Adding modules there would grow it into a monolith.
6. **Callback payloads carry state** (`fg:h:out:1405:7:20:9`). Safe today, but the pattern does not scale to longer flows, and stale buttons are only partly handled.
7. **Code is not in Git as files.** It is a zip inside the repository, including a nested `.git` and compiled `__pycache__`. Changes cannot be reviewed or diffed.

## What can be reused safely

- **Business rules for attendance**: no double check-in, no check-out without check-in, night shifts up to a maximum length, forgotten entries cannot be in the future, overlap with another shift, or put check-out before check-in. These rules are carried into `app/application/services/attendance_service.py`.
- **Persian/Jalali helpers** (`jalali.py`): month names, weekday names keyed by `date.weekday()`, Saturday week start, digit normalization. Re-implemented and extended in `app/core/utils/jalali.py` with tests.
- **Scheduler ideas**: once-per-day guarantee and catch-up window after downtime. Kept, but the guarantee is now per recipient in `notification_logs`, so multiple workers cannot double-send.
- **Excel-from-database principle**: Excel is always generated from the database, never used as storage.
- **Existing data**: `sessions` rows can be imported (see below).

## What is refactored

| Old | New |
|---|---|
| `requests` Soroush client | aiogram 3 with a configurable API server (Telegram, Bale, Soroush-compatible) |
| SQLite + startup DDL | PostgreSQL + SQLAlchemy 2 async + Alembic |
| `user_state` table | aiogram FSM in Redis |
| `ADMIN_IDS` in `.env` | `roles` / `user_roles` tables, RBAC permissions, audit log, `BOOTSTRAP_SUPER_ADMIN_IDS` only for the first login |
| Scheduler thread | Celery worker + Celery Beat, per-recipient idempotency |
| One `handlers.py` | Thin routers per module, services, repositories |
| Free sessions per day | One attendance record per user per local work date (check-out may fall after midnight) |

## Data migration risks

- **Multiple sessions in one day.** The old bot allowed several check-in/check-out pairs per day. The new model has one record per user per work date. The importer (`python -m app.cli import-legacy-sqlite`) keeps the earliest check-in and latest check-out of each day and writes a warning to the log and the audit log for every merged day.
- **Open sessions.** Sessions without a check-out are imported as open records. The reminder will ask the employee to complete them.
- **Naive timestamps.** Imported times are interpreted as `Asia/Tehran` local time and converted to UTC. If the old server had a different `TIMEZONE`, pass `--source-timezone`.
- **User identity.** The old `chat_id` is the private chat ID, which equals the user ID in private chats on Telegram-compatible APIs. Users are imported with that ID. If the platform changes (for example Soroush → Bale), IDs will differ and users must re-register; records can be re-linked by an admin.
- **Admins.** Old `ADMIN_IDS` are not imported automatically. Pass them with `--admin-ids` to grant `ADMIN_2_MANAGER`, or assign roles in the bot.

Run the importer on a copy of the SQLite file and review the printed summary before going live.
