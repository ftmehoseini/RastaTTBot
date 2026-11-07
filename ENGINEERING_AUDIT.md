# Engineering Audit: Rasta Admin Bot

| | |
|---|---|
| Date | 2026-11-07 |
| Commit audited | `1ad359d` (branch `claude/zen-cori-7y0vhd`, same as `main` at audit time) |
| Scope | Whole repository: code, migrations, tests, Docker/deploy scripts, docs |
| Status | **Proposal. No code was changed.** Waiting for approval before any fixes or refactoring. |

## 0. Summary

The codebase is in **good shape for its size** (about 15k lines of Python). The layers are clean: domain → application services → infrastructure → aiogram presentation. RBAC is checked in the services, not only in the UI. Concurrency is handled with row locks, unique constraints and savepoints. Reminders are idempotent. Logs redact secrets. Tests are meaningful and include end-to-end dialogs through the real dispatcher.

Verified during the audit:

| Check | Result |
|---|---|
| `pytest` on SQLite | **146 passed** (24 s) |
| `pytest` on real PostgreSQL 16 (`TEST_DATABASE_URL`) | **146 passed** |
| `alembic upgrade head` → `downgrade base` → `upgrade head` on PostgreSQL 16 | Clean |
| `alembic check` (ORM models vs migrations) | **No drift** |
| `ruff check` / `ruff format --check` | Clean |
| `pip-audit -r requirements.txt` (resolved 2026-11-07) | **No known vulnerabilities** |
| Line coverage (`pytest-cov`) | **83 %** overall (gaps listed in §5) |

The main risks are **not code-quality problems**. They are:

1. **Attendance integrity.** Employees can set their own check-in and check-out times (manual time for today, forgotten entries, edits), and these apply with no approval. The "requires approval" switch exists, but nothing can approve a pending correction.
2. **Two confirmed registration bugs.** `/cancel` doesn't work during registration, and «لغو» is accepted as a person's name.
3. **Operations.** There is no CI. Builds are not reproducible (no lock file). A deploy carries on even if the backup fails. Off-site backup copies and restore checks are documented, but they are manual steps.
4. **The holiday calendar.** Lunar holidays are only estimates (marked «تخمینی») after 1405/10, which is about two months from today.

---

## 1. Repository map

### 1.1 Stack

Python 3.12 · aiogram 3 (Telegram Bot API; Bale and Soroush through a different base URL) · PostgreSQL 16 + SQLAlchemy 2 async (resolves to **2.1.8**) + Alembic · Redis (FSM storage, Celery broker and results, job locks) · Celery worker + Beat · openpyxl · jdatetime · pydantic-settings + YAML policy · structlog · pytest + pytest-asyncio (+ aiosqlite) · Ruff · Docker Compose.

### 1.2 Layout

```
app/
  core/            config (Settings from env, Policy from YAML), DB engine/base, logging, RBAC, utils (Jalali, clock, text)
  domain/          SQLAlchemy models + pure rules: attendance calculation & timesheet, approval engine, holidays
  application/     services (attendance, leave, food, users, notifications, approvals), DTOs, presenters (Persian text), use cases (reminders, menu broadcast)
  infrastructure/  repositories, Excel (reports, timesheet, menu parser, roster), messenger adapter (aiogram), Celery tasks
  presentation/    bot: dispatcher, middlewares, routers/handlers, keyboards, callback data, FSM states; api: /health
  cli.py           create-super-admin, import-legacy-sqlite, check-api, check-config
  tests/           13 test modules
alembic/           4 migrations (0001 initial … 0004 user workdays)
config/default.yaml  organization policy
docker/            deploy.sh, quick.sh, backup/restore scripts
docs/              8 documents (architecture, workflows, config, deployment, ...)
```

**Leftovers from the legacy bot (not used by the current app):** `RastaTTBot.zip` (94 KB, the old repo with its `.git`; I checked it and found no secrets), `bot` (a 1-byte file), `rastattbot.service` and `crontab.example` (both point at `bot.main` / `scripts.backup_db`, which no longer exist).

### 1.3 Processes and entry points

