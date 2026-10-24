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
| `holidays` | `[]` | Jalali dates (`1405/01/01`) treated as non-workdays: no reminders, no required minutes |

### users

| Key | Default | Meaning |
|---|---|---|
| `require_admin_activation` | `false` | New registrations stay inactive until a SUPER_ADMIN activates them |
| `min_full_name_length` / `max_full_name_length` | `3` / `80` | Name validation at registration |

### attendance

| Key | Default | Meaning |
|---|---|---|
| `workdays` | sat … thu | Days with required work and reminders |
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
| `max_shift_hours` | `16` | Longest allowed shift; a check-out after midnight belongs to the previous day within this limit |
| `allow_checkout_without_checkin` | `false` | Allow check-out on a day without check-in |
| `live_confirmation_ttl_seconds` | `300` | How long a check-in/out confirm button stays valid |
| `allow_same_day_manual_correction` | `false` | Forgotten entries for today allowed. Off: today's time is entered with «ثبت ورود/خروج امروز → دستی» and marked ✍️ instead of 📝 |
| `allow_future_manual_correction` | `false` | Forgotten entries for future times allowed |
| `max_manual_correction_age_days` | `30` | Oldest day a forgotten entry can be submitted for |
| `manual_correction_requires_approval` | `false` | Store corrections as PENDING_APPROVAL instead of applying them |

#### Calculation policy `floating_window` (organization default)

```
floating_late_minutes  = min(check_in, floating_check_in_until) - standard_start_time   (تأخیر مجاز, not deducted)
late_minutes           = max(0, check_in - floating_check_in_until - allowed_late_arrival_minutes)   (تأخیر غیرمجاز)
early_departure_minutes= max(0, standard_end_time - check_out - allowed_early_departure_minutes)    (تعجیل)
gross_worked_minutes   = check_out - check_in
raw = net balance      = gross_worked_minutes - standard_workday_minutes
overtime_minutes       = round_down(max(0, raw), overtime_rounding_minutes)              (اضافه‌کار)
deficit_minutes        = max(0, -raw)
```

Example with 08:00–16:48 and a 09:30 window: in 09:00, out 17:48 → allowed lateness 1:00, no early departure,
no overtime (worked exactly 8:48). In 09:45, out 16:30 → allowed 1:30, unauthorized 0:15, early 0:18.
A report that covers one whole Jalali month also shows the monthly caps, what is left and any overrun.

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
