# RastaTT Bot — Office Attendance on Soroush Plus

A Soroush Plus bot for office check-in and check-out, with a Persian (Jalali) calendar.

| Button | What it does |
|---|---|
| ✅ ورود | Records check-in now |
| 🏁 خروج | Records check-out now and shows the time worked |
| 📝 ثبت ساعت فراموش‌شده | Adds a forgotten check-in or check-out: Month → Day → Hour (buttons) → Minute (typed) → Confirm |
| 📊 گزارش | Week / month / previous month report, and an Excel file. Managers also get whole-team reports |

Automatic messages:
- **Reminder at 10:00 on workdays (Saturday–Thursday):** anyone whose check-out from an earlier day is missing gets a message with a button that jumps straight to the hour picker for that day.
- **Weekly report on Thursday at 14:00:** each person gets their own week, and managers get the team summary plus the Excel file.

All times are configurable in `.env`.

## How it works

```
bot/
  main.py          # entry point: long-polls Soroush, starts the scheduler
  handlers.py      # buttons, registration, forgotten-entry flow, report menu
  attendance.py    # check-in/check-out rules and validation
  db.py            # SQLite storage (the source of truth)
  reports.py       # text reports
  excel_export.py  # .xlsx files, built from the database on demand
  scheduler.py     # 10:00 reminder + weekly report
  jalali.py        # Persian calendar and formatting
  soroush_api.py   # thin Soroush Plus Bot API client
  config.py        # settings from .env
scripts/
  check_api.py     # smoke test against the real Soroush API
  backup_db.py     # daily database backup
deploy/
  rastattbot.service, crontab.example
```

Records live in SQLite (`data/attendance.sqlite3`). Excel files are generated from it, so someone opening a file in Excel can never lock or corrupt the data.

### Rules the bot enforces

- **No double check-in.** A second check-in while one is open is refused.
- **No check-out without a check-in.** The bot points the person to the forgotten-entry button instead.
- **Night shifts work.** A shift may cross midnight, up to `MAX_SHIFT_HOURS` (default 16).
- **An open shift from an earlier day blocks new check-ins** until its check-out is recorded. The bot offers a one-tap button to record it.
- **Forgotten entries are validated.** They can't be in the future, overlap another shift, put a check-out before its check-in, or run past the next shift.
- **Late button presses can be corrected.** If someone pressed check-in at 10:00 but arrived at 08:15, a forgotten check-in of 08:15 on the same day fixes it.
- **Manual entries are marked** with ✍️ in reports and "بله" in Excel.
- **Persian digits work.** Minutes can be typed as `۴۵` or `45`.

## Install on the server

```bash
sudo useradd --system --create-home --home-dir /opt/rastattbot rastattbot
sudo -u rastattbot git clone https://github.com/ftmehoseini/RastaTTBot.git /opt/rastattbot
cd /opt/rastattbot
sudo -u rastattbot python3 -m venv .venv
sudo -u rastattbot .venv/bin/pip install -r requirements.txt
sudo -u rastattbot cp .env.example .env
sudo -u rastattbot nano .env            # set SOROUSH_BOT_TOKEN (and later ADMIN_IDS)
sudo chmod 600 .env
```

If `pip install` is blocked from inside Iran, point pip at a local PyPI mirror.

### 1. Smoke-test the API

```bash
sudo -u rastattbot .venv/bin/python -m scripts.check_api
```

Send the bot a message, then press one of the buttons it sends. All 5 checks must pass.

### 2. Run it as a service

```bash
sudo cp deploy/rastattbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rastattbot
sudo journalctl -u rastattbot -f        # live logs
```

### 3. Set the managers

Each manager sends `/myid` to the bot. Put the IDs in `.env`:

```
ADMIN_IDS=123456,789012
```

Then run `sudo systemctl restart rastattbot`.

### 4. Daily backups

```bash
sudo -u rastattbot crontab -e     # add the line from deploy/crontab.example
```

Backups go to `data/backups/`, and the newest 30 are kept.

## Settings (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `SOROUSH_BOT_TOKEN` | — | Bot token (required) |
| `ADMIN_IDS` | empty | Manager chat IDs, comma-separated |
| `TIMEZONE` | `Asia/Tehran` | Office timezone |
| `DB_PATH` | `data/attendance.sqlite3` | Database file |
| `WORKDAYS` | `sat,sun,mon,tue,wed,thu` | Days the reminder runs |
| `REMINDER_TIME` | `10:00` | Missing check-out reminder |
| `WEEKLY_REPORT_DAY` / `_TIME` | `thu` / `14:00` | Automatic weekly report |
| `MAX_SHIFT_HOURS` | `16` | Longer shifts are rejected as mistakes |

If the bot is down at a scheduled time, the job still runs when it comes back, as long as that's within 3 hours. Each job runs at most once a day, even across restarts.

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

## Not handled yet

- **Official holidays.** Iranian public holidays aren't known to the bot, so the reminder still runs on them.
- **Editing or deleting a wrong record.** Fix it in the database for now.
