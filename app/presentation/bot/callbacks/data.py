"""Inline-button payloads. Kept short (64-byte limit) and free of personal data: only opaque IDs,
dates as ordinals and short action codes. Anything sensitive stays server-side in FSM storage."""

from aiogram.filters.callback_data import CallbackData

# Plain strings used by background jobs (which must not import the presentation layer).
OPEN_ATTENDANCE = "open:att"
OPEN_FOOD = "open:food"
NOOP = "noop"


class NavCb(CallbackData, prefix="nav"):
    to: str  # main | att | leave | food | admin


class AttCb(CallbackData, prefix="att"):
    a: str  # in | out | in_ok | out_ok | status | week | week_xls | forgot
    ts: int = 0  # UTC epoch seconds of the previewed time
    d: int = 0  # work date ordinal (check-out of a night shift)


class CorrCb(CallbackData, prefix="cor"):
    a: str  # kind | ok
    k: str = ""  # in | out | both


class CalCb(CallbackData, prefix="cal"):
    p: str  # purpose, e.g. corr | lv_s | lv_e | rep_s | rep_e
    a: str  # d (pick day) | m (show month)
    v: int  # date ordinal for "d"; year*100+month for "m"


class TimeCb(CallbackData, prefix="tm"):
    p: str  # purpose, e.g. corr_in | corr_out | lv_s | lv_e
    a: str  # h (hour picked) | m (minute picked)
    h: int = 0
    m: int = 0


class LeaveCb(CallbackData, prefix="lv"):
    a: str  # new | pay | unit | skip_reason | submit | mine | status | view | cancel
    v: str = ""


class ApprovalCb(CallbackData, prefix="ap"):
    w: str  # workflow code: lv
    e: str  # entity id (hex)
    s: int  # step order
    a: str  # y (approve) | n (reject)


class FoodCb(CallbackData, prefix="fd"):
    a: str  # menu | today | day | item | ok | cancel | guest | g_day | g_item | g_cnt | g_ok | g_del | week | invoice
    v: str = ""


class FoodAdminCb(CallbackData, prefix="fa"):
    a: str  # upload | template | confirm | discard | notify | notify_ok | summary | orders_xls | invoices
    v: str = ""


class AdminCb(CallbackData, prefix="adm"):
    a: str
    v: str = ""


class UserAdminCb(CallbackData, prefix="ua"):
    a: str  # list | view | role | active
    u: str = ""  # user id hex
    r: str = ""  # role code
    p: int = 0  # page or on/off flag
