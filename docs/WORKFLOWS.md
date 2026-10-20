# Workflows

All user-facing text is Persian. Every multi-step flow has a «✖️ لغو» button, `/cancel` works anywhere, and pressing an outdated button shows «این دکمه منقضی شده است».

## Main menu (reply keyboard)

`⏱ ورود و خروج` · `📝 مرخصی` · `🍽 سفارش غذا` · `🛠 پنل مدیریت` (admins only) · `ℹ️ راهنما`

Buttons are shown according to the user's permissions (`presentation/bot/modules.py`). Commands: `/start`, `/myid`, `/whoami`, `/help`, `/cancel`, `/user <id>` (admins), `/health` (super admin).

## Registration

`/start` → if unknown, ask for the full name → validate (letters, 3–80 characters) → create the user with role `EMPLOYEE` → show the main menu. If `users.require_admin_activation` is on, the account stays inactive until a SUPER_ADMIN activates it. IDs in `BOOTSTRAP_SUPER_ADMIN_IDS` also get `SUPER_ADMIN`.

## Attendance

Sub-menu: ثبت ورود · ثبت خروج · ثبت فراموشی ورود/خروج · گزارش هفتگی من · وضعیت امروز · بازگشت

**Check-in.** Show the Jalali date and Tehran time → confirm → save (`BOT_LIVE`, UTC). If today already has a check-in, the registered time is shown instead. If yesterday's shift is still open and within `max_shift_hours`, the user is asked to check out first.

**Check-out.** Show the time → confirm → save → reply with today's worked time and net balance. Without a check-in it points to the forgotten-entry flow. A shift that crossed midnight is closed on the previous day's record. A duplicate shows the registered time.

**Forgotten entry.** Choose ورود or خروج → pick the day from a list of dates (8 per page, «روزهای قبل / بعد» to page), or type it as `1405/07/26` → hour buttons → minute buttons, or type `08:45` (Persian digits work) → review → confirm. A day where both were forgotten is entered as two corrections: first ورود, then خروج. Validation: not in the future, not older than `max_manual_correction_age_days`, check-out after check-in and within the maximum shift, no overlap with neighbouring days, no overwriting. The only exception: an existing check-in may be moved **earlier** to fix a late button press. Saved with source `MANUAL_CORRECTION` and an immutable `attendance_manual_corrections` row (claimed and previous values, submitter, time). If `manual_correction_requires_approval` is on, the correction is stored as `PENDING_APPROVAL` and not applied.

**Weekly report.** Current Jalali week (Saturday–Friday): each day's in/out, worked time, lateness, early departure, overtime, deficit, net, missing registrations (⚠️), manual entries (✍️), and weekly totals. An "Excel" button sends the same data as a file.

**Reminders.** At `check_in_reminder_time` on workdays, active employees with no check-in today get a reminder (people on approved daily leave are skipped). At `check_out_reminder_time`, people checked in without a check-out get one. Each is sent at most once per person per day.

## Leave

Sub-menu: درخواست مرخصی جدید · درخواست‌های من · وضعیت مرخصی‌ها · بازگشت

```
New: پرداخت (با حقوق / بی‌حقوق) → نوع (ساعتی / روزانه)
  ساعتی:  date → start time → end time → reason (optional) → review → submit
  روزانه: start date → end date (list starts at the start date) → reason → review (shows days) → submit
  Dates: tap a day in the list or type 1405/07/26; times: hour → minute buttons or type 10:30
```

Validation: start ≤ end; hourly leave is within one day and end > start; dates within `max_past_days`..`max_future_days`; at most `max_daily_leave_days`; no overlap with pending/approved requests unless `allow_overlapping_leave_requests`.

### Approval state machine

```
                ┌──────── reject (reason) ────────┐
submit → PENDING_ADMIN_1 ── approve ──→ PENDING_ADMIN_2 ── approve ──→ APPROVED
                │                           │
                └── reject (reason) ──→ REJECTED ←── reject (reason) ┘
PENDING_* ── employee cancels ──→ CANCELLED
```

- Admin 1 (`ADMIN_1_SECRETARY`) is notified on submit. Admin 2 (`ADMIN_2_MANAGER`) is notified **only after** Admin 1 approves. `SUPER_ADMIN` may decide any step.
- Each admin message shows the employee, type, dates or times, duration, reason and current step, with ✅ تأیید / ❌ رد.
- Rejecting asks for a written reason (required by policy). The employee receives it, and the chain ends.
- The employee is notified at each change: sent to manager, approved, rejected.
- Duplicate clicks, clicks after another admin decided, and clicks on your own request are refused.
- Each step is stored in `approval_steps` and each transition in `workflow_actions`, both with timestamps. Approvals and rejections are also written to `audit_logs`.
- Admin panel: «📥 کارتابل مرخصی» lists pending requests for your role. «📤 مرخصی‌های ماه جاری / باز» exports to Excel.

## Food

Employee sub-menu: منوی این هفته · سفارش امروز · سفارش مهمان · گزارش غذای این هفته · صورت‌حساب من · بازگشت. Users with food admin permissions also see «⚙️ مدیریت غذا».

**Order.** Choose a day (still open) → dishes with prices (✅ marks the current choice) → confirm. One meal per person per day by default: a new choice before the deadline replaces the old one, and «لغو سفارش این روز» cancels it. After `ordering_deadline_time` on the day itself, ordering, changing and cancelling are refused.

**Guest order.** Day → dish → guest count (buttons 1–10 or type a number, ≤ `max_guest_count_per_order`) → review with total → confirm. Ordering the same dish again for the same day replaces the count. Guest meals are stored, reported and billed separately from the employee's own meal. They can be cancelled from the weekly report before the deadline.

**Invoice.** This week's lines (own and guest), totals, and the payment details from `payment.*`.

### Admin

«📤 آپلود/به‌روزرسانی منوی هفته» → send the `.xlsx` → validation errors (with row numbers) or a preview listing each day's dishes. The preview also warns about replacing an existing version, orders on removed dishes and price changes → confirm → a new menu version. «📄 دریافت قالب اکسل منو» sends a ready template for next week.

«📣 اطلاع‌رسانی ثبت غذا» → recipient count → confirm → sent in the background in rate-limited batches with a «🍽 ثبت سفارش غذا» button. Re-sending the same menu version only reaches people who did not get it. The admin receives a delivery summary.

Reports: «📊 گزارش این هفته / هفته بعد» (per day: portions per dish, guests, total) · «📥 فایل سفارش‌ها» (detailed Excel for this week and next) · «🧾 صورت‌حساب‌ها» (per-employee weekly invoice Excel, which also refreshes `food_weekly_summaries`).

## Admin panel

| Button | Permission | Action |
|---|---|---|
| حضور امروز / هفته جاری / ماه جاری / ماه قبل | `attendance.report_all` | Summary message + detailed Excel |
| بازه‌ی دلخواه | `attendance.report_all` | Start/end Jalali calendar → report |
| گزارش یک کارمند | `attendance.report_all` | Pick an employee → week/month/previous month/range |
| کارتابل مرخصی | `leave.approve_step_1/2` | Pending requests with approval buttons |
| مرخصی‌های ماه جاری / باز | `leave.report_all` | Excel export |
| کاربران و نقش‌ها | `users.view` (edit: `users.manage_roles`) | List → user card → toggle roles / activate. The user is notified |
| وضعیت سیستم | `system.health` | Database/Redis status and server time |
