# Configuration

There are two sources of configuration:

| Source | Holds | Loaded by |
|---|---|---|
| `.env` (from `.env.example`) | secrets and infrastructure: tokens, database, Redis, paths | `app/core/config/settings.py` (Pydantic Settings) |
| `config/default.yaml` | business policy: schedules, deadlines, limits, payment text | `app/core/config/policy.py` (typed, validated at startup) |

Policy layering: `config/default.yaml` → `config/<APP_ENV>.yaml` (if it exists) → the file in `POLICY_FILE` (if set). Later files only need the keys they change. `config/production.yaml` and `config/local.yaml` are git-ignored so real organization data stays off GitHub. Invalid values, such as an unknown weekday, a malformed time, or an end time before the start time, stop the process at startup with a clear error.

Restart the bot, worker **and** beat after changing either file. In Docker: `docker compose restart bot worker beat`.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `APP_ENV` | `development` | Selects `config/<APP_ENV>.yaml` |
| `LOG_LEVEL` | `INFO` | Python log level |
| `LOG_FORMAT` | `json` | `json` for servers, `console` for local reading |
| `BOT_TOKEN` | — | **Required.** Bot token. Never logged (redacted) |
| `BOT_PLATFORM` | `telegram` | `telegram`, `bale` or `soroush` |
| `BOT_API_BASE_URL` | empty | Override the API base (e.g. a proxy). Empty = platform default |
| `BOOTSTRAP_SUPER_ADMIN_IDS` | empty | Comma-separated user IDs that become SUPER_ADMIN at /start |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | — | Used by docker compose to create the database |
| `DATABASE_URL` | local URL | `postgresql+asyncpg://…`. Compose builds it from the three above |
| `REDIS_URL` | `redis://localhost:6379/0` | FSM state, locks, health. Empty = in-memory FSM (development only) |
| `CELERY_BROKER_URL` | `redis://localhost:6379/1` | Celery broker |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/2` | Celery results (kept 1 hour) |
| `REPORTS_DIR` | `reports` | Temporary generated files |
| `UPLOAD_DIR` | `uploads` | Temporary uploaded menus (retained menus go to `UPLOAD_DIR/menus/`) |
| `MAX_UPLOAD_BYTES` | `2097152` | Maximum menu file size |
| `HEALTH_HOST` / `HEALTH_PORT` | `0.0.0.0` / `8080` | HTTP health endpoint of the bot process |
| `POLICY_FILE` | empty | Extra YAML merged over the policy |
| `PIP_INDEX_URL` | pypi.org | Docker build argument for a PyPI mirror |

## Policy options (`config/default.yaml`)

### organization

| Key | Default | Meaning |
|---|---|---|
| `name` | `RastaTT` | Organization name (informational) |
| `timezone` | `Asia/Tehran` | Timezone for every business date and message. Storage is always UTC |
| `locale` | `fa_IR` | Informational; all user messages are Persian |
| `official_holidays` | `true` | Iran's official holidays are days off for everyone (list: `app/domain/attendance/iran_holidays.py`, 1404–1407, generated from the official calendar; lunar dates not yet confirmed are marked «تخمینی». Regenerate yearly with `scripts/generate_iran_holidays.py`) |
| `holidays` | `[]` | Extra days off (Jalali dates `1405/01/01`), e.g. a day the organization closes |
| `holiday_exceptions` | `[]` | Official holidays the organization works anyway, or an estimated date that turned out wrong |

### users

| Key | Default | Meaning |
|---|---|---|
| `require_admin_activation` | `false` | New registrations stay inactive until a SUPER_ADMIN activates them |
| `require_employee_roster` | `true` | Only numbers on the staff list (admin panel → «📋 فهرست کارکنان») can register. IDs in `BOOTSTRAP_SUPER_ADMIN_IDS` are exempt |
| `max_roster_attempts` / `roster_lock_minutes` | `5` / `60` | Wrong numbers allowed before registration is locked for that person, and for how long |
| `min_full_name_length` / `max_full_name_length` | `3` / `80` | Name validation at registration |

### attendance

| Key | Default | Meaning |
|---|---|---|
| `workdays` | sat … wed | Days with required work and check-in reminders. A person can have their own (admin panel → user card → «📅 روزهای کاری», stored in `users.workdays`) |
| `standard_workday_minutes` | `528` | Required minutes on a workday |
| `standard_start_time` / `standard_end_time` | `08:00` / `16:48` | Used for lateness and early departure |
| `check_in_reminder_time` | `10:00` | Reminder to employees without a check-in |
| `check_out_reminder_time` | `20:00` | Reminder to employees checked in but not out |
| `reminder_catchup_minutes` | `180` | A reminder still goes out if the scheduler was down at the exact time, within this window |
| `allowed_late_arrival_minutes` | `0` | Grace before lateness counts |
| `allowed_early_departure_minutes` | `0` | Grace before early departure counts |
| `overtime_rounding_minutes` | `0` | Overtime is rounded **down** to this step (0 = no rounding) |
| `calculation_policy` | `floating_window` | Name of the calculation strategy (below) |
| `floating_check_in_until` | `09:30` | `floating_window` only: check-in until this time is allowed lateness (تأخیر مجاز) |
| `monthly_unauthorized_late_cap_minutes` | `420` | Monthly cap on unauthorized lateness, shown in month reports (0 = no cap) |
| `monthly_early_departure_cap_minutes` | `180` | Monthly cap on early departure (تعجیل), shown in month reports (0 = no cap) |
| `final_work_surplus_factor` / `final_work_penalty_factor` | `0.8` / `1.6` | کارکرد نهایی: + overtime surplus × the first − uncompensated lateness × the second (0 = leave the term out) |
| `max_shift_hours` | `16` | Longest allowed shift; a check-out after midnight belongs to the previous day within this limit |
| `allow_checkout_without_checkin` | `false` | Allow check-out on a day without check-in |
| `live_confirmation_ttl_seconds` | `300` | How long a check-in/out confirm button stays valid |
| `allow_same_day_manual_correction` | `false` | Forgotten entries for today allowed. Off: today's time is entered with «ثبت ورود/خروج امروز → دستی» and marked ✍️ instead of 📝 |
| `allow_future_manual_correction` | `false` | Forgotten entries for future times allowed |
| `max_manual_correction_age_days` | `30` | Oldest day a forgotten entry can be submitted for |
| `manual_correction_requires_approval` | `false` | Store corrections as PENDING_APPROVAL instead of applying them |

#### Calculation policy `floating_window` (organization default)

The organization's Excel timesheet, formula for formula (`app/domain/attendance/timesheet.py`; the
admin's monthly Excel carries the same formulas, see EXCEL_TEMPLATES.md). With E = 08:00
(`standard_start_time`), F = 09:30 (`floating_check_in_until`), G = 16:48 (`standard_end_time`),
D = check-in, H = check-out:

```
J ساعت مفید          = H − D
K کارکرد عادی        = presence inside E..G: from D (from E when D is earlier), until H (until G when H is later)
L تاخیر مجاز          = D − E when E ≤ D ≤ F;  F − E when D > F
M تاخیر غیر مجاز      = D − F when D > F
N تعجیل               = G − H when H < G
O اضافه کار           = MAX(J − K, 0): presence before E and after G
deficit (کسری)        = required − K: the part of the official window the person was not there
```

Month (the summary block of the workbook):

```
قابل جبران            = ΣL + MIN(ΣM, 7 h) + MIN(ΣN, 3 h)          (caps: monthly_*_cap_minutes)
غیرقابل جبران          = MAX(ΣM − 7 h, 0) + MAX(ΣN − 3 h, 0)
اضافه کار مازاد        = MAX(ΣO − قابل جبران, 0)
جبران نشده            = MAX(قابل جبران − ΣO, 0) + غیرقابل جبران
کارکرد نهایی           = ΣK + MIN(ΣO, قابل جبران) + مرخصی با حقوق + مازاد × 0.8 − جبران نشده × 1.6
```

Example: in 11:34, out 19:00 → K 5:14, L 1:30, M 2:04, O 2:12.

**Days off** (holidays for everyone, and weekdays outside the person's workdays — Thursday and
Friday by default): all presence is overtime. The formulas stay the same; E = F = G = the check-in
time makes them give K = L = M = N = 0 and O = J. The Excel export writes exactly those inputs.

#### Calculation policy `standard_deduction`

```
gross_worked_minutes   = check_out - check_in
late_minutes           = max(0, check_in - standard_start_time - allowed_late_arrival_minutes)
early_departure_minutes= max(0, standard_end_time - check_out - allowed_early_departure_minutes)
raw_work_balance       = gross_worked_minutes - standard_workday_minutes
deficit_minutes        = max(0, -raw_work_balance)
overtime_minutes       = round_down(max(0, raw_work_balance - late - early), overtime_rounding_minutes)
net_balance_minutes    = raw_work_balance - late - early
```

On non-workdays and holidays, required minutes, lateness and early departure are 0, so all worked time is overtime. A workday with no record is `ABSENT` (deficit = required minutes). A day with only a check-in is `MISSING_CHECK_OUT` and has no balance until it is completed. To use a different policy, see "Change the attendance formula" in [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md).

### leave

| Key | Default | Meaning |
|---|---|---|
| `require_rejection_reason` | `true` | Admins must write a reason to reject |
| `min_rejection_reason_length` | `3` | Minimum reason length |
| `allow_overlapping_leave_requests` | `false` | Allow requests overlapping pending/approved ones |
| `max_past_days` / `max_future_days` | `7` / `90` | Allowed date window for new requests |
| `max_daily_leave_days` | `30` | Longest daily leave in one request |
| `count_non_workdays_in_daily_leave` | `true` | Count Fridays/holidays inside a daily leave |
| `max_reason_length` | `500` | Longest description/reason stored |

### food

| Key | Default | Meaning |
|---|---|---|
| `ordering_deadline_time` | `10:00` | Orders and changes for a day close at this time on that day |
| `guest_ordering_deadline_time` | empty | Separate guest deadline; empty = same as above |
| `currency_label` | `تومان` | Shown next to prices |
| `allow_guest_orders` | `true` | Enable guest orders |
| `max_guest_count_per_order` | `10` | Maximum guests per dish per day |
| `max_items_per_employee_order` | `1` | Meals per person per day. With 1, a new choice replaces the old one |
| `allow_order_edit_before_deadline` | `true` | Allow changing/cancelling before the deadline |
| `max_price_toman` | `100000000` | Upper bound for menu prices (catches typos) |
| `retain_menu_files` | `false` | Keep uploaded menu files under `UPLOAD_DIR/menus/` |

### payment

| Key | Meaning |
|---|---|
| `shaba_number` | Sheba number shown on employee invoices. Keep it in `config/production.yaml`, not in Git |
| `account_holder_name` | Account holder shown on invoices |
| `payment_message` | Free text under the payment details |

### notifications

| Key | Default | Meaning |
|---|---|---|
| `batch_size` | `20` | Messages per batch in bulk sends |
| `batch_interval_seconds` | `1.5` | Pause between batches |
| `max_send_retries` | `3` | Retries for temporary API failures, and how many times a failed reminder is retried |

### reports / rate_limit

| Key | Default | Meaning |
|---|---|---|
| `reports.retention_hours` | `24` | Temporary report/upload files older than this are deleted hourly |
| `rate_limit.min_interval_seconds` | `0.4` | Messages from one user faster than this are dropped (flood protection) |
