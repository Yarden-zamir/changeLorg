import ssl

import httpx
import pytest
from test_network import Socket, Wire, response

from changelorg import network
from changelorg.discovery import discover_sources, normalize_discovery_url
from changelorg.models import SourceCreate
from changelorg.network import FetchBudgetExceeded, PublicFetchError

EMPTY_RSS = b"<rss version='2.0'><channel><title>Empty RSS</title></channel></rss>"
EMPTY_ATOM = (
    b"<feed xmlns='http://www.w3.org/2005/Atom'><title>Empty Atom</title></feed>"
)


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    def wrap(context: ssl.SSLContext, sock: Socket, *, server_hostname: str) -> Socket:
        return sock

    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", wrap)
    return Wire(monkeypatch)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("  HTTPS://Example.COM/feed#top \n", "https://example.com/feed"),
        ("http://example.com/feed?q=one", "http://example.com/feed?q=one"),
        ("example.com/feed", "https://example.com/feed"),
        ("example.com", "https://example.com"),
        ("example.com:443/feed", "https://example.com/feed"),
        (
            "example.com/feed?next=https://other.example/",
            "https://example.com/feed?next=https://other.example/",
        ),
        (
            "Other-owner/Repo_1.2-3",
            "https://github.com/Other-owner/Repo_1.2-3/releases.atom",
        ),
        ("Other-owner/Repo.git/", "https://github.com/Other-owner/Repo/releases.atom"),
        ("github.com/Other/Repo", "https://github.com/Other/Repo/releases.atom"),
        (
            "https://www.github.com/Other/Repo.git/",
            "https://github.com/Other/Repo/releases.atom",
        ),
        (
            "https://github.com/Other/Repo/releases?after=v1",
            "https://github.com/Other/Repo/releases.atom",
        ),
        (
            "https://github.com/Other/Repo/releases/latest",
            "https://github.com/Other/Repo/releases.atom",
        ),
        (
            "https://github.com/Other/Repo/releases/tag/v1.2",
            "https://github.com/Other/Repo/releases.atom",
        ),
        (
            "https://github.com/Other/Repo/releases/tag/pkg/v1/",
            "https://github.com/Other/Repo/releases.atom",
        ),
        (
            "https://github.com/Other/Repo/tags/",
            "https://github.com/Other/Repo/tags.atom",
        ),
        (
            "https://github.com/Other/Repo/releases.atom?x=1",
            "https://github.com/Other/Repo/releases.atom?x=1",
        ),
        (
            "https://www.github.com/Other/Repo/tags.atom",
            "https://www.github.com/Other/Repo/tags.atom",
        ),
        (
            "github.com/Other/Repo/commits/",
            "https://github.com/Other/Repo/commits.atom",
        ),
        (
            "github.com/Other/Repo/commits.atom",
            "https://github.com/Other/Repo/commits.atom",
        ),
        (
            "https://github.com/Other/Repo/commits/main.atom?x=1",
            "https://github.com/Other/Repo/commits/main.atom?x=1",
        ),
        (
            "https://github.com/Other/Repo/commits/release/9.0.atom",
            "https://github.com/Other/Repo/commits/release/9.0.atom",
        ),
        (
            "https://github.com/Other/Repo/commits/release%2F9.0.atom",
            "https://github.com/Other/Repo/commits/release%2F9.0.atom",
        ),
    ],
)
def test_normalize_without_network(wire: Wire, value: str, expected: str) -> None:
    url = normalize_discovery_url(value)
    assert isinstance(url, httpx.URL)
    assert str(url) == expected
    assert not wire.lookups
    assert not wire.sockets


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "example",
        "example/feed/other",
        "//example.com/feed",
        "https:///example.com",
        "http:example.com",
        "ftp://example.com/feed",
        "file:///etc/passwd",
        "custom.scheme://example.com/feed",
        "example..com/feed",
        "-example.com/feed",
        "example.com/a b",
        "https://example.com/a\nb",
        "https://example.com:8000/feed",
        "https://127.0.0.1/feed",
        "127.0.0.1/feed",
        "https://[::1]/feed",
        "https://localhost/feed",
        "https://printer.local/feed",
        "https://secret:password@github.com/Other/Repo",
        "https://github.com@127.0.0.1/Other/Repo",
        "https://github.com\\@example.com/Other/Repo",
        "https://github%2ecom/Other/Repo",
        "https://@github.com/Other/Repo",
        "secret:password@example.com/feed",
        "owner_/repo",
        "owner/r$po",
        "\u00f6wner/repo",
        "owner/r\u00e9po",
        "owner/repo?query=1",
        "owner/repo#tag",
        "owner/repo//",
        "owner/%72epo",
        "owner/..",
        "https://github.com/owner_/repo",
        "https://github.com/owner/.git",
        "https://github.com/Other/Repo/" + "x" * 2000,
    ],
)
def test_invalid_initial_input_is_value_error_without_credentials_or_dns(
    wire: Wire, value: str
) -> None:
    with pytest.raises(ValueError) as error:
        discover_sources(value)
    assert not isinstance(error.value, PublicFetchError)
    assert "secret" not in str(error.value)
    assert "password" not in str(error.value)
    assert not wire.lookups
    assert not wire.sockets


