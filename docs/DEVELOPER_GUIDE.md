# Developer guide

## Setup

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest            # must stay green
.venv/bin/ruff check . && .venv/bin/ruff format .
```

Run against real PostgreSQL before merging anything that touches queries or locking:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/rasta_bot_test .venv/bin/pytest
```

## Conventions

- Code, identifiers, logs and docs are in English. **Everything a user reads is Persian**, in `application/presenters/` or the handler.
- Store UTC (`timestamptz`), compute business dates with `TimeService`, format with `core/utils/jalali.py`.
- Services raise `AppError` subclasses with a Persian `user_message`. The error middleware shows it. Never `print` or send raw exceptions.
- Every state-changing service method: `access.require(...)` → validate → lock (`for_update=True`) → write → `self.audit.record(...)` → `commit()`.
- Callback payloads use `CallbackData` classes in `callbacks/data.py`. Keep them under 64 bytes and put only opaque IDs (UUID hex), ordinals and short codes in them. Anything larger goes in FSM data.
- Every multi-step flow sets an FSM state, offers `cancel_row()`, and has a stale-button handler that raises `ExpiredActionError`.
- New thresholds go into `Policy` and `config/default.yaml`, and are documented in `CONFIGURATION.md`.

## Adding a new module (example: "Mission requests")

1. **Domain:** `app/domain/missions/models.py` holds the enums (`MissionStatus`) and ORM models with `UUIDPrimaryKeyMixin`, `TimestampMixin` and `AuthorshipMixin`, plus unique constraints for anything that must not duplicate. Import the models in `app/domain/models.py`.
2. **Repository:** `app/infrastructure/repositories/missions.py` subclasses `Repository[Mission]` and holds every query the module needs.
3. **Service:** `app/application/services/mission_service.py` holds the rules, permission checks, transactions and audit entries. Put DTOs in `app/application/dto/missions.py` and register the service in `Services` (`container.py`) as a `cached_property`.
4. **Permissions:** add `Permission.MISSION_SELF` and `Permission.MISSION_APPROVE` in `core/security/rbac.py` and grant them in `ROLE_PERMISSIONS`. Add new `AuditAction` values in `domain/audit/models.py`.
5. **Policy:** add a `MissionPolicy` section to `core/config/policy.py` and `config/default.yaml`.
6. **Texts:** add `app/application/presenters/missions.py`.
7. **Bot:** create `presentation/bot/handlers/missions.py` with `router = Router(name="missions")`, `router.message.filter(IsRegistered())` and `router.callback_query.filter(IsRegistered())`. Add callback classes in `callbacks/data.py`, states in `states/flows.py`, the main-menu button in `modules.py` (`MENU_ENTRIES`, with its visibility permission), and the router in `ROUTERS` in `dispatcher.py`, before `fallback.router`.
8. **Approvals (if needed):** build a `WorkflowDefinition` (steps and roles), call `ApprovalService.start/decide/cancel` from the service, and register an `ApprovalHandler` under a short code in `handlers/approvals.py` (`HANDLERS["ms"] = MissionApprovalHandler()`). The ✅/❌ buttons, reason prompt, duplicate-click protection and history come for free.
9. **Migration:** run `alembic revision --autogenerate -m "missions"`, review the file, then `alembic upgrade head`. Seed rows (like new roles) with `op.bulk_insert`.
10. **Scheduled work (if needed):** a use case in `application/use_cases/`, a task in `infrastructure/tasks/jobs.py` wrapped in `run_exclusive`, a `beat_schedule` entry, and dedup keys on every notification.
11. **Tests:** service tests with the `services`, `make_user` and `clock` fixtures, plus an end-to-end flow in the style of `test_bot_flows.py`.
12. **Docs:** update `WORKFLOWS.md`, `CONFIGURATION.md` and the module table in `README.md`.

## Adding a button to an existing module

1. Add an action code to the module's `CallbackData` (e.g. `AttCb(a="month")`).
2. Add the button to the module's `menu_markup()`, hidden with `access.can(...)` if it is restricted.
3. Add the handler `@router.callback_query(AttCb.filter(F.a == "month"))`. It calls a service method and renders a presenter.
4. Write a test.

## Change the attendance formula

```python
# app/domain/attendance/calculation.py
class NoOvertimeWhenLatePolicy(StandardDeductionPolicy):
    name = "no_overtime_when_late"

    def calculate(self, schedule, check_in, check_out):
        result = super().calculate(schedule, check_in, check_out)
        if result.late_minutes:
            return dataclasses.replace(result, overtime_minutes=0)
        return result


CALCULATION_POLICIES[NoOvertimeWhenLatePolicy.name] = NoOvertimeWhenLatePolicy()
```

Then set `attendance.calculation_policy: no_overtime_when_late`. Summaries refresh whenever a record changes. Reports always recalculate from records, so old days follow the new policy in reports too.

Per-user or per-group schedules: give `users.schedule_code` a value and return a different `WorkSchedule` from `ScheduleResolver.for_day`, for example from a new `work_schedules` table.

## Sending notifications

Use `services.notifications.notify_user(user, text, NotificationType.X, buttons=InlineButtons.of(...), dedup_key=...)` after committing. Give a `dedup_key` to anything that must not repeat (reminders, approval requests, broadcasts). Background jobs must never import `presentation`. Buttons they send use plain callback strings that a router handles (see `OPEN_ATTENDANCE` / `OPEN_FOOD`).

## Debugging

- Every log line carries `correlation_id` and `update_id`. Errors shown to users are logged at `info` with the internal detail.
- `/health` (HTTP) and `/health` (bot command, SUPER_ADMIN) check the database and Redis.
- `python -m app.cli check-config` prints the effective configuration without secrets.
