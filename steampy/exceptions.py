class SteampyException(Exception):
    """All the exception of this library should extend this."""
    pass


class TradeHoldException(SteampyException):
    pass


class TooManyRequests(SteampyException):
    pass


class ApiException(SteampyException):
    pass


class LoginRequired(SteampyException):
    pass


class CaptchaRequired(SteampyException):
    pass


class ConfirmationExpected(SteampyException):
    pass


class LoginException(SteampyException):
    pass


class InvalidCredentials(LoginException):
    pass


class SteamServerError(SteampyException):
    pass


class ParameterError(SteampyException, ValueError):
    pass