@pytest.mark.parametrize(
    "path",
    [
        "",
        "Other",
        "Other/Repo/issues",
        "Other/Repo/pulls",
        "Other/Repo/pull/1",
        "Other/Repo/tree/main",
        "Other/Repo/releases/tag",
        "Other/Repo/releases/new",
        "Other/Repo/tags/extra",
        "Other/Repo/releases.atom/extra",
        "Other/Repo/commits/main",
        "Other/Repo/commits/main.atom/extra",
        "Other/Repo/commits/.atom",
        "Other/Repo/commits/.hidden.atom",
        "Other/Repo/commits/main.lock.atom",
        "Other/Repo/commits/release//main.atom",
        "Other/Repo/commits/a..b.atom",
        "Other/Repo/commits/a%3Fb.atom",
        "Other/Repo/commits/a%0Ab.atom",
        "Other/Repo/commits/@.atom",
    ],
)
def test_unsupported_github_paths_do_not_map_to_releases(wire: Wire, path: str) -> None:
    with pytest.raises(ValueError, match="GitHub"):
        discover_sources("https://github.com/" + path)
    assert not wire.lookups


@pytest.mark.parametrize(
    "host",
    [
        "github.com.evil.example",
        "github-com.example",
        "www.github.com.evil.example",
        "notgithub.com",
        "github.com.",
    ],
)
def test_only_exact_github_hosts_map(wire: Wire, host: str) -> None:
    url = f"https://{host}/Other/Repo/issues"
    assert str(normalize_discovery_url(url)) == url
    wire.responses.append(response(b"<html><title>Not a feed</title></html>"))
    assert discover_sources(url) == []
    assert wire.lookups == [(host, 443)]
    assert b"GET /Other/Repo/issues HTTP/1.1" in wire.sockets[0].sent


@pytest.mark.parametrize("suffix", ["", "/releases", "/releases/latest", "/tags"])
def test_github_fetches_only_canonical_feed(wire: Wire, suffix: str) -> None:
    wire.responses.append(
        response(
            EMPTY_ATOM.replace(
                b"</feed>",
                b"<entry><id>release-1</id><title>Release</title></entry></feed>",
            )
        )
    )
    sources = discover_sources("https://github.com/Any-owner/Any.repo" + suffix)
    endpoint = "tags.atom" if suffix == "/tags" else "releases.atom"
    assert sources[0].config == {
        "url": f"https://github.com/Any-owner/Any.repo/{endpoint}"
    }
    assert len(wire.sockets) == 1
    assert (
        f"GET /Any-owner/Any.repo/{endpoint} HTTP/1.1".encode() in wire.sockets[0].sent
    )


def test_fallback_draft_keeps_release_url_and_stable_repository_name(
    wire: Wire,
) -> None:
    url = "https://github.com/Other/Repo/releases.atom"
    wire.responses.extend(
        [
            response(EMPTY_ATOM),
            response(
                status=302, headers=b"Location: /Other/Repo/commits/main.atom\r\n"
            ),
            response(EMPTY_ATOM.replace(b"Empty Atom", b"Recent commits to Repo:main")),
        ]
    )
    fallback = discover_sources("Other/Repo")
    assert fallback[0].config == {"url": url}
    assert "Repo" in fallback[0].name
    assert "commit" not in fallback[0].name.lower()
    wire.responses.append(
        response(
            EMPTY_ATOM.replace(
                b"</feed>",
                b"<entry><id>release-1</id><title>Release</title></entry></feed>",
            )
        )
    )
    assert discover_sources("Other/Repo") == fallback
    assert not wire.responses


