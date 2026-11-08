"""Cross-site request forgery protection for the requests a session cookie authenticates.

SameSite=Lax keeps the session cookie off requests that other sites start, except top-level
navigations, but every host under candlestack.tech is the same site to the browser, so a page on any
of them could post to prod with the cookie. So every route that changes something on behalf of
the signed-in user requires the request to come from the app's own origin, by the browser's
``Origin`` header or, without one, ``Sec-Fetch-Site: same-origin``:
``@router.post(..., dependencies=[Depends(require_same_origin)])``. A GET that starts a change
(a link of the app) checks ``is_same_origin`` itself: a navigation sends no ``Origin``, but
``Sec-Fetch-Site``.

A request with an API key (``Authorization: Bearer ...``) and no cookie at all skips the check:
nothing the browser adds by itself signs it in, and another site cannot make a browser send an
``Authorization`` header (that needs CORS, which the API does not allow). With any cookie the
check stays, so a cookie never authenticates a request that skipped it.
"""

from fastapi import Request
from fastapi.security.utils import get_authorization_scheme_param

from candlestack.core.config import Settings, SettingsDep
from candlestack.core.errors import ProblemError


def bearer_token(request: Request) -> str | None:
    """The token of the request's ``Authorization: Bearer <token>`` header, else None."""
    scheme, token = get_authorization_scheme_param(request.headers.get("authorization"))
    return token if scheme.lower() == "bearer" and token else None


def is_same_origin(request: Request, settings: Settings) -> bool:
    """Whether the request comes from a page of ``PUBLIC_URL``."""
    origin = request.headers.get("origin")
    if origin is not None:
        return origin == settings.public_url
    return request.headers.get("sec-fetch-site") == "same-origin"


def require_same_origin(request: Request, settings: SettingsDep) -> None:
    """403 ``cross-origin-request`` unless the request comes from a page of ``PUBLIC_URL`` or
    is signed in with an API key and carries no cookie."""
    with_key = bearer_token(request) is not None
    if with_key and "cookie" not in request.headers:
        return
    if not is_same_origin(request, settings):
        detail = (
            "This request changes your account, so it is accepted only from the CandleStack app "
            f"at {settings.public_url}. Open the app there and try again."
        )
        if with_key:
            detail = (
                "A request with an API key must not send cookies: it came with both, so it is "
                f"accepted only from the CandleStack app at {settings.public_url}. Send the key "
                "alone."
            )
        raise ProblemError(403, "cross-origin-request", "Cross-origin request", detail)