| Process | Entry | Notes |
|---|---|---|
| `bot` | `python -m app.presentation.bot.main` | Long polling plus aiohttp `/health` on :8080 (published only on host 127.0.0.1) |
| `worker` | `celery -A app.infrastructure.tasks.celery_app worker` | Each task runs `asyncio.run` with a fresh `NullPool` engine and Bot session |
| `beat` | `celery … beat` | Reminders every 10 min (each task decides if it is due), file cleanup hourly |
| `migrate` | `alembic upgrade head` | Must finish successfully before bot and worker start |

### 1.4 Main execution paths

**Every bot update.** `UserLockMiddleware` runs one update at a time per user and drops an identical message repeated within 0.4 s. Then `ContextMiddleware` opens one `AsyncSession`, builds the `Services` container, loads `User`, grants bootstrap SUPER_ADMIN and updates `chat_id`, and logs updates slower than 1.5 s. Then `ErrorMiddleware` maps `AppError` to the Persian `user_message` and anything else to a generic error. Routers are tried in order: `start → common → approvals → attendance → leave → food_admin → food → admin → fallback`. Module routers are left out when switched off in `BOT_MODULES`. Anything a handler leaves uncommitted is rolled back.

**Live check-in.** `AttCb(in)` → `preview_check_in` → a button carrying `ts` (the epoch of the preview time) → `check_in(ts)`. That validates `ts` against `live_confirmation_ttl_seconds`, then `SELECT … FOR UPDATE` on (user, work_date). A unique constraint plus a savepoint absorbs a double tap. Then the audit row, `_refresh_summary` (the daily calculation) and the commit.

**Forgotten entry / edit.** `_plan_correction` / `_plan_edit` check the date window, ordering, maximum shift length and the neighbouring days. Then an `AttendanceManualCorrection` row is written and, if the status is APPLIED, the record changes. Then audit, summary and commit.

**Leave.** `LeaveService.submit` → `ApprovalService.start` (the generic step engine) → approvers are notified. `decide` row-locks the request and its steps, forbids approving your own request, checks the step role, and requires a rejection reason.

**Reminders.** Beat → `run_exclusive` (a Redis lock) → `send_check_in_reminders`. Recipients are active employees on their workday who are not on daily leave and have not checked in. `NotificationRepository.claim` (unique `dedup_key`) commits *before* sending, so delivery is at most once.

**Menu upload.** The admin sends an `.xlsx` (size checked twice) → `parse_menu` (openpyxl, read-only) → preview → the temp file is saved → on confirm it is parsed again and `import_menu` creates a new version and remaps existing orders.

### 1.5 Data model (high level)

`users`, `roles`, `user_roles`, `employee_roster` · `attendance_records` (unique per user/day), `attendance_manual_corrections`, `attendance_daily_summaries` · `leave_requests`, `leave_balances` (in the schema; nothing enforces them yet) · `approval_steps`, `workflow_actions` · `food_menus` (versioned), `food_menu_items`, `food_orders`, `food_guest_orders`, `food_invoices` · `notification_logs` (unique `dedup_key`) · `audit_logs`. Times are stored as UTC `timestamptz`; business dates are Tehran dates. Enums are stored as VARCHAR.

### 1.6 External integrations

The messenger Bot API (Telegram, Bale or Soroush), PostgreSQL and Redis. The legacy SQLite file is used for a one-time import only. There are no other outbound calls.

---

## 2. Findings

Severity: **High** = a wrong business outcome or a security exposure likely in production · **Medium** = a real defect or risk with a limited blast radius · **Low** = hygiene, debt or polish.

### 2.1 Confirmed bugs (reproduced during the audit)

