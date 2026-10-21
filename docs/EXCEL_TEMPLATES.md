# Excel templates and reports

All generated workbooks are right-to-left, with a bold coloured header row, a frozen header, auto-filters and sized columns. Money columns use `#,##0`. Durations are `H:MM` text, because Excel time formats break for negative balances and totals over 24 hours. Jalali dates are written as `1405/07/27` text, so they sort and filter correctly. The detailed attendance report also includes the Gregorian ISO date.

Generation code: `app/infrastructure/excel/`. It takes service DTOs and returns bytes, with no bot or database code.

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

## Attendance report (`attendance_all_<from>-<to>.xlsx`, `attendance_me_…`, `attendance_employee_…`)

Sheet **جزئیات حضور**, one row per employee-day (workdays without a record appear as «بدون ثبت»): نام کارمند, شناسه کاربری, تاریخ شمسی, روز, تاریخ میلادی, ورود, خروج (`(+1)` = next day), کارکرد, موظفی, تأخیر (دقیقه), تعجیل (دقیقه), اضافه‌کار, کسری, خالص, وضعیت, منبع ورود, منبع خروج, اصلاحیه‌ها (each manual correction with its type, status and submission time).

Sheet **خلاصه**, one row per employee: complete, incomplete and absent days, total worked and required time, lateness, early departure, overtime, deficit, net.

## Leave export (`leave_month_…`, `leave_open_…`)

شماره, نام کارمند, نوع (با حقوق/بی‌حقوق), واحد (ساعتی/روزانه), از تاریخ, تا تاریخ, از ساعت, تا ساعت, تعداد روز, مدت (ساعت), وضعیت, دلیل, دلیل رد, تاریخ ثبت.

## Food orders (`food_orders_…`)

تاریخ شمسی, روز, نام کارمند, غذا, نوع (کارمند/مهمان), تعداد مهمان, قیمت واحد, مبلغ کل, وضعیت (ثبت‌شده/لغو شده), زمان ثبت, توضیحات. Cancelled orders are included so the file can be reconciled.

## Weekly invoices (`food_invoices_…`)

Sheet **صورت‌حساب هفتگی**: per employee, own meal count and amount, guest meal count and amount, total payable, plus a totals row. Sheet **ریز اقلام**: every line (employee, date, weekday, dish, type, quantity, unit price, amount).
