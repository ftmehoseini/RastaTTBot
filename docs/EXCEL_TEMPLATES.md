# Excel templates and reports

All generated workbooks are right-to-left, with a bold coloured header row, a frozen header, auto-filters and sized columns. Money columns use `#,##0`. Durations are `H:MM` text, because Excel time formats break for negative balances and totals over 24 hours. Jalali dates are written as `1405/07/27` text, so they sort and filter correctly. The detailed attendance report also includes the Gregorian ISO date.

Generation code: `app/infrastructure/excel/`. It takes service DTOs and returns bytes, with no bot or database code.

## Attendance workbooks uploaded by the admin (test version)

For people who have one, the admin's own Excel file calculates everything. The bot only writes
«ساعت ورود» and «ساعت خروج», LibreOffice recalculates a copy, and the bot sends what the formulas
produced. Code: `app/infrastructure/excel/workbook.py` (cells), `recalc.py` (LibreOffice),
`app/application/use_cases/workbook_report.py` (the flow).

**Admin:** 🛠 پنل مدیریت → «📊 فایل‌های اکسل حضور» → upload. Then 👥 کاربران و نقش‌ها → the employee's
card → «📊 فایل اکسل حضور» → choose the file. The month's existing check-ins are written into it.
Any number of files; one per person. Only the super admin can do this.

**What the file needs** (found by text, so no cell addresses to configure):

| Part | Rule |
|---|---|
| Month sheets | Named after the Jalali month: «فروردین» … «اسفند» (other sheets are ignored) |
| Header row | Somewhere in the first 10 rows, with «تاریخ» (day of the month), «ساعت ورود», «ساعت خروج» |
| Day rows | Under the header; the «تاریخ» cell holds the day number (1–31) |
| Day report | Every other column of that day's row, by its header (روز and ماه are skipped) |
| Month report | Every label under the day rows that ends with «:», and the first value to its right |

**After a check-in or check-out** (and after a forgotten entry or an edit) the employee gets the time
only («✅ ورود شما ثبت شد …»). The time is written into the file in the background; no report is sent.
Reports are on request only (below). People without a file keep the bot's own reply.

