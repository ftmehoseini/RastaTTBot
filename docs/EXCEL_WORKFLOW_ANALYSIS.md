# Excel-Based Attendance Workflow: Analysis

| | |
|---|---|
| Status | **Analysis only (Stages 1–2). No code has been changed.** Stage 3 (the change proposal) waits for answers to §7. |
| Based on | commit `56ebcb2` and two sample workbooks from the organization (form A and form B). Neither workbook is stored in the repository; they contain personal data. |
| Related | [ENGINEERING_AUDIT.md](../ENGINEERING_AUDIT.md) |

## 1. Requested workflow

1. The admin keeps Excel templates with the organization's formulas.
2. Employees send check-in and check-out times through the bot.
3. The bot writes them into fixed input cells, and the **Excel formulas** calculate everything: working hours, overtime, lateness, early departure, deficit and monthly totals.
4. The bot reads the result cells and sends a Telegram report, only to authorized recipients.

## 2. How the repository works today

- **The database is the source of truth.** `AttendanceService.check_in` / `check_out` (`app/application/services/attendance_service.py`) validate and save one row per person per day in `attendance_records`. A unique constraint and a row lock prevent duplicates. Every change is written to the audit log.
- **The bot calculates in Python.** `app/domain/attendance/timesheet.py` reproduces form A's formulas. The result is stored in `attendance_daily_summaries` and shown right after check-in by `presenters/attendance.py:punch_saved`.
- **Excel is output only.** `app/infrastructure/excel/timesheet.py:build_timesheet_workbook` writes the inputs (A–I, P) plus form A's formulas exactly as they appear (J–O, the summary block). The file is set to recalculate when opened. It is never calculated on the server, and nothing reads results back from it.
- **LibreOffice is used only in a test.** `app/tests/test_timesheet_excel.py::test_libreoffice_recalculation_matches_the_bot` recalculates a generated workbook headless and compares it with the Python numbers. It passes on LibreOffice 24.2.
- **One set of rules for everyone.** E/F/G (08:00 / 09:30 / 16:48), the monthly caps and the 0.8/1.6 factors come from `config/default.yaml` (`ScheduleResolver.for_day` in `app/domain/attendance/calculation.py`). There are no per-person or per-day values.
- **No admin upload of attendance templates.** The only Excel uploads are the staff list (`handlers/admin.py:roster_upload_file`) and the food menu (`handlers/food_admin.py:upload_file`).
- **Docker:** `python:3.12-slim` image, **without LibreOffice**.

## 3. What the sample workbooks show

### 3.1 Two calculation forms with the same daily formulas

| Part | Form A (12-month file) | Form B |
|---|---|---|
| Daily J ساعت مفید, K کارکرد عادی, L تاخیر مجاز, M تاخیر غیر مجاز, N تعجیل, overtime | identical | identical |
| Allowed exit (G) | 16:48 | 18:00 |
| Column I | مرخصی **با** حقوق (paid; summed into the totals) | مرخصی **بدون** حقوق (unpaid; **no formula reads it**) |
| Extra daily column | — | O کسر روزانه = M + N |
| Monthly caps on lateness / early departure | yes (values change over time and per person) | no |
| کارکرد نهایی (final work) | B + MIN(overtime, compensable) + paid leave + surplus×0.8 − uncompensated×1.6 (the factors are missing in some months) | normal work − (MAX(allowed late − overtime, 0) + monthly deficit). The surplus is calculated but **not added**. |

### 3.2 "Per-person rules" are inputs, not different formulas

| Rule | How the admin enters it today |
|---|---|
| Personal hours | the default E (start), F (end of the late-arrival window), G (allowed exit) |
| No allowed lateness | F = E |
| Hourly leave at the start of the day | that day's E (and F) moved to the return time, leave length in I |
| Hourly leave at the end of the day | that day's G moved to the leave start, leave length in I |
| Manager exception | that day's F moved |
| Official holiday on a workday (form A) | K typed as 8:48 over the formula |
| Second visit the same day | the overtime cell typed over the formula |
| Monthly caps | typed into the cap cells; 0:00 for some people |
| Leave allowance | carried from the previous month's sheet, or typed in |

### 3.3 LibreOffice works as the engine for both forms

