"""Public-only source HTTP with pinned addresses and per-invocation limits."""

from __future__ import annotations

import ipaddress
import queue
import socket
import ssl
import threading
import time
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

import httpcore
import httpx

from changelorg.enrichment import PROFILES
from changelorg.models import SourceCreate

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
MAX_REQUESTS = 12
MAX_ENTRIES = 200
TOTAL_TIMEOUT = 60.0
IO_TIMEOUT = 20.0

# Python 3.12 misclassifies some special-purpose ranges. Reject translation routes too, since they can reach private IPv4.
_SPECIAL_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "192.0.0.0/24",
        "192.88.99.0/24",
        "64:ff9b::/96",
        "64:ff9b:1::/48",
        "2001::/23",
        "2002::/16",
    )
)
_DNS_SLOTS = threading.BoundedSemaphore(16)
SOURCE_FETCH_SLOTS = threading.BoundedSemaphore(4)


class PublicFetchError(ValueError):
    """Safe source failure; messages contain no remote body, address, or credentials."""


class FetchBudgetExceeded(PublicFetchError):
    """The source deadline or request budget is exhausted."""


class RequestBudgetExceeded(FetchBudgetExceeded):
    """Optional article fetches can stop without discarding completed entries."""


def _public_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise PublicFetchError("Source address is invalid") from None
    if (
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or "%" in value
        or isinstance(address, ipaddress.IPv6Address)
        and (address.ipv4_mapped is not None or address.is_site_local)
        or any(address in network for network in _SPECIAL_NETWORKS)
    ):
        raise PublicFetchError("Source addresses must be public")
    return address


def validate_public_url(
    value: object, *, base_url: httpx.URL | None = None
) -> httpx.URL:
    """Validate structure and literal addresses without DNS. Fetches also validate every DNS answer."""
    if not isinstance(value, str) or not value or len(value) > 2000:
        raise PublicFetchError(
            "Source URL must be a non-empty string of at most 2000 characters"
        )
    if (
        any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in value
        )
        or "\\" in value
    ):
        raise PublicFetchError("Source URL contains invalid characters")
    try:
        parts = urlsplit(value)
        url = httpx.URL(value) if base_url is None else base_url.join(value)
        if (
            url.scheme not in {"http", "https"}
            or base_url is None
            and not parts.netloc
            or not url.host
            or "@" in parts.netloc
            or url.port not in {None, 80, 443}
            or "%" in url.host
        ):
            raise ValueError
    except (ValueError, httpx.InvalidURL):
        raise PublicFetchError(
            "Source URL requires HTTP or HTTPS, port 80 or 443, and no credentials"
        ) from None
    host = url.host.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local")):
        raise PublicFetchError("Source addresses must be public")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        _public_ip(host)
    return url.copy_with(fragment=None)


def validate_source_config(source: SourceCreate) -> None:
    """Validate web source config without network access. Trusted local plugins do not use this entry point."""
    if source.plugin not in {"rss-atom", "html-news", "x"}:
        raise PublicFetchError("Only built-in source plugins are available")
    config = source.config
    validate_public_url(config.get("url"))
    if source.plugin == "x":
        from changelorg.social import x_username

        x_username(validate_public_url(config.get("url")))
    string_keys = {"url", "profile", "user_agent", "enrichment_profile"}
    list_keys = (
        {"include_any", "exclude_any"}
        if source.plugin in {"rss-atom", "x"}
        else {"exclude_path_prefixes", "title_suffixes"}
    )
    allowed_keys = string_keys | list_keys
    if source.plugin == "x":
        allowed_keys -= {"enrichment_profile", "user_agent"}
    if source.plugin == "html-news":
        string_keys.add("article_path_prefix")
        allowed_keys |= {"article_path_prefix", "limit"}
        if "article_path_prefix" not in config:
            raise PublicFetchError("html-news requires article_path_prefix")
    if config.keys() - allowed_keys:
        raise PublicFetchError("Source config contains unsupported keys")
    for key in string_keys & config.keys():
        value = config[key]
        if not isinstance(value, str) or not value.strip():
            raise PublicFetchError(f"Source config {key} must be a non-empty string")
    for key in list_keys & config.keys():
        value = config[key]
        if not isinstance(value, str) and not (
            isinstance(value, list) and all(isinstance(item, str) for item in value)
        ):
            raise PublicFetchError(
                f"Source config {key} must be a string or list of strings"
            )
    if "limit" in config and (
        type(config["limit"]) is not int or not 1 <= config["limit"] <= 100
    ):
        raise PublicFetchError("Source config limit must be an integer from 1 to 100")
    if "enrichment_profile" in config and config["enrichment_profile"] not in PROFILES:
        raise PublicFetchError("Source enrichment profile is unknown")
    _user_agent(config.get("user_agent", "changelorg/0.1"))


