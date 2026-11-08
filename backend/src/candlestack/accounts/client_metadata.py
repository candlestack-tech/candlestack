"""What an OAuth client says about itself, and where the browser may return to it.

A client registers itself (RFC 7591) or names itself by the https URL of its client ID metadata
document, which the server fetches. That fetch reaches a URL anyone can choose, so it is held
tight: https only, an address that resolves to the public internet only (never loopback, a
private network, link-local or the cloud's metadata address), connected to the very address that
was checked (no second lookup another answer could slip into), no redirects, at most
``DOCUMENT_MAX_BYTES`` in ``FETCH_SECONDS``.

Redirect URIs are https URLs, or http on a loopback address for a native app (RFC 8252), and
must match one the client registered exactly; on a loopback address the port may differ, as a
native app listens on whatever port is free.
"""

import asyncio
import ipaddress
import json
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import SplitResult, urlsplit

import httpx
from pydantic import ValidationError

from candlestack.accounts.errors import OAuthError
from candlestack.accounts.schemas import ClientMetadataIn, TokenEndpointAuthMethod

# The longest client ID metadata document read, and how long its fetch may take in all.
DOCUMENT_MAX_BYTES = 16 * 1024
FETCH_SECONDS = 5.0
URI_MAX = 512
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_GRANT_TYPES = frozenset({"authorization_code", "refresh_token"})

# Resolves a host and port to the IP addresses it names; replaced by the tests.
Resolver = Callable[[str, int], Awaitable[list[str]]]


