# Workflows

All user-facing text is Persian. Every multi-step flow has a «✖️ لغو» button, `/cancel` works anywhere, and pressing an outdated button shows «این دکمه منقضی شده است».

## Main menu (reply keyboard)

`⏱ ورود و خروج` · `🍽 سفارش غذا` · `🛠 پنل مدیریت` (admins only) · `ℹ️ راهنما`

Buttons are shown according to the user's permissions (`presentation/bot/modules.py`). Commands: `/start`, `/myid`, `/whoami`, `/help`, `/cancel`, `/user <id>` (admins), `/health` (super admin).

## Registration

`/start` → if unknown, ask for the person's number on the staff list (mobile in any form — ۰۹۱۲…, +98 912…, 912… — or a personnel code) → it must be on the list and not used yet → if the list has a name, register with it, otherwise ask for the full name → validate (letters, 3–80 characters) → create the user with role `EMPLOYEE` (hourly if the list says «ساعتی») → show the main menu. Five wrong numbers lock registration for that person for 60 minutes (survives /start). With `users.require_employee_roster: false` the number step is skipped. If `users.require_admin_activation` is on, the account stays inactive until a SUPER_ADMIN activates it. IDs in `BOOTSTRAP_SUPER_ADMIN_IDS` also get `SUPER_ADMIN`.

## Attendance

