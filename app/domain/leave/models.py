"""Leave types, as an admin registers them with «📝 ثبت مرخصی» (see excel_rules.py).

Leave lives only in the employee's Excel file: there is no leave request, approval or balance in the
database any more. (The old tables leave_requests, leave_balances, approval_steps and workflow_actions
are left untouched in existing databases; nothing reads them.)
"""

from enum import StrEnum


class LeavePayType(StrEnum):
    PAID = "PAID"
    UNPAID = "UNPAID"


class LeaveUnit(StrEnum):
    HOURLY = "HOURLY"
    DAILY = "DAILY"
