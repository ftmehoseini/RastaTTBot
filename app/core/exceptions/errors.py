"""Application errors. `user_message` is Persian and safe to show; `str(exc)` is for logs."""


class AppError(Exception):
    default_user_message = "خطایی رخ داد. لطفاً دوباره تلاش کنید."

    def __init__(self, user_message: str | None = None, *, log_message: str | None = None):
        self.user_message = user_message or self.default_user_message
        super().__init__(log_message or self.user_message)


class ValidationError(AppError):
    default_user_message = "اطلاعات واردشده معتبر نیست."


class ConflictError(AppError):
    """The action collides with existing data (duplicate, overlap, already decided)."""

    default_user_message = "این درخواست قبلاً ثبت یا پردازش شده است."


class NotFoundError(AppError):
    default_user_message = "مورد درخواستی پیدا نشد."


class PermissionDeniedError(AppError):
    default_user_message = "شما اجازه‌ی انجام این کار را ندارید."


class ExpiredActionError(AppError):
    default_user_message = "این دکمه منقضی شده است. لطفاً دوباره از منو شروع کنید."


class DeadlinePassedError(AppError):
    default_user_message = "مهلت انجام این کار گذشته است."