def _user_agent(value: str) -> bytes:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 500
        or any(not 32 <= ord(char) < 127 for char in value)
    ):
        raise PublicFetchError(
            "Source user_agent must contain at most 500 printable ASCII characters"
        )
    return value.encode("ascii")


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise FetchBudgetExceeded("Source time budget exhausted")
    return min(IO_TIMEOUT, remaining)


def _resolve(
    host: str, port: int, deadline: float
) -> list[tuple[socket.AddressFamily, str]]:
    # libc DNS has no timeout. Daemon workers bound caller latency; slots bound stalled workers across source invocations.
    if not _DNS_SLOTS.acquire(timeout=_remaining(deadline)):
        raise PublicFetchError("Source DNS capacity exhausted")
    result: queue.Queue[Any] = queue.Queue(maxsize=1)

    def lookup() -> None:
        try:
            result.put(
                socket.getaddrinfo(
                    host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
                )
            )
        except OSError:
            result.put(None)
        finally:
            _DNS_SLOTS.release()

    try:
        threading.Thread(target=lookup, daemon=True).start()
    except RuntimeError:
        _DNS_SLOTS.release()
        raise PublicFetchError("Source DNS capacity exhausted") from None
    try:
        answers = result.get(timeout=_remaining(deadline))
    except queue.Empty:
        _remaining(deadline)
        raise PublicFetchError("Source DNS lookup timed out") from None
    _remaining(deadline)
    if not answers:
        raise PublicFetchError("Source DNS lookup failed")
    addresses: list[tuple[socket.AddressFamily, str]] = []
    for family, _, _, _, sockaddr in answers:
        address = _public_ip(sockaddr[0])
        if family not in {socket.AF_INET, socket.AF_INET6} or (
            family == socket.AF_INET
        ) != (address.version == 4):
            raise PublicFetchError("Source address family is invalid")
        addresses.append((family, str(address)))
    return addresses


class _DeadlineStream(httpcore.NetworkStream):
    def __init__(self, sock: socket.socket, deadline: float) -> None:
        self.sock = sock
        self.deadline = deadline

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        self.sock.settimeout(_remaining(self.deadline))
        data = self.sock.recv(max_bytes)
        _remaining(self.deadline)
        return data

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.sock.settimeout(_remaining(self.deadline))
        self.sock.sendall(buffer)
        _remaining(self.deadline)

    def close(self) -> None:
        self.sock.close()

    def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.NetworkStream:
        try:
            self.sock.settimeout(_remaining(self.deadline))
            self.sock = ssl_context.wrap_socket(
                self.sock, server_hostname=server_hostname
            )
            _remaining(self.deadline)
        except BaseException:
            self.close()
            raise
        return self

    def get_extra_info(self, info: str) -> Any:
        if info == "ssl_object" and isinstance(self.sock, ssl.SSLSocket):
            return self.sock
        return None


