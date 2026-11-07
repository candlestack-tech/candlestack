"""Errors of the accounts service. They know nothing about HTTP: the API turns a failed sign-in
or connect into a redirect (``/signin?error=<code>`` or ``/settings/sign-in?error=<code>``) and
the others into problems."""


class SignInFailed(Exception):
    """The sign-in cannot go on: cancelled, refused or broken at the provider, a state that
    does not match, a provider that is not configured. ``reason`` is for the log and never
    holds a secret or personal data; ``code`` is the ``error`` the page it returns to shows."""

    code = "failed"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SignInMethodTaken(SignInFailed):
    """The account to connect already signs in to another user."""

    code = "taken"

    def __init__(self) -> None:
        super().__init__("the account signs in to another user")


class SignInDenied(Exception):
    """The provider vouched for the account, but its email is not on the beta list. The
    account waits in Redis under ``denial_id`` for the sign-in page to name it."""

    def __init__(self, denial_id: str) -> None:
        super().__init__("account not on the beta list")
        self.denial_id = denial_id


class NoSuchSignInMethod(Exception):
    """The user does not sign in with the provider."""


class LastSignInMethod(Exception):
    """Removing the sign-in method would leave the user no way to sign in."""


class TooManyApiKeys(Exception):
    """The user has as many keys as allowed that are neither revoked nor expired."""


class NoSuchApiKey(Exception):
    """The user has no key with the ID that is not revoked."""
