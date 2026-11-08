"""The cookies of signing in: the session, the state of a sign-in in progress, the ID of a
denied sign-in, and the browser an MCP client's authorization request started in.

All are HttpOnly (no script reads them), SameSite=Lax (sent when the provider redirects back,
not with requests other sites start) and for the whole host (Path=/). Over https they are
``Secure`` and their names carry the ``__Host-`` prefix, which keeps any other host under
candlestack.tech from setting or overriding them.
"""

from typing import Annotated, Literal

from fastapi import Depends, Request, Response

from candlestack.core import SettingsDep

CookieName = Literal["session", "sign_in_state", "sign_in_denied", "oauth_request"]
SESSION: CookieName = "session"
STATE: CookieName = "sign_in_state"
DENIED: CookieName = "sign_in_denied"
OAUTH_REQUEST: CookieName = "oauth_request"


class Cookies:
    def __init__(self, public_url: str) -> None:
        self._secure = public_url.startswith("https://")

    def _name(self, name: CookieName) -> str:
        return f"__Host-{name}" if self._secure else name

    def read(self, request: Request, name: CookieName) -> str | None:
        return request.cookies.get(self._name(name)) or None

    def set(self, response: Response, name: CookieName, value: str, max_age: int) -> None:
        response.set_cookie(
            self._name(name),
            value,
            max_age=max_age,
            path="/",
            secure=self._secure,
            httponly=True,
            samesite="lax",
        )

    def delete(self, response: Response, name: CookieName) -> None:
        response.delete_cookie(
            self._name(name), path="/", secure=self._secure, httponly=True, samesite="lax"
        )


def get_cookies(settings: SettingsDep) -> Cookies:
    return Cookies(settings.public_url)


CookiesDep = Annotated[Cookies, Depends(get_cookies)]