@pytest.mark.parametrize("branch", ["release/9.0", "release%2F9.0"])
def test_direct_commit_feed_preserves_encoded_request_path(
    wire: Wire, branch: str
) -> None:
    url = f"https://github.com/Other/Repo/commits/{branch}.atom"
    wire.responses.append(
        response(EMPTY_ATOM.replace(b"Empty Atom", b"Recent commits to Repo"))
    )
    sources = discover_sources(url)
    assert sources[0].config["url"] == url
    assert sources[0].name == "Recent commits to Repo"
    assert (
        f"GET /Other/Repo/commits/{branch}.atom HTTP/1.1".encode()
        in wire.sockets[0].sent
    )
    assert len(wire.sockets) == 1


@pytest.mark.parametrize(
    ("body", "title"),
    [
        (EMPTY_RSS, "Empty RSS"),
        (EMPTY_ATOM, "Empty Atom"),
        (
            EMPTY_RSS.replace(
                b"</channel>", b"<item><title>Entry</title></item></channel>"
            ),
            "Empty RSS",
        ),
        (
            EMPTY_ATOM.replace(b"Empty Atom", b"&lt;b&gt;Release&lt;/b&gt; &amp; News"),
            "Release & News",
        ),
        (
            EMPTY_RSS.replace(b"<title>Empty RSS</title>", b""),
            "final.example/extensionless",
        ),
        (EMPTY_RSS.replace(b"Empty RSS", b"A" * 250), "A" * 200),
    ],
)
def test_direct_feed_uses_content_and_final_url(
    wire: Wire, body: bytes, title: str
) -> None:
    wire.responses.extend(
        [
            response(
                status=302, headers=b"Location: http://final.example/extensionless\r\n"
            ),
            response(body, headers=b"Content-Type: text/plain\r\n"),
        ]
    )
    sources = discover_sources("http://example.com/start")
    assert len(sources) == 1
    assert isinstance(sources[0], SourceCreate)
    assert sources[0].model_dump() == {
        "name": title,
        "plugin": "rss-atom",
        "enabled": True,
        "config": {"url": "http://final.example/extensionless"},
    }
    assert len(wire.sockets) == 2


@pytest.mark.parametrize(
    "body",
    [b"", b"not a feed", b"<html><title>News</title></html>", b"<SECRET malformed"],
)
def test_suffix_is_not_feed_evidence_and_no_html_plugin_guess(
    wire: Wire, body: bytes
) -> None:
    wire.responses.append(
        response(body, headers=b"Content-Type: application/rss+xml\r\n")
    )
    assert discover_sources("http://example.com/news.rss") == []
    assert len(wire.sockets) == 1


def test_alternate_tokens_case_parameters_and_names(wire: Wire) -> None:
    wire.responses.append(
        response(b"""<html><head>
      <LINK REL='stylesheet ALTERNATE' TYPE='Application/RSS+XML ; charset=utf-8'
            HREF='/rss' TITLE='&lt;b&gt;Releases&lt;/b&gt; &amp; News'>
      <link rel='alternate' type='APPLICATION/ATOM+XML' href='//feeds.example/atom'/>
      <link rel='notalternate' type='application/rss+xml' href='/not-alternate'>
      <link rel='alternate' type='text/html' href='/not-feed'>
      <link rel='alternate' href='/missing-type'>
      <link rel='alternate' type='application/rss+xml'>
      <title>  Page &amp; News </title></head></html>""")
    )
    sources = discover_sources("http://example.com/news.rss")
    assert [s.config["url"] for s in sources] == [
        "http://example.com/rss",
        "http://feeds.example/atom",
    ]
    assert [s.name for s in sources] == ["Releases & News", "Page & News"]
    assert all(s.plugin == "rss-atom" and s.enabled for s in sources)
    assert wire.lookups == [("example.com", 80)]
    assert len(wire.sockets) == 1


