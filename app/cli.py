"""Operations CLI.

python -m app.cli create-super-admin --user-id 123456789 --name "نام مدیر"
python -m app.cli import-legacy-sqlite data/attendance.sqlite3 [--dry-run] [--admin-ids 1,2]
python -m app.cli check-config
"""

import argparse
import asyncio
import sqlite3
import sys
from collections import defaultdict
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import app.domain.models  # noqa: F401
from app.application.services.user_service import UserService
from app.core.config import get_policy, get_settings
from app.core.database import create_engine, create_session_factory
from app.core.logging import configure_logging
from app.core.security.rbac import RoleCode
from app.core.utils.clock import SystemClock, TimeService
from app.domain.attendance.models import AttendanceRecord, RecordSource
from app.domain.audit.models import AuditAction
from app.domain.users.models import User, UserRole
from app.infrastructure.repositories.attendance import AttendanceRepository
from app.infrastructure.repositories.audit import AuditRepository
from app.infrastructure.repositories.users import RoleRepository, UserRepository


async def create_super_admin(user_id: int, name: str) -> None:
    settings, policy = get_settings(), get_policy()
    engine = create_engine(settings.database_url)
    try:
        async with create_session_factory(engine)() as session:
            service = UserService(session, policy.users, TimeService(SystemClock(), policy.organization.timezone))
            user = await service.create_super_admin(user_id, name)
            print(f"SUPER_ADMIN ready: {user.full_name} ({user.messenger_user_id})")
    finally:
        await engine.dispose()


async def import_legacy(path: str, source_tz: str, admin_ids: list[int], dry_run: bool) -> None:
    """Import users and sessions from the old Soroush SQLite bot (see docs/INITIAL_AUDIT.md)."""
    tz = ZoneInfo(source_tz)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    legacy_users = conn.execute("SELECT chat_id, full_name, created_at, active FROM users").fetchall()
    sessions = conn.execute("SELECT * FROM sessions ORDER BY check_in").fetchall()
    conn.close()

    def to_utc(value: str | None) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(value).replace(tzinfo=tz).astimezone(UTC)

    by_day: dict[tuple[str, object], list[sqlite3.Row]] = defaultdict(list)
    for row in sessions:
        local_in = datetime.fromisoformat(row["check_in"])
        by_day[(row["chat_id"], local_in.date())].append(row)
    merged_days = sum(1 for rows in by_day.values() if len(rows) > 1)
    print(
        f"legacy users: {len(legacy_users)}, sessions: {len(sessions)}, days: {len(by_day)}, merged days: {merged_days}"
    )
    if dry_run:
        print("dry run: nothing written")
        return

    settings = get_settings()
    engine = create_engine(settings.database_url)
    created_users = created_records = skipped_records = 0
    try:
        async with create_session_factory(engine)() as session:
            users = UserRepository(session)
            roles = RoleRepository(session)
            records = AttendanceRepository(session)
            audit = AuditRepository(session)
            employee = await roles.get_by_code(RoleCode.EMPLOYEE)
            manager = await roles.get_by_code(RoleCode.ADMIN_2_MANAGER)
            user_map: dict[str, User] = {}
            for row in legacy_users:
                messenger_id = int(row["chat_id"])
                user = await users.get_by_messenger_id(messenger_id)
                if user is None:
                    user = users.add(
                        User(
                            messenger_user_id=messenger_id,
                            chat_id=messenger_id,
                            full_name=row["full_name"][:120],
                            is_active=bool(row["active"]),
                            registered_at=to_utc(row["created_at"]),
                        )
                    )
                    await session.flush()
                    session.add(UserRole(user_id=user.id, role_id=employee.id))
                    if messenger_id in admin_ids:
                        session.add(UserRole(user_id=user.id, role_id=manager.id))
                    created_users += 1
                user_map[row["chat_id"]] = user
            await session.flush()

            for (chat_id, work_date), rows in by_day.items():
                user = user_map.get(chat_id)
                if user is None or await records.get_for_day(user.id, work_date):
                    skipped_records += 1
                    continue
                check_in = min(to_utc(r["check_in"]) for r in rows)
                outs = [to_utc(r["check_out"]) for r in rows if r["check_out"]]
                check_out = max(outs) if outs and len(outs) == len(rows) else None
                record = records.add(
                    AttendanceRecord(
                        user_id=user.id,
                        work_date=work_date,
                        check_in_at=check_in,
                        check_out_at=check_out,
                        check_in_source=RecordSource.LEGACY_IMPORT,
                        check_out_source=RecordSource.LEGACY_IMPORT if check_out else None,
                        note=f"merged {len(rows)} legacy sessions" if len(rows) > 1 else None,
                    )
                )
                await session.flush()
                if len(rows) > 1:
                    audit.record(
                        AuditAction.ATTENDANCE_LEGACY_IMPORT,
                        "attendance_record",
                        record.id,
                        None,
                        {"merged_sessions": [r["id"] for r in rows]},
                    )
                created_records += 1
            await session.commit()
    finally:
        await engine.dispose()
    print(f"created users: {created_users}, records: {created_records}, skipped: {skipped_records}")
    print("Run the bot's weekly report to recalculate summaries, or let them refresh on the next edit.")


def check_config() -> None:
    settings, policy = get_settings(), get_policy()
    print(f"environment: {settings.app_env}")
    print(f"platform: {settings.bot_platform.value} ({settings.api_base_url})")
    print(f"bot token set: {bool(settings.bot_token.get_secret_value())}")
    print(f"timezone: {policy.organization.timezone}")
    print(f"workdays: {policy.attendance.workdays}")
    print(f"schedule: {policy.attendance.standard_start_time}–{policy.attendance.standard_end_time}")
    print(f"reminders: in {policy.attendance.check_in_reminder_time}, out {policy.attendance.check_out_reminder_time}")
    print(f"food deadline: {policy.food.ordering_deadline_time}")
    print(f"payment configured: {bool(policy.payment.shaba_number)}")


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, "console")
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_admin = sub.add_parser("create-super-admin", help="create or promote the first SUPER_ADMIN")
    p_admin.add_argument("--user-id", type=int, required=True, help="numeric messenger user ID (/myid)")
    p_admin.add_argument("--name", required=True)

    p_import = sub.add_parser("import-legacy-sqlite", help="import data from the old SQLite bot")
    p_import.add_argument("path")
    p_import.add_argument("--source-timezone", default="Asia/Tehran")
    p_import.add_argument("--admin-ids", default="", help="legacy ADMIN_IDS to grant ADMIN_2_MANAGER")
    p_import.add_argument("--dry-run", action="store_true")

    sub.add_parser("check-config", help="print the effective configuration (no secrets)")

    args = parser.parse_args(argv)
    if args.command == "create-super-admin":
        asyncio.run(create_super_admin(args.user_id, args.name))
    elif args.command == "import-legacy-sqlite":
        ids = [int(x) for x in args.admin_ids.split(",") if x.strip()]
        asyncio.run(import_legacy(args.path, args.source_timezone, ids, args.dry_run))
    elif args.command == "check-config":
        check_config()
    return 0


if __name__ == "__main__":
    sys.exit(main())