class ClientDocumentFailed(Exception):
    """The client ID metadata document cannot be used: unreachable, too large, not public, or
    not a valid document of that URL. ``reason`` is for the log and the user."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _split(uri: str) -> SplitResult | None:
    """`uri` split, or None when it is no valid URL (a bad port, say)."""
    try:
        parts = urlsplit(uri)
        _ = parts.port
    except ValueError:
        return None
    return parts


def _is_loopback(parts: SplitResult) -> bool:
    return parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS


def redirect_uri_problem(uri: str) -> str | None:
    """Why `uri` cannot be a redirect URI; None when it can."""
    parts = _split(uri)
    if parts is None or len(uri) > URI_MAX or not parts.hostname:
        return f"{uri!r} is not a valid URL of at most {URI_MAX} characters"
    if "#" in uri or parts.username is not None or parts.password is not None:
        return f"{uri!r} must have no fragment and no user name or password"
    if parts.scheme != "https" and not _is_loopback(parts):
        return f"{uri!r} must be https, or http on a loopback address (127.0.0.1, [::1], localhost)"
    return None


def redirect_uri_matches(registered: list[str], given: str) -> bool:
    """Whether `given` is one of the `registered` redirect URIs, exactly, or on a loopback
    address with another port (RFC 8252, section 7.3)."""
    if given in registered:
        return True
    parts = _split(given)
    if parts is None or not _is_loopback(parts):
        return False

    def without_port(p: SplitResult) -> tuple[str, str | None, str, str]:
        return p.scheme, p.hostname, p.path, p.query

    for uri in registered:
        other = _split(uri)
        if other is not None and _is_loopback(other) and without_port(other) == without_port(parts):
            return True
    return False


def _https_url(uri: str | None) -> bool:
    parts = None if uri is None else _split(uri)
    return parts is not None and parts.scheme == "https" and bool(parts.hostname)


def check_metadata(metadata: ClientMetadataIn) -> None:
    """Checks what the server keeps of a client's metadata.

    Raises:
        OAuthError: ``invalid_redirect_uri`` for a redirect URI that breaks the rules,
            ``invalid_client_metadata`` for anything else.
    """
    for uri in metadata.redirect_uris:
        problem = redirect_uri_problem(uri)
        if problem is not None:
            raise OAuthError("invalid_redirect_uri", problem)
    if "authorization_code" not in metadata.grant_types or not _GRANT_TYPES.issuperset(
        metadata.grant_types
    ):
        raise OAuthError(
            "invalid_client_metadata",
            "grant_types must name authorization_code, and refresh_token at most besides",
        )
    if metadata.response_types != ["code"]:
        raise OAuthError("invalid_client_metadata", 'response_types must be ["code"]')
    for name in ("client_uri", "logo_uri"):
        value = getattr(metadata, name)
        if value is not None and not _https_url(value):
            raise OAuthError("invalid_client_metadata", f"{name} must be an https URL")


def parse_metadata(body: object) -> ClientMetadataIn:
    """The client metadata in `body`, checked.

    Raises:
        OAuthError: ``invalid_client_metadata`` or ``invalid_redirect_uri``.
    """
    try:
        metadata = ClientMetadataIn.model_validate(body)
    except ValidationError as error:
        detail = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'body'}: {item['msg']}"
            for item in error.errors(include_url=False, include_input=False)
        )
        raise OAuthError("invalid_client_metadata", detail) from None
    check_metadata(metadata)
    return metadata


def is_document_url(client_id: str) -> bool:
    """Whether `client_id` names a client ID metadata document (an https URL)."""
    return client_id.startswith("https://")


def _document_parts(url: str) -> SplitResult:
    """`url` split, when it may name a client ID metadata document: https, a host, a path
    other than ``/``, no fragment, user name, password or dot segments.

    Raises:
        ClientDocumentFailed: It may not.
    """
    parts = _split(url)
    if (
        parts is None
        or len(url) > URI_MAX
        or parts.scheme != "https"
        or not parts.hostname
        or parts.path in ("", "/")
        or "#" in url
        or parts.username is not None
        or parts.password is not None
        or any(segment in (".", "..") for segment in parts.path.split("/"))
    ):
        raise ClientDocumentFailed("the client ID is not a valid https URL with a path")
    return parts


async def _resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def _document(raw: bytes, url: str) -> ClientMetadataIn:
    """The metadata of the document `raw` fetched from `url`.

    Raises:
        ClientDocumentFailed: It is no JSON object of that client, asks for a secret, or its
            metadata breaks the rules.
    """
    try:
        body = json.loads(raw)
    except ValueError:
        raise ClientDocumentFailed("the document is not JSON") from None
    if not isinstance(body, dict) or body.get("client_id") != url:
        raise ClientDocumentFailed("the document's client_id is not its URL")
    if "client_secret" in body:
        raise ClientDocumentFailed("a client ID metadata document must not hold a secret")
    # A document's client has no secret: none is the only method it can use.
    body.setdefault("token_endpoint_auth_method", TokenEndpointAuthMethod.none.value)
    try:
        metadata = parse_metadata(body)
    except OAuthError as error:
        raise ClientDocumentFailed(error.description) from None
    if metadata.token_endpoint_auth_method is not TokenEndpointAuthMethod.none:
        raise ClientDocumentFailed("a document's token_endpoint_auth_method must be none")
    return metadata


class ClientDocuments:
    """Fetches client ID metadata documents through the app's HTTP client."""

    def __init__(self, http: httpx.AsyncClient, resolve: Resolver | None = None) -> None:
        self._http = http
        self._resolve = resolve or _resolve

    async def fetch(self, url: str) -> ClientMetadataIn:
        """The client metadata the document at `url` holds, checked.

        Raises:
            ClientDocumentFailed: The URL or its address is not allowed, the fetch failed or
                took too long, or the document is not valid.
        """
        parts = _document_parts(url)
        host = parts.hostname
        assert host is not None  # _document_parts
        port = parts.port or 443
        try:
            async with asyncio.timeout(FETCH_SECONDS):
                address = await self._public_address(host, port)
                raw = await self._get(parts, address, port)
        except TimeoutError:
            raise ClientDocumentFailed("fetching the document took too long") from None
        except (httpx.HTTPError, OSError) as error:
            raise ClientDocumentFailed(
                f"the document is unreachable ({type(error).__name__})"
            ) from None
        return _document(raw, url)

    async def _public_address(self, host: str, port: int) -> str:
        """The address to connect to: the first of `host`'s, when all of them are public."""
        addresses = await self._resolve(host, port)
        if not addresses or not all(_is_public(address) for address in addresses):
            raise ClientDocumentFailed("the document's host is not on the public internet")
        return addresses[0]

    async def _get(self, parts: SplitResult, address: str, port: int) -> bytes:
        """The body at `parts`, from `address`: the TLS handshake and its certificate check use
        the URL's host name, while the connection goes to the address that was checked."""
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        netloc = f"[{ip}]" if ip.version == 6 else str(ip)
        if port != 443:
            netloc = f"{netloc}:{port}"
        target = f"https://{netloc}{parts.path}" + (f"?{parts.query}" if parts.query else "")
        async with self._http.stream(
            "GET",
            target,
            headers={"Host": parts.netloc, "Accept": "application/json"},
            extensions={"sni_hostname": parts.hostname},
            follow_redirects=False,
            timeout=httpx.Timeout(FETCH_SECONDS),
        ) as response:
            if response.status_code != 200:
                raise ClientDocumentFailed(
                    f"the document's URL answered HTTP {response.status_code}"
                )
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > DOCUMENT_MAX_BYTES:
                    raise ClientDocumentFailed(
                        f"the document is larger than {DOCUMENT_MAX_BYTES} bytes"
                    )
        return bytes(body)