@pytest.mark.parametrize(
    ("bases", "expected"),
    [
        ("", "http://final.example/news/feed"),
        ("<base href='../feeds/'>", "http://final.example/feeds/feed"),
        (
            "<base href='https://feeds.example/root/'>",
            "https://feeds.example/root/feed",
        ),
        (
            "<base href='http://127.0.0.1/'><base href='/safe/'><base href='/ignored/'>",
            "http://final.example/safe/feed",
        ),
        (
            "<base href='http://secret:password@example.com/'><base href='javascript:bad'>",
            "http://final.example/news/feed",
        ),
        (
            "<base href='https://[bad'><base href='/safe/'>",
            "http://final.example/safe/feed",
        ),
    ],
)
def test_first_valid_base_applies_even_after_links(
    wire: Wire, bases: str, expected: str
) -> None:
    wire.responses.extend(
        [
            response(status=302, headers=b"Location: http://final.example/news/\r\n"),
            response(
                (
                    "<link rel='alternate' type='application/rss+xml' href='feed'>"
                    + bases
                ).encode()
            ),
        ]
    )
    sources = discover_sources("http://example.com/start")
    assert [s.config["url"] for s in sources] == [expected]
    assert sources[0].name == httpx.URL(expected).host + httpx.URL(expected).path
    assert wire.lookups == [("example.com", 80), ("final.example", 80)]


def test_unsafe_alternates_skip_without_dns_and_distinct_candidates_cap_at_ten(
    wire: Wire,
) -> None:
    unsafe = [
        "http://127.0.0.1/feed",
        "//[::1]/feed",
        "http://169.254.169.254/feed",
        "http://localhost/feed",
        "http://printer.local/feed",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:application/rss+xml,feed",
        "http://example.com:8000/feed",
        "http://secret:password@example.com/feed",
        "http://github.com@127.0.0.1/feed",
        "http://github.com\\@example.com/feed",
        "https://[bad",
        "/has space",
        "x" * 2001,
    ]
    links = (
        unsafe
        + ["/feed#one", "http://EXAMPLE.com:80/feed#two"]
        + [f"/feed/{i}" for i in range(20)]
    )
    wire.responses.append(
        response(
            "".join(
                f"<link rel='alternate' type='application/rss+xml' href='{href}'>"
                for href in links
            ).encode()
        )
    )
    sources = discover_sources("http://example.com/")
    assert [s.config["url"] for s in sources] == ["http://example.com/feed"] + [
        f"http://example.com/feed/{i}" for i in range(9)
    ]
    assert "secret" not in str(sources)
    assert "password" not in str(sources)
    assert wire.lookups == [("example.com", 80)]
    assert len(wire.sockets) == 1


def test_candidate_dns_is_deferred_to_preview(wire: Wire) -> None:
    wire.answers["unverified.example"] = ["127.0.0.1"]
    wire.responses.append(
        response(
            b"<link rel='alternate' type='application/rss+xml' href='http://unverified.example/feed'>"
        )
    )
    sources = discover_sources("http://example.com/")
    assert sources[0].config["url"] == "http://unverified.example/feed"
    assert wire.lookups == [("example.com", 80)]


def test_private_dns_and_redirect_failures_remain_fetch_errors(wire: Wire) -> None:
    wire.answers["private.example"] = ["93.184.216.34", "10.0.0.1"]
    wire.responses.append(
        response(status=302, headers=b"Location: http://private.example/feed\r\n")
    )
    with pytest.raises(PublicFetchError):
        discover_sources("http://example.com/")
    assert len(wire.sockets) == 1


def test_response_size_limit_applies_before_parse(wire: Wire) -> None:
    wire.responses.append(response(b" " * (network.MAX_RESPONSE_BYTES + 1)))
    with pytest.raises(PublicFetchError, match="byte limit"):
        discover_sources("http://example.com/")
    assert wire.sockets[0].closed


def test_redirects_share_deadline(wire: Wire, monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]
    monkeypatch.setattr(network.time, "monotonic", lambda: now[0])

    def tick() -> None:
        now[0] += 31

    wire.on_recv = tick
    wire.responses.extend(
        [
            response(status=302, headers=b"Location: /next\r\n"),
            response(EMPTY_RSS),
        ]
    )
    with pytest.raises(FetchBudgetExceeded):
        discover_sources("http://example.com/")
    assert len(wire.sockets) == 2
    assert all(sock.closed for sock in wire.sockets)


def test_http_error_does_not_expose_response(wire: Wire) -> None:
    wire.responses.append(response(b"SECRET", status=403))
    with pytest.raises(PublicFetchError) as error:
        discover_sources("http://example.com/")
    assert "SECRET" not in str(error.value)