**«📊 گزارش هفتگی من» / «📅 گزارش ماهانه من»** for people with a file are read from the file too
(recalculated by LibreOffice when the button is pressed). Weekly: each day's check-in/out and the
columns the formulas calculate (zeros hidden), then the month's totals. Monthly: the month's totals.
Which totals, and in which order: `attendance.workbook_report_labels` in `config/default.yaml`
(labels as written in the file, without «:»; `[]` = all of them; if none of them is in a file, all of
that file's totals are shown). The bot adds nothing up itself: a week has no totals of its own because
the workbook has none.

**Safety:** a new version of the file is saved on every change (the last 50 are kept, on the data volume
under `workbooks/`); LibreOffice works on a copy; one person's file is written by one job at a time
(Redis lock). If the month sheet is missing, a formula shows an error (#VALUE!, …), or LibreOffice fails,
no figures are sent: the employee is told the report is delayed and the super admins get the reason.

## Weekly menu upload template

Download it from the bot (**🍽 سفارش غذا → ⚙️ مدیریت غذا → 📄 دریافت قالب اکسل منو**). It comes prefilled with next week's dates (Saturday–Thursday) and has a guide sheet.

| Column | Required | Format | Notes |
|---|---|---|---|
| `jalali_date` | yes | `1405/07/27` (text; Persian digits OK) | A real Excel date cell is read as a **Gregorian** date |
| `weekday_name` | no | `شنبه` … `جمعه` | If filled, must match the date (catches typos). `سه شنبه` and `سه‌شنبه` are both accepted |
| `food_name` | yes | text ≤ 120 | Several rows with the same date = several choices that day |
| `price_toman` | yes | positive whole number | `185000`, `185,000` and `۱۸۵۰۰۰` all work. Upper bound `food.max_price_toman` |
| `is_active` | no | `1`/`0` (also بله/خیر, true/false) | Default 1. Inactive dishes are stored but not orderable |

Persian headers are also accepted: `تاریخ`, `روز`, `نام غذا`, `قیمت (تومان)`, `فعال`.

Validation (all errors are listed with row numbers, at most 15 shown):

- the file is a real `.xlsx` no larger than `MAX_UPLOAD_BYTES`, and the required headers are present
- valid Jalali dates; weekday matches; food name not blank; price is a positive integer; `is_active` is a known value
- no duplicate (date, food name)
- every row in **one** Saturday–Friday week, and that week has not already ended
- at most 300 rows

Example:

| jalali_date | weekday_name | food_name | price_toman | is_active |
|---|---|---|---|---|
| 1405/08/03 | شنبه | قرمه‌سبزی | 185000 | 1 |
| 1405/08/03 | شنبه | جوجه کباب | 210000 | 1 |
| 1405/08/04 | یکشنبه | قیمه | 175000 | 1 |

Uploading a second file for the same week creates version 2. Orders keep the name and price they were placed with. See WORKFLOWS.md for the replacement rules.

## Attendance Excel files: the organization's timesheet (`timesheet_<from>-<to>.xlsx`, `timesheet_me_…`)

**Every** attendance Excel file the bot sends has one structure: the organization's «آبان» sheet, kept as
`app/infrastructure/excel/templates/timesheet_template.xlsx` (layout, formulas, totals block, styles,
column widths and the notes on the totals; no personal data). That includes the employee's weekly and
monthly Excel, and the admin's reports for today, this week, this/last month, a custom range or one
employee. One sheet per employee (hourly staff included), right-to-left, as an Excel table.

For each report the template is recreated for the report's days: one row per day (a 29-, 30- or 31-day
month, a week, any range), each day row with the styles of the matching template row, the template's
formulas (with the table renamed), and the totals block moved right under the last day with its
references adjusted.

| Column | Written by | Content |
|---|---|---|
| A روز, B ماه, C تاریخ | bot | Weekday, Jalali month, day |
| D ساعت ورود, H ساعت خروج | bot | Times (a check-out after midnight is more than 24 h; a check-out without a check-in is left out and noted) |
| E شروع شناوری, F پایان شناوری, G ساعت مجاز خروج | bot | 8:00, 9:30, 16:48 from the policy. Day off with presence: E = F = G = check-in (all overtime). Hourly staff: E = F = check-in and G = check-out (no lateness or early departure) |
| I مرخصی وسط روز, J مرخصي اول و آخر | file | Filled by **📝 ثبت مرخصی** in the employee's own Excel file (see WORKFLOWS.md) and copied from there |
| K ساعت مفید … P اضافه کار روزانه | **formulas** | The template's formulas, character for character |
| Q توضیحات | bot | Holiday, leave, missing time, ثبت فراموشی / ثبت دستی / ویرایش‌شده |

Totals block (the template's own formulas): مجموع کارکرد عادی, مجموع اضافه کار, مقدار تاخیر و تعجیل قابل
جبران, مرخصی مجاز (left empty for the admin), مجموع تاخیر مجاز, مقدار اضافه کار مازاد بر نیاز, مرخصی
مصرفی (= start/end leave + mid-day leave), مجموع تاخیر غیرمجاز (cap 7:00 next to it), میزان تاخیر و تعجیل
جبران نشده, باقیمانده مرخصی, مجموع تعجیل (cap 3:00), کارکرد نهایی (= normal + MIN(overtime, compensable)
+ start/end leave, without factors; the policy's `final_work_*_factor` are 0 so the bot's own text
reports give the same figure). The file has no stored results: Excel or LibreOffice calculates on open.
The test suite compares the generated sheet with the template cell for cell, and recalculates a
generated file with LibreOffice to check it against the bot's own numbers.

**People with their own Excel file:** every export of theirs takes from that file, for each day, what the
admin set there: E/F/G where they differ from the official 08:00 / 09:30 / 16:48 (a registered leave),
I and J, and «توضیحات» (added to the bot's own notes). Check-in and check-out stay the bot's records.
So a leave registered in the bot, or changed by hand in the file and uploaded again, shows in every
export; clearing it in the file and uploading again removes it everywhere.

**Results stored in every file the bot sends.** openpyxl saves formulas without results; desktop Excel
recalculates only after «Enable Editing» (files from Telegram open in Protected View), and phone viewers
and the Telegram preview never do, so they would show 0. Before sending (📥 downloads, admin reports and
⬇️ an employee's own file), LibreOffice calculates a copy and each formula's result is stored in the same
cell of the file (`app/infrastructure/excel/cached_values.py`). Formulas, formatting and comments are
unchanged and Excel still recalculates on open. If LibreOffice fails, the file is sent as it was.

To change the structure, replace the template file with a new version of the organization's sheet
(same column headers); no code change is needed.

## Staff list (`staff_list.xlsx`, admin panel → فهرست کارکنان)

Columns: شماره موبایل یا پرسنلی (text, keeps the leading 0), نام و نام خانوادگی (optional), نوع همکاری (optional: تمام‌وقت / ساعتی), وضعیت ثبت‌نام (download only, ignored on upload). The upload also accepts a sheet without a header (number, name, type in the first three columns) and any way of writing a mobile number. Rows with an invalid or repeated number are listed by row number and nothing is imported until they are fixed. Uploading replaces the list: new numbers are added, names and types are updated, numbers missing from the file are removed unless someone registered with them. A row whose name exactly matches one person who registered before the list existed is linked to that person.

## Attendance report (`attendance_all_<from>-<to>.xlsx`, `attendance_me_…`, `attendance_employee_…`)

Sheet **جزئیات حضور**, one row per employee-day (workdays without a record appear as «بدون ثبت»): نام کارمند, شناسه کاربری, تاریخ شمسی, روز, تاریخ میلادی, ورود, خروج (`(+1)` = next day), کارکرد, موظفی, تأخیر (دقیقه), تعجیل (دقیقه), اضافه‌کار, کسری, خالص, وضعیت, منبع ورود, منبع خروج, اصلاحیه‌ها (each manual correction with its type, status and submission time).

Sheet **خلاصه**, one row per employee: complete, incomplete and absent days, total worked and required time, lateness, early departure, overtime, deficit, net.

## Leave export (`leave_month_…`, `leave_open_…`)

شماره, نام کارمند, نوع (با حقوق/بی‌حقوق), واحد (ساعتی/روزانه), از تاریخ, تا تاریخ, از ساعت, تا ساعت, تعداد روز, مدت (ساعت), وضعیت, دلیل, دلیل رد, تاریخ ثبت.

## Food orders (`food_orders_…`)

تاریخ شمسی, روز, نام کارمند, غذا, نوع (کارمند/مهمان), تعداد مهمان, قیمت واحد, مبلغ کل, وضعیت (ثبت‌شده/لغو شده), زمان ثبت, توضیحات. Cancelled orders are included so the file can be reconciled.

## Weekly invoices (`food_invoices_…`)

Sheet **صورت‌حساب هفتگی**: per employee, own meal count and amount, guest meal count and amount, total payable, plus a totals row. Sheet **ریز اقلام**: every line (employee, date, weekday, dish, type, quantity, unit price, amount).
