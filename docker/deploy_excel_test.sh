#!/usr/bin/env bash
# Deploy the Excel-workbook test branch to the DEVELOPMENT bot, then check that it is ready.
#
#   cd /root/rasta-bot-dev
#   git fetch origin claude/zen-cori-7y0vhd && git checkout claude/zen-cori-7y0vhd
#   ./docker/deploy_excel_test.sh
#
# It refuses to run in the production folder (COMPOSE_PROJECT_NAME=rasta-admin-bot).
# Back to the normal development bot: git checkout develop && ./docker/deploy.sh develop
set -euo pipefail

# Inside main() so bash reads the whole file before git can change it.
main() {
  cd "$(dirname "$0")/.."
  local branch="${1:-claude/zen-cori-7y0vhd}"

  local project
  project="$(grep -E '^COMPOSE_PROJECT_NAME=' .env 2>/dev/null | tail -1 | cut -d= -f2 | tr -d '"[:space:]' || true)"
  if [ "${project:-rasta-admin-bot}" = "rasta-admin-bot" ]; then
    echo "This looks like the PRODUCTION bot (COMPOSE_PROJECT_NAME=${project:-<default>}). Test on the development bot."
    exit 1
  fi
  echo "==> Development bot: $project, branch: $branch"

  # Backup, update, rebuild (installs LibreOffice: the first build takes several minutes), restart.
  ./docker/deploy.sh "$branch"

  echo
  echo "==> 1/4 LibreOffice in the worker"
  docker compose exec -T worker soffice --version

  echo "==> 2/4 Database migration (expect 0005)"
  docker compose exec -T worker alembic current 2>/dev/null | tail -1

  echo "==> 3/4 Workbook folder on the data volume"
  docker compose exec -T worker sh -c 'mkdir -p /app/data/workbooks && test -w /app/data/workbooks && echo "/app/data/workbooks is writable"'

  echo "==> 4/4 Write, recalculate with LibreOffice, read back (a small sample file, inside the worker)"
  docker compose exec -T worker python - <<'PY'
import io

import openpyxl

from app.infrastructure.excel.recalc import recalculate
from app.infrastructure.excel.workbook import DayTimes, read_report, write_times

wb = openpyxl.Workbook()
ws = wb.active
ws.title = "آذر"
ws.append(["تاریخ", "ساعت ورود", "ساعت خروج", "ساعت مفید"])
for day in range(1, 31):
    ws.append([day, None, None, f"=C{day + 1}-B{day + 1}"])
    ws[f"D{day + 1}"].number_format = "h:mm"
ws["A33"], ws["B33"] = "مجموع ساعت مفید:", "=SUM(D2:D31)"
ws["B33"].number_format = "[h]:mm"
buffer = io.BytesIO()
wb.save(buffer)
times = DayTimes(5, 8 * 60, 16 * 60 + 30)
report = read_report(recalculate(write_times(buffer.getvalue(), 9, [times])), 9, 5, times)
assert report.ok and dict(report.day_values)["ساعت مفید"] == "8:30", report
print("OK: Excel formulas recalculated by LibreOffice:", report.day_values, report.month_values)
PY

  cat <<'TXT'

==> Ready. Test in the development bot:
  1. Admin: 🛠 پنل مدیریت → 📊 فایل‌های اکسل حضور → 📤 بارگذاری فایل جدید → send the .xlsx
  2. Admin: 👥 کاربران و نقش‌ها → the employee → 📊 فایل اکسل حضور → choose the file
  3. Employee: ⏱ ورود و خروج → ثبت ورود امروز → the reply has the time only;
     a few seconds later the report read from the Excel file arrives
  4. Admin: 📊 فایل‌های اکسل حضور → the file → ⬇️ دریافت نسخه‌ی فعلی (open it in Excel)

  Worker log for the Excel jobs:  docker compose logs -f worker | grep -i workbook
TXT
}

main "$@"