| # | Sev | Finding | Evidence / root cause |
|---|---|---|---|
| B1 | **Medium** | **Typing «لغو» ("cancel") at the name step registers the user with the name «لغو».** | Reproduced through the real dispatcher. `registration_name` (`app/presentation/bot/handlers/start.py:102`) is registered *before* `cancel_command` (`start.py:128`) in the same router, so in `Registration.full_name` every text, including «لغو», is taken as a name, and `validate_full_name` accepts any 3 letters. |
| B2 | **Medium** | **`/cancel` during the staff-number step is used up as a failed attempt**, so a user can lock themselves out by trying to cancel. | Reproduced: `/cancel` answered with «شماره را فقط با رقم بنویسید…». Same cause: `registration_code` (`start.py:65`) catches `F.text` before `cancel_command`. Only the registration states are affected, because the other module routers come after `start.router`. |
| B3 | **Medium** | **`manual_correction_requires_approval: true` is a dead end.** Corrections get status `PENDING_APPROVAL`, but no handler, service method or permission approves or rejects them, so those changes never apply. | `grep PENDING_APPROVAL` finds only the status being *set* (`attendance_service.py:260,379`) and *displayed* (`handlers/attendance.py:439,456`). `docs/ASSUMPTIONS.md` says the service "already supports" approval, but it does not. |
| B4 | **Medium** | **A typo in policy YAML is silently ignored.** For example, `manual_correction_requires_aproval: true` leaves the real switch at `false` with no warning. | All policy `BaseModel`s use pydantic's default `extra="ignore"` (`app/core/config/policy.py`). Checked: `Policy.model_validate({'attendance': {'manual_correction_requires_aproval': True}})` validates and the flag stays `False`. |

> While checking B2, I tested whether `/cancel` lifts the brute-force lock on the staff number. **It does not**: the lock held. That hypothesis is ruled out.

### 2.2 Security and integrity

| # | Sev | Finding | Detail |
|---|---|---|---|
| S1 | **High (business)** | **Employees choose their own attendance times, and nothing approves them.** Today's check-in/out has a «✍️ دستی» (manual time) option. Forgotten entries cover up to 30 days back. Any registered time from the last 30 days can be edited. All of these apply at once by default. | Sources are recorded (`MANUAL_TODAY`, `MANUAL_CORRECTION`, `USER_EDIT`), counted in `team_summary.manual_count` and audited, so this can be *detected*. Nothing *prevents* it, and the only switch meant to (B3) does nothing. The organization needs to decide whether this is acceptable. If not, B3 must be built before the switch is turned on. |
| S2 | Medium | **Check-in/out time comes from the callback payload.** `AttCb.ts` is the epoch the server previewed. The server only checks that `ts` is within `[now − 300 s, now + 60 s]`. | A modified client can move a live punch by up to 5 minutes. That is small, and the user can already type any time through S1. The fix is to keep the preview time in FSM/Redis instead of the button. |
| S3 | Medium | **Admin Excel exports and menu parsing run synchronously on the event loop.** | `build_timesheet_workbook`, `build_attendance_workbook` (`handlers/admin.py:135-137`) and `parse_menu` (`handlers/food_admin.py:133,164`) are CPU-bound openpyxl work. With a single polling process, a monthly team timesheet stalls the bot for **every** user while it builds. Uploads are admin-only and capped at 2 MB (low abuse risk), but a crafted xlsx can still expand heavily when decompressed. Fix: `asyncio.to_thread`, plus a cap on rows and sheets that is already partly there (`MAX_ROWS = 300`). |
| S4 | Low | Authorization for reading a leave request lives in the handler, not the service. | `LeaveService.get_request` (`leave_service.py:157`) has no access check. Only `view_request` compares `user_id`. That is safe today, but it breaks the "every rule in the service" convention, and the next caller could forget the check. |
| S5 | Low | The roster lock is per messenger account and stored in FSM (Redis, 24 h TTL). | Several accounts can each get 5 attempts per hour. Phone-shaped codes make blind guessing impractical, but the error messages show whether a number *exists* («قبلاً ثبت‌نام شده» vs «در فهرست کارکنان نیست»). Low risk inside one organization. |
| S6 | Low | Nothing rejects the default credentials. | `.env.example` ships `POSTGRES_PASSWORD=change-me`, and nothing refuses to start with it when `APP_ENV=production`. Redis has no password, but it is reachable only on the compose network (no published port), which is acceptable. |
| S7 | Low | Unknown role strings would crash permission checks. | `permissions_for` calls `RoleCode(role)` (`rbac.py:72`). A role row that isn't in the enum (for example from a manual DB edit) raises `ValueError` on every update for that user. |

