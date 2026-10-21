# Architecture

A modular monolith with four layers. Dependencies point inward: presentation → application → domain. Infrastructure implements what the application needs (repositories, Excel, messenger, tasks).

```
app/
  core/                 shared technical foundations (no business rules)
    config/             Settings (.env, secrets) + Policy (YAML, business rules)
    database/           Declarative Base, mixins (UUID PK, timestamps, authorship), engine/session
    security/rbac.py    RoleCode, Permission, role → permission map
    logging/            structlog JSON, correlation IDs, token redaction
    exceptions/         AppError hierarchy with Persian user_message
    utils/              clock.py (UTC ↔ Tehran), jalali.py (the only calendar code), text.py
  domain/               ORM models, enums and pure rules per bounded context
    users/ audit/ notifications/
    approvals/          generic step workflow: models + engine.py (pure state machine)
    attendance/         records, corrections, daily summaries, calculation.py (strategies)
    leave/  food/       (report data shapes live in application/dto, builders in infrastructure/excel)
    models.py           imports every model (Alembic, tests)
  application/          use-case logic, independent of Telegram
    services/           AttendanceService, LeaveService, FoodService, ApprovalService,
                        NotificationService, UserService, access.py (permission checks),
                        container.py (per-request service factory)
    use_cases/          multi-service jobs: reminders, food broadcast
    dto/                dataclasses passed between layers; Messenger port (messaging.py)
    presenters/         DTO → Persian text (shared by bot handlers and background jobs)
  infrastructure/
    repositories/       every SQL query lives here
    excel/              workbook styling, report builders, menu template/parser
    integrations/       aiogram transport (Telegram/Bale/Soroush) implementing Messenger
    tasks/              Celery app, Beat schedule, tasks
  presentation/
    bot/                aiogram: dispatcher, middlewares, filters, routers (handlers/),
                        keyboards (incl. Jalali calendar/time pickers), callbacks, FSM states
    api/                /health endpoint (place for a future admin API)
  tests/
alembic/                migrations (0001 = full schema + role seed)
config/default.yaml     organization policy
docker/                 backup/restore scripts
```

## Rules the code follows

- **Thin handlers.** A handler reads the update, calls one service method, and renders a presenter's text. No SQL and no business rules.
- **Repositories own SQL.** Services never build queries.
- **Services own transactions.** Every command method validates, locks, writes, records an audit entry and calls `commit()`. Notifications go out after the commit (`NotificationService.notify` commits its own log row).
- **One clock.** `TimeService` converts between UTC (stored) and Asia/Tehran (business dates). Nothing calls `datetime.now()` directly; tests inject `FixedClock`.
- **One calendar.** All Jalali conversion and formatting is in `core/utils/jalali.py`.
- **Policy, not constants.** Every threshold comes from `Policy` (YAML). There are no magic numbers in services.
- **Permissions in one place.** `application/services/access.py` (`can` / `require`) over `core/security/rbac.py`. Services check permissions; handlers also hide buttons the user may not use.

## Main data flows

### A button press

```
Telegram/Bale update
 → UserLockMiddleware   (one update at a time per user; drops message floods)
 → ContextMiddleware    (correlation ID, DB session, Services container, current User)
 → ErrorMiddleware      (AppError → Persian alert/message; others → logged + generic message)
 → Router + filters     (IsRegistered, FSM state, CallbackData)
 → handler → service (validate → lock → write → audit → commit) → presenter → edit message
```

### Live check-in

`preview_check_in` shows the Tehran time and puts its UTC epoch in the confirm button. `check_in` accepts that time only within `live_confirmation_ttl_seconds`. It locks the day's record (`SELECT … FOR UPDATE`, falling back to the unique constraint on an insert race), writes the punch, refreshes `attendance_daily_summaries` and commits. A second tap returns the existing record.

### Leave approval

`LeaveService.submit` creates the request and its `approval_steps` (step 1 PENDING, step 2 WAITING). Secretaries are notified with ✅/❌ buttons. `ApprovalService.decide` locks the steps and runs the pure engine. The engine refuses stale steps, wrong roles and missing rejection reasons. The leave status is updated in the same transaction, then the employee and the next approvers are notified.

### Reminders

Beat → `attendance.check_in_reminders` (every 10 minutes) → Redis lock → `send_check_in_reminders`: is today a workday, is the reminder due (configured time ≤ now < time + catch-up), who is missing (active employees − checked in − on approved daily leave) → `notify_many` with dedup key `check_in_reminder:<date>:<user>` → one `notification_logs` row per person per day.

### Menu import

Excel upload → size and extension checks → `parse_menu` validates (headers, Jalali dates, weekday match, prices, duplicates, one week, not in the past) → preview (affected orders, price changes) → confirm → new `food_menus` version. Existing orders are moved to the matching dish in the new version, or kept with their snapshot name and price.

## Extension points

| Need | Where |
|---|---|
| New module / main-menu button | `presentation/bot/modules.py` (`MENU_ENTRIES`) + a router in `dispatcher.py` |
| New permission | `Permission` + `ROLE_PERMISSIONS` in `core/security/rbac.py` |
| New role | `RoleCode`, `ROLE_TITLES_FA`, `ROLE_PERMISSIONS`, a migration that inserts the row |
| New approval workflow | a `WorkflowDefinition` + an `ApprovalHandler` registered in `handlers/approvals.py` (`HANDLERS`) |
| Different attendance formula | implement `CalculationPolicy`, register it in `CALCULATION_POLICIES`, set `attendance.calculation_policy` |
| Per-user or per-group schedules | `ScheduleResolver.for_day` (`users.schedule_code` already exists) |
| New scheduled job | a task in `infrastructure/tasks/jobs.py` + an entry in `beat_schedule` |
| New report/Excel | a builder in `infrastructure/excel/reports.py` using `add_table_sheet` |
| New transport | implement the `Messenger` protocol (`application/dto/messaging.py`) |
| Admin dashboard / REST API | `presentation/api/` using the same `Services` container |

## Concurrency and idempotency

| Risk | Protection |
|---|---|
| Double-tap on a confirm button | per-user update lock + idempotent services (existing record returned) |
| Two requests creating the same day/order | unique constraints (`user_id, work_date`), (`user_id, order_date`) + savepoint retry |
| Two admins approving at once | `SELECT … FOR UPDATE` on approval steps; the engine rejects a non-current step |
| Stale buttons after a decision | engine `ConflictError` → "already reviewed" alert |
| Duplicate reminders / broadcasts | unique `notification_logs.dedup_key`, claimed and committed before sending; Redis lock per job |
| Worker crash mid-broadcast | re-running resumes: SENT keys are skipped, FAILED keys retried up to `max_send_retries` |