Sub-menu: ثبت ورود امروز · ثبت خروج امروز · ویرایش ورود/خروج · ثبت فراموشی · گزارش هفتگی من · گزارش ماهانه من · بازگشت (today's figures are in the weekly report)

**Days off.** Iran's official holidays (built-in calendar), Thursday and Friday are off; a person can have their own workdays, e.g. one who works Thursday (admin panel → user card → «📅 روزهای کاری»: toggle each day, «↩️ پیش‌فرض سازمان» to go back). Presence on a day off is all overtime (🌙 in the weekly report). Check-in reminders follow each person's workdays and skip holidays.

**Entering a time.** In the forgotten-entry, manual-time and edit dialogs the time is picked on a keypad, one digit at a time (⌨️ `۱۴:۳_`): only digits that can come next are offered (first 0–2, then 0–9 or 0–3 after a 2, then 0–5, then 0–9), with «⌫» to delete one. Soroush accepts at most two buttons per row, so the four digit columns are filled in turn rather than side by side. Typing `HH:MM` works too.

**Edit** («✏️ ویرایش ورود/خروج»). Pick a day (today or up to `max_manual_correction_age_days` back) → the day's registered times are shown → choose the check-in or check-out to change → new time → review (old ← new) → confirm. Same checks as a forgotten entry (not in the future, check-out after check-in, maximum shift, neighbouring days). The record gets source `USER_EDIT` (✏️), and the change, with the previous value, goes into `attendance_manual_corrections` and the audit log. A day with nothing registered points to «ثبت فراموشی».

Every multi-step dialog (forgotten entry, edit) has «✖️ لغو», which ends the dialog, and «🔙 مرحله قبل», which goes back one step and keeps the earlier answers.

**Full-time and hourly staff.** By default a user is full-time and the organization rules apply (08:00–16:48, floating check-in until 09:30, unauthorized lateness, early departure, overtime and the monthly caps; see `floating_window` in CONFIGURATION.md). An admin with role management can switch a user to hourly on the user card («⏰ تبدیل به نیروی ساعتی», stored as `users.schedule_code = 'hourly'`). For hourly staff only check-in, check-out and worked time are recorded: no lateness, early departure, overtime, deficit or caps and days without a record are not absences. They get the same check-in and check-out reminders as everyone else.

**Check-in.** Show the Jalali date and Tehran time → choose «⏱ اتوماتیک» (save the current time, `BOT_LIVE`, UTC) or «✍️ دستی» (type today's time as `HH:MM`, validated like a forgotten entry → review → saved as `MANUAL_TODAY`, shown with ✍️). Check-out offers the same two choices. If today already has a check-in, the registered time is shown instead. If yesterday's shift is still open and within `max_shift_hours`, the user is asked to check out first.

**Check-out.** Show the time → confirm → save → reply with today's worked time and net balance. Without a check-in it points to the forgotten-entry flow. A shift that crossed midnight is closed on the previous day's record. A duplicate shows the registered time.

**Forgotten entry** (earlier days only; today's time uses «دستی» on the check-in/out buttons). Pick the day from a list of dates (8 per page, «روزهای قبل / بعد» to page), or type it as `1405/07/26` → choose ورود or خروج → enter the time (keypad or typed, see «Entering a time»). A wrong time is answered right away with the reason and an example (bad format, hour above 23, minute above 59), and the day's rules (future time, check-out before check-in, overlap) are checked as soon as the time is typed, so the person can correct it without starting over → review → confirm. A day where both were forgotten is entered as two corrections: first ورود, then خروج. Validation: not in the future, not older than `max_manual_correction_age_days`, check-out after check-in and within the maximum shift, no overlap with neighbouring days, no overwriting. The only exception: an existing check-in may be moved **earlier** to fix a late button press. Saved with source `MANUAL_CORRECTION` and an immutable `attendance_manual_corrections` row (claimed and previous values, submitter, time). If `manual_correction_requires_approval` is on, the correction is stored as `PENDING_APPROVAL` and not applied.

**Weekly report.** Current Jalali week (Saturday–Friday): each day's check-in and check-out, allowed lateness*, unauthorized lateness*, early departure*, overtime and normal work (the timesheet's K), approved leave, missing registrations (⚠️), today's manual times (✍️), forgotten entries (📝), edits (✏️); the week's totals; the month so far (unauthorized lateness and early departure split into compensable and non-compensable under the 7 h / 3 h caps, and what is not yet compensated); footnotes (*) with the rules. An "Excel" button sends the days as a file.

**Monthly report.** The timesheet's summary block for the current Jalali month: normal work, allowed lateness, unauthorized lateness and early departure each split into compensable / non-compensable, overtime, paid leave, then compensable total, overtime surplus, uncompensated and final work (see `floating_window` in CONFIGURATION.md). Its Excel button sends the organization's timesheet workbook (EXCEL_TEMPLATES.md).

**Reminders.** At `check_in_reminder_time` on workdays, active employees with no check-in today get a reminder . At `check_out_reminder_time`, people checked in without a check-out get one. Each is sent at most once per person per day.

## Food

Employee sub-menu: منو و سفارش هفته · سفارش امروز · سفارش مهمان · صورت‌حساب من · بازگشت. Users with food admin permissions also see «⚙️ مدیریت غذا».

**Week order («📋 منو و سفارش هفته»; also the button under the menu notification).** One screen: the message gives the instructions, the deadline (one line) and the running total; the buttons are every dish of every day of this week and next that is still open, with day, name and price, ✅ marking the chosen ones. Tapping a dish ticks or unticks it (one meal per day by default: another dish on the same day replaces the choice). Nothing is saved until «✅ ثبت سفارش هفته»: then each listed day's order becomes exactly what is ticked, and a day that was unticked is cancelled. A day whose deadline passed while choosing is reported as not changed, with its deadline; the others are saved. «↩️ برگرداندن به سفارش‌های ثبت‌شده» goes back to what is registered.

**Today («🍛 سفارش امروز»).** Today's dishes → choose → confirm, as before. After `ordering_deadline_time` on the day itself, ordering, changing and cancelling are refused.

**Guest order («👥 سفارش مهمان»).** One screen with every day still open for guests and its dishes with prices: «➕ day · dish · price · ×N» adds one guest portion, «➖» next to it removes one (two buttons per row for Soroush). The message shows the guest deadline and the running total; nothing is saved until «✅ ثبت سفارش مهمان (N پرس)». Each dish/day is then set to exactly that count; a count brought back to 0 cancels that guest order; at most `max_guest_count_per_order` (10) per dish per day; days that closed meanwhile are reported as not changed. «↩️ برگرداندن به سفارش‌های ثبت‌شده» discards unsaved changes. Guest meals are stored, reported and billed separately from the employee's own meal. (The old step-by-step guest buttons in earlier messages still work.)

**Bill («🧾 صورت‌حساب من»).** This week's lines (own and guest), totals, and the payment details from `payment.*`, with «🗑 لغو مهمان» for guest orders still open and «🧾 صورت‌حساب هفته‌ی بعد» when there are orders next week. (The former «📊 گزارش غذای این هفته» showed the same; its buttons in old messages open the bill.)

### Admin

«📤 آپلود/به‌روزرسانی منوی هفته» → send the `.xlsx` → validation errors (with row numbers) or a preview listing each day's dishes. The preview also warns about replacing an existing version, orders on removed dishes and price changes → confirm → a new menu version. «📄 دریافت قالب اکسل منو» sends a ready template for next week.

Only the food admin role «مسئول غذا» (`FOOD_ADMIN`: `food.menu_manage`, `food.broadcast`, `food.report_all`) and the super admin manage food; the secretary and the manager don't. A food admin doesn't see «پنل ادمین». Without the `EMPLOYEE` role the food menu shows only «⚙️ مدیریت غذا». Assign it from the user's card in پنل ادمین.

When a new menu version drops a dish someone had ordered (own or guest, on a day still open), only those people get a message listing the dish and day, with a «🍽 ثبت سفارش غذا» button. The upload result says how many were told. The old order itself is left as it was.

«📣 اطلاع‌رسانی» → pick a ready text (`food.announcement_samples`: «منو هفتگی آپلود شد…», «در منو تغییراتی رخ داد…») or «✍️ متن دلخواه» and type one (≤ 1000 characters) → preview with the recipient count (every active user) → confirm → sent in the background in rate-limited batches, each with a «🍽 ثبت سفارش غذا» button. Each announcement is sent once; pressing the same confirmation again is refused. The admin receives a delivery summary. Sending an announcement marks the latest menu as announced.

Once the week's menu has been announced (`food.notify_food_admins_of_changes`), every food admin gets one message per confirmed change: the weekly screen, today's order, cancelling, guest orders and cancelling a guest order, e.g. «🍽 مریم کریمی سفارش غذا را تغییر داد: • دوشنبه ۲۷/۷: جوجه کباب». Before the announcement nothing is sent, so the first wave of orders doesn't flood them. A failure to send never affects the order.

Reports: «📊 گزارش این هفته / هفته بعد» (per day: portions per dish, guests, total) · «📥 فایل سفارش‌ها» (detailed Excel for this week and next) · «🧾 صورت‌حساب‌ها» (per-employee weekly invoice Excel, which also refreshes `food_weekly_summaries`).

## Admin panel

| Button | Permission | Action |
|---|---|---|
| حضور امروز / هفته جاری / ماه جاری / ماه قبل | `attendance.report_all` | Summary message + detailed Excel; a whole month also sends the timesheet workbook |
| بازه‌ی دلخواه | `attendance.report_all` | Start/end Jalali calendar → report |
| گزارش یک کارمند | `attendance.report_all` | Pick an employee → week/month/previous month/range |
| ثبت مرخصی | `leave.register` | Register a leave in the employee's Excel file (see below) |
| کاربران و نقش‌ها | `users.view` (edit: `users.manage_roles`) | List → user card → toggle roles / activate. The user is notified |
| فهرست کارکنان (ثبت‌نام) | `users.manage_roles` | Counts (registered / not yet) → download the list (also the empty template) → upload a new list → preview (new, changed, removed, linked, kept) → confirm |
| وضعیت سیستم | `system.health` | Database/Redis status and server time |

## Leave registered by an admin (written into the employee's Excel file)

🛠 پنل مدیریت → **📝 ثبت مرخصی** (secretary, manager and super admin; permission `leave.register`,
checked on every step). This is the only way leave enters the system: employees do not request leave in the bot.

1. Employee: active, full-time only (hourly staff are not listed and are refused).
2. Type: the existing ones, با حقوق / بدون حقوق.
3. Duration: روزانه or ساعتی.
4. Date (calendar or typed, within `leave.max_past_days` / `leave.max_future_days`); for hourly leave the
   start x and end y are typed as HH:MM.
5. Preview: the case and the cells that will change → confirm.

The employee's **uploaded Excel file is the only record**: the cells of that day's row are overwritten,
«توضیحات» gets «مرخصی ساعتی با حقوق ۰۸:۰۰ تا ۱۰:۳۰» / «مرخصی روزانه …» after what is already there,
(a new version of the file is saved; older versions stay on the server) and the registration is kept in
the audit log (`LEAVE_REGISTERED_BY_ADMIN`). The employee gets a message. There is no cancel button: to
cancel or correct a leave, the admin downloads the file (employee card → 📊 فایل اکسل حضور), clears the
cells and uploads it again. Every Excel export of that person (📥 in «گزارش ماهانه/هفتگی من», admin reports)
takes these cells from the file, so the leave shows there too, and disappears when it is cleared.

Cases (`app/domain/leave/excel_rules.py`). E/F/G are read from the employee's row of that day
(08:00 / 09:30 / 16:48 in the organization's sheet):

| Case | Condition | Cells written |
|---|---|---|
| 1 start of the day | x ≤ F and y < G | E = E + (y − x), F = y, J = y − x |
| 2 middle of the day | x > F and y < G | I = y − x (nothing else) |
| 3 end of the day | x > F and y = G | G = x, J = y − x |
| daily | — | J = G − E (the whole official day, 8:48) |

Refused, with nothing written: x ≥ y, x before E, y after G, x ≤ F with y = G (use daily leave), an
employee who is hourly, inactive or has no Excel file, a file without the month's sheet, that day's row
or the needed columns, and a day whose I or J already has a value.