What is **already good** on the security side: the token is a `SecretStr` and is redacted in structlog and in stdlib logs; `.env` is ignored by git and Docker; the container runs as a non-root user; `/health` is published only on host 127.0.0.1; callback data holds no personal data; RBAC lives in one module and is checked in the services; leave requests can't be approved by their owner; a super admin can't remove their own SUPER_ADMIN role or deactivate themselves; the audit log covers role, user, attendance and leave changes.

### 2.3 Reliability and operations

| # | Sev | Finding | Detail |
|---|---|---|---|
| O1 | **High** | **No CI.** There is no `.github/workflows`, so tests, Ruff and migrations are checked only by hand. | The test suite is good and already runs against PostgreSQL. It just isn't enforced. |
| O2 | **High** | **Builds aren't reproducible.** `requirements.txt` uses ranges and there is no lock file. | Today `aiogram>=3.15` resolves to 3.31 and `SQLAlchemy<2.2` to **2.1.8** (the docs say "SQLAlchemy 2"). `structlog>=24.1` has no upper bound. Each `docker compose build` can ship different library versions from the ones tested. |
| O3 | **High** | **A deploy carries on if the backup fails, and off-site copies and restore checks are left to manual steps.** | `docker/deploy.sh:21`: `backup_postgres.sh \|\| echo "warning…"`, then `alembic upgrade head` runs anyway. The scripts write backups unencrypted to `./backups` on the same server. `docs/DEPLOYMENT.md` *recommends* an rsync/rclone off-server copy and a restore drill before launch, but nothing automates or checks either one. If they were never set up, one lost disk or VPS loses the data and every backup. |
| O4 | **Medium (time-bound)** | **Holiday data becomes estimates after 1405/10**, about two months from now. | `iran_holidays.py` covers 1404–1407, and lunar dates after the confirmed calendar are marked «تخمینی». A wrong estimate changes overtime and deficit figures. We need a yearly step: regenerate the file with `scripts/generate_iran_holidays.py` once the official calendar is published, and record corrections in `holiday_exceptions`. |
| O5 | Medium | **Delivery is at most once, and a crash mid-send loses that message for good.** | `NotificationService.notify` commits the `PENDING` claim before sending. If the worker dies between the claim and the send, the row stays `PENDING` and `claim()` never retries it. That is the right choice for reminders, but there is no sweep or alert for stuck `PENDING` rows. |
| O6 | Medium | A long menu broadcast can be killed by Celery's time limit. | The global `task_time_limit=900` (`celery_app.py:28`) applies to `food.broadcast_menu`, which takes a 3600 s lock (`jobs.py:101`). At 20 messages per 1.5 s, the limit is hit after about 12k recipients. That is fine for one organization, but the two limits disagree, and a killed run leaves its lock held for up to an hour. |
| O7 | Low | The per-user lock dictionaries grow without bound. | `UserLockMiddleware.locks` and `last_message` (`middlewares/context.py:47-48`) are never pruned. That is negligible at organization scale. |
| O8 | Low | Health covers the bot only. | The worker and beat have Docker health checks, but nothing alerts when reminders stop. There are no metrics. Slow updates are logged but nobody watches for them. |
| O9 | Low | Base images use floating tags. | `postgres:16-alpine` and `redis:7-alpine` aren't pinned by digest. `PYTHON_IMAGE` can be pinned through `.env`. |

### 2.4 Technical debt and maintainability