class _PublicBackend(httpcore.NetworkBackend):
    def __init__(self, deadline: float) -> None:
        self.deadline = deadline

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        addresses = _resolve(host, port, self.deadline)
        for family, address in addresses:
            sock = socket.socket(family, socket.SOCK_STREAM)
            try:
                sock.settimeout(_remaining(self.deadline))
                # Numeric canonical addresses go straight to connect, never through create_connection or a second DNS lookup.
                sock.connect((address, port))
                _remaining(self.deadline)
                return _DeadlineStream(sock, self.deadline)
            except OSError:
                sock.close()
            except BaseException:
                sock.close()
                raise
        _remaining(self.deadline)
        raise PublicFetchError("Source connection failed")


class PublicFetcher:
    """Reuse one instance per source invocation. Every get and redirect consumes the same deadline and request budget."""

    def __init__(self, *, user_agent: str = "changelorg/0.1") -> None:
        self.deadline = time.monotonic() + TOTAL_TIMEOUT
        self.requests_remaining = MAX_REQUESTS
        self._headers = [
            (b"User-Agent", _user_agent(user_agent)),
            (b"Accept-Encoding", b"identity"),
        ]

    def check_deadline(self) -> None:
        _remaining(self.deadline)

    def get(self, url: str) -> httpx.Response:
        target = validate_public_url(url)
        try:
            for redirects in range(MAX_REDIRECTS + 1):
                self.check_deadline()
                if self.requests_remaining <= 0:
                    raise RequestBudgetExceeded("Source request budget exhausted")
                self.requests_remaining -= 1
                # A fresh pool forces address validation on every request, including same-host redirects and article links.
                with (
                    httpcore.ConnectionPool(
                        network_backend=_PublicBackend(self.deadline),
                        max_connections=1,
                        max_keepalive_connections=0,
                        retries=0,
                    ) as pool,
                    pool.stream("GET", str(target), headers=self._headers) as response,
                ):
                    self.check_deadline()
                    headers = httpx.Headers(response.headers)
                    if response.status in {301, 302, 303, 307, 308}:
                        if redirects == MAX_REDIRECTS:
                            raise PublicFetchError("Source redirect limit exceeded")
                        location = headers.get("location")
                        if not location:
                            raise PublicFetchError("Source redirect has no location")
                        target = validate_public_url(location, base_url=target)
                        continue
                    if not 200 <= response.status < 300:
                        raise PublicFetchError(
                            f"Source returned HTTP status {response.status}"
                        )
                    # Identity-only avoids decompression bombs. Add bounded decompression if a required source mandates compression.
                    if (
                        headers.get("content-encoding", "identity").lower()
                        != "identity"
                    ):
                        raise PublicFetchError(
                            "Source compressed responses are not supported"
                        )
                    length = headers.get("content-length")
                    if length is not None and (
                        not length.isascii()
                        or not length.isdecimal()
                        or int(length) > MAX_RESPONSE_BYTES
                    ):
                        raise PublicFetchError(
                            "Source response exceeds the byte limit or has an invalid length"
                        )
                    body = bytearray()
                    for chunk in response.iter_stream():
                        self.check_deadline()
                        if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise PublicFetchError(
                                "Source response exceeds the byte limit"
                            )
                        body.extend(chunk)
                    self.check_deadline()
                    return httpx.Response(
                        response.status,
                        headers=headers,
                        content=bytes(body),
                        request=httpx.Request("GET", target),
                    )
        except PublicFetchError:
            raise
        except (
            OSError,
            httpcore.NetworkError,
            httpcore.TimeoutException,
            httpcore.ProtocolError,
            httpcore.ConnectionNotAvailable,
            httpcore.UnsupportedProtocol,
            httpx.HTTPError,
            httpx.InvalidURL,
            ValueError,
        ):
            self.check_deadline()
            raise PublicFetchError("Source fetch failed") from None
        raise PublicFetchError("Source redirect limit exceeded")
