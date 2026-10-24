from aiogram.fsm.state import State, StatesGroup


class Registration(StatesGroup):
    full_name = State()


class Correction(StatesGroup):
    choosing_date = State()
    choosing_kind = State()  # check-in or check-out
    typing_time = State()  # typed HH:MM
    confirming = State()


class LeaveFlow(StatesGroup):
    choosing = State()  # pay type / unit / dates / times via buttons
    typing_time = State()
    typing_reason = State()
    confirming = State()


class ApprovalFlow(StatesGroup):
    typing_rejection_reason = State()


class GuestFlow(StatesGroup):
    typing_count = State()


class MenuUpload(StatesGroup):
    waiting_file = State()
    confirming = State()


class ReportRange(StatesGroup):
    choosing = State()