| # | Sev | Finding |
|---|---|---|
| D1 | Medium | **The docs have drifted from the code.** `docs/ASSUMPTIONS.md` still says: workdays are "Saturday–Thursday" (config: sat–wed); "anyone who starts the bot can register" (the staff list is now required); "official Iranian holidays are not built in" (they are); the default policy is `standard_deduction` (it is `floating_window`); the repository is `ftmehoseini/claudecode`; the service "already supports" PENDING_APPROVAL (B3). The README says "93 tests" (there are 146). |
| D2 | Low | Legacy files in the repo root: `RastaTTBot.zip`, `bot`, `rastattbot.service`, `crontab.example`. They confuse operators (the systemd unit runs a module that doesn't exist). |
| D3 | Low | Large modules: `attendance_service.py` (752 lines), `handlers/admin.py` (583), `handlers/attendance.py` (469). They are cohesive and readable, so a split is optional, not urgent. |
| D4 | Low | The step-to-permission mapping is hard-coded in `LeaveService.decide` (`step_order == 1 ? STEP_1 : STEP_2`), even though the approval engine is generic. A third step would silently need STEP_2. |
| D5 | Low | `team_reports` loads every record in the range and filters per user in Python (O(users × records)). That is fine at the current scale; group in SQL or with a dict if the staff count grows. |
| D6 | Low | No type checker. The code is heavily annotated, so `mypy` or `pyright` would be cheap to add and would catch `# type: ignore` regressions. |
| D7 | Low | `leave_balances` exists in the schema but is unused (as documented). It is fine to keep, but it should be called out as not implemented. |

---

## 3. Dependencies

Resolved on 2026-11-07 from `requirements.txt` into a clean Python 3.12 venv:

| Package | Constraint | Resolved | Note |
|---|---|---|---|
| aiogram | `>=3.15,<4` | 3.31.0 | Wide range; Bale and Soroush compatibility is tested only against what CI installs (there is no CI) |
| SQLAlchemy[asyncio] | `>=2.0.30,<2.2` | **2.1.8** | A minor-version jump inside the range; tests pass on it |
| asyncpg | `>=0.29,<1` | 0.32.0 | |
| alembic | `>=1.13,<2` | 1.20.1 | |
| redis | `>=5.0,<7` | 6.4.0 | |
| celery[redis] | `>=5.4,<6` | 5.7.0 | |
| aiohttp | (via aiogram) | 3.14.5 | Used directly by `/health` but not declared |
| openpyxl | `>=3.1,<4` | 3.1.5 | |
| jdatetime | `>=5.0,<7` | 6.1.1 | |
| pydantic / pydantic-settings | `<3` | 2.13.5 / 2.16.2 | |
| structlog | `>=24.1` | 26.1.0 | **No upper bound** |
| PyYAML, tzdata | | | |

`pip-audit` reports **no known vulnerabilities** in the application dependencies. The only hits were for the venv's own `pip 24.0`, a build tool that isn't shipped. The Docker image upgrades nothing either, so the same applies there: bump pip in the Dockerfile as hygiene.

Recommendation: add a lock file (`pip-compile`/`uv pip compile` producing `requirements.lock` with hashes), install from it in Docker and CI, declare `aiohttp` directly, cap `structlog`, and run `pip-audit` in CI.

---

## 4. Testing

**Strengths:** pure-domain tests (Jalali boundaries, calculation formulas, the Excel timesheet parity), service tests with real DB transactions, reminder idempotency, the leave state machine, RBAC, and **end-to-end dialogs through the real aiogram `Dispatcher`** with a recording Bot session that also checks blank labels and Soroush's row width. The suite passes on both SQLite and PostgreSQL.

**Coverage gaps (line coverage, 83 % overall):**

| Area | Coverage | Risk |
|---|---|---|
| `infrastructure/tasks/jobs.py`, `celery_app.py` | **0 %** | `run_exclusive`, `unit_of_work`, `cleanup_files` and the broadcast follow-up report are untested |
| `cli.py` (legacy import, create-super-admin) | **0 %** | A one-time data migration with no test |
| `presentation/bot/main.py` | 0 % | Startup and shutdown wiring (acceptable; covered by a smoke run) |
| `handlers/food_admin.py` | 40 % | Upload, confirm and broadcast flows |
| `handlers/admin.py` | 48 % | Roster upload, many report branches |
| `handlers/food.py` | 54 % | Guest orders, invoices |
| `handlers/start.py` | 60 % | **Where B1 and B2 were found** |
| `user_service.py` | 75 % | Roster import edge cases, `set_active` |
| `api/health.py` | 34 % | The 503 path |

Missing kinds of tests: Celery task integration (with eager mode or a direct `run_exclusive` call and fakeredis), concurrency tests on PostgreSQL (two check-ins at once, two approvers at once), and a migration up/down test in CI (I verified it by hand here).

---

## 5. Prioritized improvement plan

Each item is a small, separate PR with tests. Nothing here rewrites the architecture.

### P0: fix now (small, low risk)

1. **Fix B1 and B2: cancel during registration.** Register `cancel_command` before the registration state handlers, or exclude cancel texts from them, so `/cancel` and «لغو» cancel in *every* state *without* clearing the roster lock data. Add regression tests for both.
2. **Fix B4: reject unknown policy keys.** Set `model_config = ConfigDict(extra="forbid")` on the policy models so a typo fails at startup. `check-config` in the CLI already exists to surface it. Check that `config/default.yaml` still loads, and add a test.
3. **CI (O1).** A GitHub Actions workflow running Ruff, pytest on SQLite, pytest plus `alembic upgrade/downgrade/check` on a PostgreSQL 16 service, and `pip-audit`.
4. **Deploy safety (O3, part 1).** In `deploy.sh`, stop if the backup fails, except on a first deploy with an empty database (an explicit `--skip-backup` flag).

### P1: decide and build next

5. **Decide on attendance integrity (S1 and B3). Needs a decision from you or the organization.** Either:
   (a) accept self-reported times and say so in the docs, keeping the audit trail; or
   (b) build correction approval: an `ATTENDANCE_APPROVE_CORRECTION` permission, an inbox, approve or reject with a reason, and apply on approval, reusing the generic `ApprovalService` the way leave does. Then turn on `manual_correction_requires_approval` and optionally limit `✍️ دستی` and edits.
6. **Reproducible builds (O2).** Add a lock file with hashes, use it in the Dockerfile and CI, declare `aiohttp`, cap `structlog`.
7. **Off-site, tested backups (O3, part 2).** Automate what DEPLOYMENT.md asks for by hand: copy backups off the server (any S3-compatible store or a second host reachable from Iran), encrypt them (`age`/`gpg`), and add a monthly restore check with `restore_postgres.sh` into a scratch database.
8. **Holiday calendar process (O4).** A short runbook in DEPLOYMENT.md, and optionally a log warning or admin notice when today falls within 60 days of the first «تخمینی» date.
9. **Move blocking Excel work off the event loop (S3).** Wrap the workbook builders and `parse_menu` in `asyncio.to_thread`.

### P2: hardening and debt

10. Keep the live-punch preview time on the server instead of in the callback (S2).
11. Move the leave-view ownership check into `LeaveService` (S4). Make `permissions_for` skip unknown roles and log them (S7).
12. Refuse to start with default credentials in production (S6).
13. Add a sweep or alert for stuck `PENDING` notification rows (O5). Line up the Celery time limit with the broadcast lock (O6).
14. Tests for Celery jobs, the CLI legacy import and the uncovered handler flows (§4).
15. Bring ASSUMPTIONS.md and the README back in line with the code (D1). Remove or move the legacy files after you confirm (D2).
16. Optional: mypy/pyright in CI (D6); derive the step permission from the workflow definition (D4).

### Not recommended

- No framework or ORM changes. aiogram, SQLAlchemy, Celery and openpyxl all fit the problem.
- No split into microservices. The modular monolith is the right size.
- No webhook migration unless there is a hosting reason. Long polling avoids needing a public HTTPS endpoint, which matters for Iran-hosted VPSs.

---

## 6. Open questions for you

1. **Attendance integrity (item 5):** option (a) self-reported, or (b) manager approval of corrections and edits?
2. **Which platform is in production** (Telegram, Bale or Soroush)? It decides what CI's smoke test and `check-api` should target.
3. **Off-site backup target:** what storage can the production server reach?
4. **Legacy files** (`RastaTTBot.zip`, `bot`, `rastattbot.service`, `crontab.example`): may I delete them, or should I move them to `legacy/`? The legacy data import (`import-legacy-sqlite`) needs only the old SQLite file, not these.

## 7. Limitations of this audit

- The bot wasn't run against a live Telegram, Bale or Soroush API, so there was no token. `check-api` behaviour and the platform quirks are taken from the code and its comments.
- The Celery worker and beat weren't run end to end. Their logic was reviewed by reading it, and the use cases they call are covered by tests.
- Docker images weren't built in this environment.
- I didn't load-test or measure the event-loop stall in S3. The risk is inferred from synchronous openpyxl calls in a single-process poller.
- `pip-audit` reflects the advisory databases on the audit date.
