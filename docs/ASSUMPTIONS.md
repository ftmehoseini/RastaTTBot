# Assumptions

These were decided without asking, to keep work moving. Each one is configurable or easy to change; items marked **confirm** need a decision from the organization before launch.

## Platform and repository

- The new system is built in this repository (`ftmehoseini/claudecode`). The original `RastaTTBot` repository was read-only for this work; its data can be imported with `python -m app.cli import-legacy-sqlite`.
- The bot speaks the Telegram Bot API through aiogram 3. Bale (`https://tapi.bale.ai`) and Soroush Plus (`https://api.splus.ir`) are supported by pointing aiogram at their API base URL (`BOT_PLATFORM` / `BOT_API_BASE_URL`). Messages are sent as plain text (no `parse_mode`) because Bale and Soroush handle HTML/Markdown differently. **Confirm** which platform goes to production.
- Long polling is used (no public HTTPS endpoint needed). Webhooks can be added later in `app/presentation/bot/main.py`.

## Users and roles

- Anyone who starts the bot can register by sending their full name and becomes an active `EMPLOYEE`. Set `users.require_admin_activation: true` to keep new users inactive until a super admin activates them. **Confirm.**
- The first super admin is created from `BOOTSTRAP_SUPER_ADMIN_IDS` the first time that user starts the bot, or with `python -m app.cli create-super-admin`.
- `SUPER_ADMIN` has every permission. Admin roles do not imply `EMPLOYEE`; give both roles to admins who also record their own attendance (the bot assigns `EMPLOYEE` to everyone at registration).

## Attendance

- One attendance record per user per **local work date** (the Tehran date of the check-in). A check-out after midnight belongs to the previous day's record if it is within `attendance.max_shift_hours` (default 16).
- Live check-in and check-out ask for confirmation. If the confirmation is pressed several minutes later, the time saved is the time **shown in the confirmation**, as long as it is no older than `attendance.live_confirmation_ttl_seconds` (default 300). After that the user must start again.
- Workdays are Saturday–Thursday (`attendance.workdays`). On non-workdays and configured holidays the required work is 0 and every worked minute is overtime; reminders are not sent.
- The default calculation policy is exactly the formula in the brief (`standard_deduction`). Overtime is rounded **down** to `overtime_rounding_minutes`. Late/early minutes are not rounded.
- Manual corrections are applied immediately (`attendance.manual_correction_requires_approval: false`). The data model and service already support a `PENDING_APPROVAL` status. **Confirm** whether managers must approve them.
- The forgotten-entry dialog enters one time at a time (ورود or خروج); a day where both were forgotten is two entries, check-in first.
- Floating check-in: arriving inside the window (08:00–09:30) is not deducted, but worked time still counts against the 8:48 day, so it can leave a deficit (کسری) if the person does not stay later.
- Hourly staff have no fixed hours, so lateness, early departure, overtime, deficit and the monthly caps are not computed for them.

## Leave

- Two-step approval: `ADMIN_1_SECRETARY` then `ADMIN_2_MANAGER`. Any user holding the step's role may decide that step; every holder is notified. A user cannot approve their own request.
- Leave requests can be for past dates up to `leave.max_past_days` (default 7) and future dates up to `leave.max_future_days` (default 90). **Confirm.**
- Daily leave counts calendar days including non-workdays (`leave.count_non_workdays_in_daily_leave: true`). Hourly leave must start and end on the same day.
- The employee can cancel their own request while it is still pending.
- `leave_balances` exists but no balance rule is enforced yet (no policy was given).

## Food

- Menus are uploaded per Jalali week (Saturday–Friday). All rows of one file must fall in the same week.
- Uploading a menu for a week that already has one creates a new **version**. Existing orders are kept with the food name and price they were ordered at. If an ordered dish disappears from the new menu, the admin sees how many orders are affected in the preview and must confirm; those orders remain valid and are listed in reports.
- One employee meal per person per day (`food.max_items_per_employee_order: 1`). Ordering closes at `food.ordering_deadline_time` on the meal day itself. Guest orders follow the same deadline unless `food.guest_ordering_deadline_time` is set.
- Prices are whole Toman. Payment is outside the bot; the bot only shows the Sheba number and message from configuration.

## Operations

- Timestamps are stored in UTC (`timestamptz`). Every business date is the Tehran local date.
- Reports and uploads are written to `REPORTS_DIR` / `UPLOAD_DIR` temporarily and deleted after sending; a daily cleanup task removes anything older than `reports.retention_hours`.
- Official Iranian holidays are not built in. Add them as Jalali dates in `organization.holidays`.
- Attendance figures follow the organization's Excel timesheet formulas. On days off (Thursday, Friday, Iran's official holidays, or any day outside a person's own workdays) all presence is overtime; this is done by the inputs E = F = G = check-in, not by changing the formulas. Rows where the workbook has hours typed over a formula can't be reproduced.
- Lunar holidays after the confirmed part of the official calendar are estimates (marked «تخمینی»); `organization.holiday_exceptions` / `holidays` correct a wrong one.
- کارکرد نهایی uses the newest sheet's formula (آذر: + surplus × 0.8 − uncompensated × 1.6); مهر and آبان had the version without the factors. Set both factors to 0 for that version.
- «مرخصی مجاز» (the leave allowance carried between months) is not tracked by the bot; the cell is left for the admin.
- A day of approved paid daily leave counts 8:48 in «مرخصی با حقوق»; days are counted as `leave.count_non_workdays_in_daily_leave` says.
- The staff list number is a mobile number or a personnel code; mobile numbers in any format are stored as 09xxxxxxxxx.
