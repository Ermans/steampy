import requests

from .constants import API_URL
from .utils import handle_steam_response, extract_json
from .guard import load_steam_guard
from .login import LoginExecutor

from .exceptions import SteamServerError, LoginRequired, InvalidCredentials, LoginException


def login_required(func):
    def func_wrapper(self, *args, **kwargs):
        if not self.steam_session._login_executor:
            raise LoginRequired('Use login method first')
        else:
            return func(self, *args, **kwargs)

    return func_wrapper


class SteamSession(requests.Session):
    def __init__(self):
        super().__init__()
        self._login_executor = None  # type: LoginExecutor

        self.steam_guard = {}
        self.steam_id = None  # type: str
        self.api_key = None  # type: str

    def login(self, username: str, password: str, steam_guard: str) -> None:
        self.steam_guard = load_steam_guard(steam_guard)
        self._login_executor = LoginExecutor(username, password, self.steam_guard['shared_secret'], self)
        self._login()

    @login_required
    def relogin(self):
        self._login()

    def _login(self) -> None:
        try:
            login_response_dict = self._login_executor.login()
            self.steam_id = login_response_dict["steamid"]
        except InvalidCredentials as e:
            raise e
        except Exception as e:
            raise LoginException("Something bad occured") from e

    def api_call(self, request_method: str, interface: str, api_method: str, version: str,
                 params: dict = None) -> dict:
        # if not self.api_key:
        #     raise ParameterError("No Steam API key provided")

        url = "/".join([API_URL, interface, api_method, version])
        params = params or {}
        if self.api_key:
            params["key"] = self.api_key

        if request_method == 'GET':
            response = self.get(url, params=params)
        else:
            response = self.post(url, data=params)

        if "Please verify your <pre>key=</pre> parameter" in response.text:
            raise InvalidCredentials("Invalid Steam API key")

        handle_steam_response(response)
        response_json = extract_json(response)
        return response_json

    def post(self, url, data=None, json=None, **kwargs) -> requests.Response:
        """ Same of requests.post(...) """
        try:
            return super().post(url, data=data, json=json, **kwargs)
        except requests.exceptions.RequestException as e:
            raise SteamServerError() from e

    def get(self, url, **kwargs) -> requests.Response:
        """ Same of requests.get(...) """
        try:
            return super().get(url, **kwargs)
        except requests.exceptions.RequestException as e:
            raise SteamServerError() from e

    def head(self, url, **kwargs) -> requests.Response:
        """ Same of requests.head(...) """
        try:
            return super().head(url, **kwargs)
        except requests.exceptions.RequestException as e:
            raise SteamServerError() from e

    def __getstate__(self):
        state = super().__getstate__()
        for x, v in self.__dict__.items():
            if x not in state:
                state[x] = v
        return state