- **Form A:** recalculated headless, **1,764 of 1,764 formula cells match the values Excel saved, with 0 errors**. This includes the structured table references and the links between monthly sheets. LibreOffice re-saves the file in its own way, so it must work on a copy and never overwrite the admin's original.
- **Form B:** times were written into a copy with openpyxl (the table and formulas were preserved), the copy was recalculated with LibreOffice, and the results were read back. Every daily and monthly value matched a hand calculation.

### 3.4 Critical: the bot's current numbers differ from form A

Putting a real employee's check-ins and check-outs through the bot's current global rules gives different day values on **up to 12 days a month**, and a different کارکرد نهایی in 7 of 9 months. The causes:

1. Holidays on workdays are not credited 8:48.
2. Hourly leave doesn't move E/F/G, so the leave is credited in I **and** also counted as lateness or early departure.
3. A second session on the same day isn't supported.
4. Caps and factors are global, but they vary in the organization's files.

This is reported only. It is not fixed (out of scope until approved).

## 4. Options considered

| Option | Verdict |
|---|---|
| 1. A settings file per employee, with the Python calculation | Right for parameters, but every new form needs new Python code, which is the "several functions" problem. |
| 2. Per-person Excel files on the server, edited in place | Works technically, but the admin still types the daily adjustments, and the bot and admin write the same files. That brings concurrency, corruption and versioning risks, with N files × 12 sheets to maintain. |
| **3. Recommended: form templates + per-person settings + automatic daily adjustments, with LibreOffice as the engine** | No formulas in the code; a new form means uploading a template. See §5. |

## 5. Recommended architecture

```
FORM     admin-uploaded template (one month sheet with its formulas), validated and versioned.
         Cells are found by header/label text («ساعت ورود», «ساعت خروج», «شروع شناوری», … «کارکرد نهایی:»),
         with an optional explicit cell map when the labels differ.
PERSON   form + settings: E/F/G, caps, holiday credit, workdays, opening leave balance.
DAY      check-in/out from the database + adjustments generated from events:
         approved hourly leave → E/F/G shift + I · holiday → K credit · second session → overtime.
ENGINE   (Celery worker) copy the template → write the inputs → LibreOffice headless (separate profile per run)
         → read the result cells → check them (no #VALUE!/#REF!, right types, the name and month match)
         → Telegram message (configurable text) + .xlsx, to the employee only (and to authorized admins).
         On failure: "saved; report delayed", the admin is alerted, and no possibly wrong figures are sent.
```

What this keeps: the database as the source of truth (duplicates, permissions, audit, backups), the existing check-in/out dialogs and checks, and the existing reports. The Python calculation could still give the instant reply after check-in.

What it requires (each part needs approval): a template upload handler, storage for templates and person settings (**schema change** or files on the data volume), a template filler, a LibreOffice runner, result reading and validation, a configurable message, a Celery task, small changes in the attendance handlers, and **LibreOffice in the `Dockerfile`** (about 400–600 MB).

## 6. Risks

| Risk | Mitigation |
|---|---|
| Two requests at the same time | A fresh copy per job; never write the master file |
| LibreOffice instances running in parallel | Separate `-env:UserInstallation` profile per run; limit worker concurrency |
| Recalculation fails or hangs | Timeout; check the exit code and that the output exists; never send figures from a failed run |
| Formula errors or stale values | Reject results with error values or wrong types; every report is recalculated fresh |
| Template changed by the admin | Versioned uploads; check the required headers and labels on upload |
| Time zone | Times stored in UTC and converted to Tehran time before writing; a check-out after midnight is written as more than 24 h |
| Report latency | About 1 s per LibreOffice run, so the report arrives 2–5 s after check-in |
| Image size and mirrors in Iran | The LibreOffice package may need a local apt mirror |

## 7. Open questions (needed for Stage 3)

1. How many forms exist, and which person uses which?
2. Form B: is column I (unpaid leave) meant to be ignored by the formulas?
3. Form B: should the overtime surplus really be left out of کارکرد نهایی?
4. Form B: how are Thursdays, Fridays and holidays handled? Every row has the same E/F/G, so presence on a day off counts as normal work.
5. Hourly leave: is the convention always "leave at the start moves E/F; leave at the end moves G"? And what does a moved E/F with an empty I mean?
6. Holiday credit of 8:48: for whom, and on which days?
7. Caps: per person, per month, or both?
8. Should the bot allow several check-ins and check-outs per day?
9. Should the bot carry the leave allowance from month to month?
10. Should employees be able to add a note to a day from the bot?
11. Should the instant reply after check-in stay (Python), or wait for the Excel result?
