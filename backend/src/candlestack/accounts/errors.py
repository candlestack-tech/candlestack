"""Errors of the accounts service. They know nothing about HTTP: the API turns a failed sign-in
or connect into a redirect (``/signin?error=<code>`` or ``/settings/sign-in?error=<code>``) and
the others into problems."""


class SignInFailed(Exception):
    """The sign-in cannot go on: cancelled, refused or broken at the provider, a state that
    does not match, a provider that is not configured. ``reason`` is for the log and never
    holds a secret or personal data; ``code`` is the ``error`` the page it returns to shows.
    ``next_path`` is the page a sign-in was going to, once its state is known."""

    code = "failed"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.next_path: str | None = None


class SignInMethodTaken(SignInFailed):
    """The account to connect already signs in to another user."""

    code = "taken"

    def __init__(self) -> None:
        super().__init__("the account signs in to another user")


class SignInDenied(Exception):
    """The provider vouched for the account, but its email is not on the beta list. The
    account waits in Redis under ``denial_id`` for the sign-in page to name it.
    ``next_path`` is the page the sign-in was going to."""

    def __init__(self, denial_id: str) -> None:
        super().__init__("account not on the beta list")
        self.denial_id = denial_id
        self.next_path: str | None = None


class NoSuchSignInMethod(Exception):
    """The user does not sign in with the provider."""


class LastSignInMethod(Exception):
    """Removing the sign-in method would leave the user no way to sign in."""


class TooManyApiKeys(Exception):
    """The user has as many keys as allowed that are neither revoked nor expired."""


class NoSuchApiKey(Exception):
    """The user has no key with the ID that is not revoked."""


class OAuthError(Exception):
    """An OAuth error the token, registration and revocation endpoints answer as JSON
    (``{"error", "error_description"}``, RFC 6749 section 5.2). ``description`` never holds a
    secret or personal data."""

    def __init__(self, error: str, description: str, *, status: int = 400) -> None:
        super().__init__(description)
        self.error = error
        self.description = description
        self.status = status


class AuthorizeRefused(Exception):
    """The authorize request names no client the server knows or a redirect URI the client did
    not register (RFC 6749 section 4.1.2.1), or it is not valid. The browser gets a problem
    instead of going back to the client: before the user acted, nothing proves the redirect URI
    is not an attacker's (RFC 9700 section 4.11.2). ``reason`` tells the user what is wrong,
    ``error`` is the OAuth error code."""

    def __init__(self, reason: str, *, error: str = "invalid_request") -> None:
        super().__init__(reason)
        self.reason = reason
        self.error = error


class NoSuchAuthorizationRequest(Exception):
    """No authorization request has the ID: it was decided already, expired or never was."""


class ScopeNotRequested(Exception):
    """The consent grants a scope the app did not ask for."""

    def __init__(self, scope: str) -> None:
        super().__init__(scope)
        self.scope = scope


class NotAdmitted(Exception):
    """The signed-in user's email is not on the beta list (any more)."""


class NoSuchConnectedApp(Exception):
    """The user has no connected app with the ID."""
