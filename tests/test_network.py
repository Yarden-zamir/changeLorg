from __future__ import annotations

import ipaddress
import socket
import ssl
import threading
import time
from collections import deque
from collections.abc import Callable

import httpx
import pytest

from changelorg import network
from changelorg.network import (
    FetchBudgetExceeded,
    PublicFetcher,
    PublicFetchError,
    RequestBudgetExceeded,
    validate_public_url,
)


def response(body: bytes = b"ok", *, status: int = 200, headers: bytes = b"") -> bytes:
    return (
        f"HTTP/1.1 {status} Test\r\nContent-Length: {len(body)}\r\n".encode()
        + headers
        + b"\r\n"
        + body
    )


class Wire:
    """Exercise the real httpcore parser and network backend without external network access."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.responses: deque[bytes | list[bytes]] = deque()
        self.answers: dict[str, list[str]] = {}
        self.lookups: list[tuple[str, int]] = []
        self.sockets: list[Socket] = []
        self.on_recv: Callable[[], None] = lambda: None
        monkeypatch.setattr(network.socket, "getaddrinfo", self.resolve)
        monkeypatch.setattr(network.socket, "socket", self.open_socket)

    def resolve(self, host: str, port: int, **kwargs: object) -> list[tuple]:
        self.lookups.append((host, port))
        return [
            (
                socket.AF_INET6 if ":" in address else socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (address, port),
            )
            for address in self.answers.get(host, ["93.184.216.34"])
        ]

    def open_socket(self, family: int, kind: int) -> Socket:
        sock = Socket(self, family)
        self.sockets.append(sock)
        return sock


class Socket:
    def __init__(self, wire: Wire, family: int) -> None:
        self.wire = wire
        self.family = family
        self.address: tuple[str, int] | None = None
        self.sent = bytearray()
        self.timeouts: list[float] = []
        self.closed = False
        data = wire.responses.popleft() if wire.responses else b""
        self.chunks = deque([data] if isinstance(data, bytes) else data)

    def connect(self, address: tuple[str, int]) -> None:
        assert ipaddress.ip_address(address[0]).is_global
        self.address = address

    def settimeout(self, timeout: float) -> None:
        self.timeouts.append(timeout)

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def recv(self, max_bytes: int) -> bytes:
        self.wire.on_recv()
        if not self.chunks:
            return b""
        chunk = self.chunks.popleft()
        if len(chunk) > max_bytes:
            self.chunks.appendleft(chunk[max_bytes:])
        return chunk[:max_bytes]

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    return Wire(monkeypatch)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/secret",
        "http://10.0.0.1",
        "http://172.16.0.1",
        "http://192.168.0.1",
        "http://169.254.169.254/latest/meta-data",
        "http://100.64.0.1",
        "http://0.0.0.0",
        "http://224.0.0.1",
        "http://240.0.0.1",
        "http://192.0.0.8",
        "http://192.0.2.1",
        "http://[::1]",
        "http://[::]",
        "http://[fe80::1]",
        "http://[fc00::1]",
        "http://[ff02::1]",
        "http://[fec0::1]",
        "http://[::ffff:8.8.8.8]",
        "http://[::ffff:127.0.0.1]",
        "http://[2001:db8::1]",
        "http://[64:ff9b::7f00:1]",
        "http://[2002:7f00:1::]",
        "http://[fe80::1%25en0]",
        "http://localhost",
        "http://a.localhost",
        "http://printer.local",
        "file:///etc/passwd",
        "ftp://example.com/feed",
        "http://example.com:8080",
        "https://example.com:8443",
        "http://user:secret@example.com",
        "http://@example.com",
        "http://example.com\\@127.0.0.1",
        " http://example.com",
        "http://example.com/\nsecret",
        "http:///feed",
        "//example.com/feed",
    ],
)
def test_rejects_unsafe_urls_before_dns(wire: Wire, url: str) -> None:
    with pytest.raises(PublicFetchError):
        PublicFetcher().get(url)
    assert not wire.lookups
    assert not wire.sockets


@pytest.mark.parametrize(
    "answers",
    [
        ["127.0.0.1"],
        ["93.184.216.34", "10.0.0.1"],
        ["::ffff:93.184.216.34"],
        ["93.184.216.34", "fe80::1"],
        ["224.0.0.1"],
        [],
    ],
)
def test_checks_every_dns_answer_before_any_connection(
    wire: Wire, answers: list[str]
) -> None:
    wire.answers["example.com"] = answers
    with pytest.raises(PublicFetchError):
        PublicFetcher().get("http://example.com/feed")
    assert not wire.sockets


@pytest.mark.parametrize("host", ["2130706433", "127.1", "0177.0.0.1", "0x7f000001"])
def test_dns_checks_noncanonical_ipv4(wire: Wire, host: str) -> None:
    wire.answers[host] = ["127.0.0.1"]
    with pytest.raises(PublicFetchError):
        PublicFetcher().get(f"http://{host}")
    assert not wire.sockets


def test_connection_pins_dns_and_preserves_tls_hostname(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    tls_hosts = []

    def wrap(context: ssl.SSLContext, sock: Socket, *, server_hostname: str) -> Socket:
        assert context.check_hostname
        assert context.verify_mode == ssl.CERT_REQUIRED
        tls_hosts.append(server_hostname)
        return sock

    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", wrap)
    original_resolve = wire.resolve

    def rebind(host: str, port: int, **kwargs: object) -> list[tuple]:
        answers = original_resolve(host, port, **kwargs)
        wire.answers[host] = ["127.0.0.1"]
        return answers

    monkeypatch.setattr(network.socket, "getaddrinfo", rebind)
    wire.responses.append(response(b"feed"))
    result = PublicFetcher().get("https://example.com/feed")
    assert result.content == b"feed"
    assert result.url == httpx.URL("https://example.com/feed")
    result.raise_for_status()
    assert wire.lookups == [("example.com", 443)]
    assert wire.sockets[0].address == ("93.184.216.34", 443)
    assert b"Host: example.com\r\n" in wire.sockets[0].sent
    assert tls_hosts == ["example.com"]
    assert wire.sockets[0].closed


def test_public_ipv6_uses_numeric_ipv6_socket(wire: Wire) -> None:
    wire.answers["example.com"] = ["2606:4700:4700::1111"]
    wire.responses.append(response())
    PublicFetcher().get("http://example.com")
    assert wire.sockets[0].family == socket.AF_INET6
    assert wire.sockets[0].address == ("2606:4700:4700::1111", 80)


@pytest.mark.parametrize(
    "location",
    [
        "http://127.0.0.1/private",
        "//169.254.169.254/secret",
        "http://private.example/secret",
        "http://user:secret@example.com",
        "http://@example.com",
        "http://example.com:8000/secret",
        "file:///etc/passwd",
        "http://example.com\\@127.0.0.1",
    ],
)
def test_redirects_cannot_bypass_policy(wire: Wire, location: str) -> None:
    wire.answers["private.example"] = ["10.1.2.3"]
    wire.responses.append(
        response(status=302, headers=f"Location: {location}\r\n".encode())
    )
    with pytest.raises(PublicFetchError):
        PublicFetcher().get("http://example.com/feed")
    assert len(wire.sockets) == 1
    assert wire.sockets[0].closed


def test_same_host_redirect_resolves_again_and_rejects_rebinding(wire: Wire) -> None:
    wire.responses.append(response(status=302, headers=b"Location: /next\r\n"))
    wire.on_recv = lambda: wire.answers.update({"example.com": ["127.0.0.1"]})
    with pytest.raises(PublicFetchError):
        PublicFetcher().get("http://example.com/feed")
    assert len(wire.lookups) == 2
    assert len(wire.sockets) == 1


def test_three_redirects_succeed_without_forwarding_cookies(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1234")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1234")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1234")
    for status in [301, 307, 308]:
        wire.responses.append(
            response(
                status=status,
                headers=b"Location: /next\r\nSet-Cookie: secret=token\r\n",
            )
        )
    wire.responses.append(response())
    fetcher = PublicFetcher()
    assert fetcher.get("http://example.com").text == "ok"
    assert fetcher.requests_remaining == network.MAX_REQUESTS - 4
    assert all(host == "example.com" for host, _ in wire.lookups)
    assert all(b"Cookie:" not in sock.sent for sock in wire.sockets)


def test_fourth_redirect_is_not_followed(wire: Wire) -> None:
    wire.responses.extend([response(status=303, headers=b"Location: /next\r\n")] * 5)
    with pytest.raises(PublicFetchError, match="redirect limit"):
        PublicFetcher().get("http://example.com")
    assert len(wire.sockets) == 4
    assert all(sock.closed for sock in wire.sockets)


@pytest.mark.parametrize("framing", ["length", "chunked", "eof"])
def test_stream_size_limit(wire: Wire, framing: str) -> None:
    body = b"a" * (network.MAX_RESPONSE_BYTES + 1)
    if framing == "length":
        wire.responses.append(response(body))
    elif framing == "chunked":
        wire.responses.append(
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
            + f"{len(body):x}\r\n".encode()
            + body
            + b"\r\n0\r\n\r\n"
        )
    else:
        wire.responses.append(b"HTTP/1.1 200 OK\r\n\r\n" + body)
    with pytest.raises(PublicFetchError, match="byte limit"):
        PublicFetcher().get("http://example.com")
    assert wire.sockets[0].closed


def test_exact_byte_limit_is_allowed(wire: Wire) -> None:
    wire.responses.append(response(b"a" * network.MAX_RESPONSE_BYTES))
    assert (
        len(PublicFetcher().get("http://example.com").content)
        == network.MAX_RESPONSE_BYTES
    )


def test_compression_is_rejected_before_decompression(wire: Wire) -> None:
    wire.responses.append(
        response(b"compressed bomb", headers=b"Content-Encoding: gzip\r\n")
    )
    with pytest.raises(PublicFetchError, match="compressed"):
        PublicFetcher().get("http://example.com")
    assert b"Accept-Encoding: identity\r\n" in wire.sockets[0].sent


def test_request_budget_includes_redirects_and_subsequent_gets(wire: Wire) -> None:
    wire.responses.extend([response()] * (network.MAX_REQUESTS - 1))
    wire.responses.append(response(status=302, headers=b"Location: /next\r\n"))
    fetcher = PublicFetcher()
    for _ in range(network.MAX_REQUESTS - 1):
        fetcher.get("http://example.com")
    with pytest.raises(RequestBudgetExceeded):
        fetcher.get("http://example.com/article")
    assert len(wire.sockets) == network.MAX_REQUESTS
    assert all(sock.closed for sock in wire.sockets)


def test_deadline_does_not_reset_between_requests(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [0.0]
    monkeypatch.setattr(network.time, "monotonic", lambda: now[0])
    wire.responses.append(response())
    fetcher = PublicFetcher()
    fetcher.get("http://example.com")
    now[0] = network.TOTAL_TIMEOUT
    with pytest.raises(FetchBudgetExceeded):
        fetcher.get("http://example.com/article")
    assert len(wire.sockets) == 1


@pytest.mark.parametrize(
    "chunks",
    [
        [b"HTTP/1.1 200 OK\r\nX-Slow: ", b"a", b"b", b"c"],
        [b"HTTP/1.1 200 OK\r\n\r\n", b"a", b"b", b"c"],
    ],
)
def test_slow_headers_and_body_share_absolute_deadline(
    wire: Wire, monkeypatch: pytest.MonkeyPatch, chunks: list[bytes]
) -> None:
    now = [0.0]
    monkeypatch.setattr(network.time, "monotonic", lambda: now[0])

    def tick() -> None:
        now[0] += 16

    wire.on_recv = tick
    wire.responses.append(chunks)
    with pytest.raises(FetchBudgetExceeded):
        PublicFetcher().get("http://example.com")
    assert wire.sockets[0].closed
    assert wire.sockets[0].timeouts[-1] == 12


def test_dns_wait_is_bounded(wire: Wire, monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()
    finished = threading.Event()

    def stalled_dns(*args: object, **kwargs: object) -> list:
        try:
            release.wait(5)
            return []
        finally:
            finished.set()

    monkeypatch.setattr(network.socket, "getaddrinfo", stalled_dns)
    monkeypatch.setattr(network, "TOTAL_TIMEOUT", 0.05)
    start = time.monotonic()
    try:
        with pytest.raises(FetchBudgetExceeded):
            PublicFetcher().get("http://example.com")
        assert time.monotonic() - start < 1
        assert not wire.sockets
    finally:
        release.set()
        assert finished.wait(1)


def test_dns_worker_capacity_is_bounded(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(network, "_DNS_SLOTS", slots)
    monkeypatch.setattr(network, "TOTAL_TIMEOUT", 0.01)
    with pytest.raises(PublicFetchError):
        PublicFetcher().get("http://example.com")
    assert not wire.lookups
    assert not wire.sockets


def test_tls_certificate_failures_are_safe_and_close_socket(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject(*args: object, **kwargs: object) -> None:
        raise ssl.SSLCertVerificationError("PRIVATE CERTIFICATE DETAIL")

    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", reject)
    with pytest.raises(PublicFetchError) as error:
        PublicFetcher().get("https://example.com")
    assert "PRIVATE" not in str(error.value)
    assert wire.sockets[0].closed


@pytest.mark.parametrize(
    "raw",
    [
        response(b"PRIVATE RESPONSE SECRET", status=403),
        b"PRIVATE RESPONSE SECRET\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nPRIVATE RESPONSE SECRET",
    ],
)
def test_errors_do_not_expose_response_content(wire: Wire, raw: bytes) -> None:
    wire.responses.append(raw)
    with pytest.raises(ValueError) as error:
        PublicFetcher().get("http://example.com")
    assert "PRIVATE" not in str(error.value)
    assert "SECRET" not in str(error.value)
    assert wire.sockets[0].closed


def test_url_validation_never_resolves_dns(wire: Wire) -> None:
    assert validate_public_url("https://example.com/feed#section").fragment == ""
    assert not wire.lookups
