import json

import pytest
from test_discovery import wire as wire
from test_network import response
from test_source_preview import source, window as window

from changelorg.discovery import discover_sources, normalize_discovery_url
from changelorg.network import validate_source_config
from changelorg.plugins.x import XPlugin, XUnavailable


@pytest.mark.parametrize(
    "link",
    [
        "x.com/Example",
        "https://twitter.com/Example/status/123?s=20",
        "https://mobile.x.com/Example",
    ],
)
def test_x_discovery_needs_no_key_or_network(link, wire):
    draft = discover_sources(link)[0]
    assert draft.plugin == "x"
    assert draft.config == {"url": "https://x.com/Example"}
    assert wire.lookups == []


@pytest.mark.parametrize(
    "link",
    [
        "x.com/home",
        "x.com/i/lists/123",
        "x.com/search?q=test",
        "x.com/abc/likes",
        "x.com/abc/status/not-id",
        "x.com/abcdefghijklmnop",
    ],
)
def test_invalid_x_links_rejected(link):
    with pytest.raises(ValueError):
        normalize_discovery_url(link)


@pytest.mark.parametrize("suffix", ["", "/rss", "/post/abc123"])
def test_bluesky_links_use_public_rss(suffix):
    assert (
        str(normalize_discovery_url("https://bsky.app/profile/bsky.app" + suffix))
        == "https://bsky.app/profile/bsky.app/rss"
    )


def post(
    id="12", text="Hello <script>bad()</script>", date="Thu Jul 02 12:00:00 +0000 2026"
):
    return {
        "type": "tweet",
        "content": {"tweet": {"id_str": id, "full_text": text, "created_at": date}},
    }


def timeline(entries):
    payload = {"props": {"pageProps": {"timeline": {"entries": entries}}}}
    return (
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(payload).replace("</", "<\\/")
        + "</script>"
    ).encode()


def test_keyless_posts_are_filtered_deduplicated_and_escaped(wire, window):
    wire.responses.append(
        response(
            timeline(
                [
                    post(),
                    post(),
                    post("13", "old", "2020-01-01T00:00:00Z"),
                    post("14", "Hello again"),
                ]
            )
        )
    )
    changes = XPlugin().fetch(
        source("x", url="https://x.com/Example", include_any=["hello"]), window
    )
    assert [item.external_id for item in changes] == ["x:12", "x:14"]
    assert changes[0].url == "https://x.com/Example/status/12"
    assert "<script>" not in changes[0].content
    assert "&lt;script&gt;" in changes[0].content
    assert len(wire.sockets) == 1
    assert b"Host: syndication.twitter.com" in wire.sockets[0].sent
    assert b"Authorization:" not in wire.sockets[0].sent
    assert b"Cookie:" not in wire.sockets[0].sent


@pytest.mark.parametrize("status", [401, 403, 429, 451, 500])
def test_blocked_or_rate_limited_timeline_is_an_error(wire, window, status):
    wire.responses.append(response(b"private upstream details", status=status))
    with pytest.raises(XUnavailable) as error:
        XPlugin().fetch(source("x", url="https://x.com/Example"), window)
    assert "private upstream" not in str(error.value)
    assert len(wire.lookups) == 1


def test_private_redirect_is_rejected(wire, window):
    wire.responses.append(
        response(status=302, headers=b"Location: http://127.0.0.1/secret\r\n")
    )
    with pytest.raises(XUnavailable):
        XPlugin().fetch(source("x", url="https://x.com/Example"), window)
    assert len(wire.lookups) == 1


def test_no_credentials_allowed_in_source_config():
    with pytest.raises(ValueError):
        validate_source_config(
            source("x", url="https://x.com/Example", bearer_token="secret")
        )


@pytest.mark.parametrize(
    "body",
    [
        b"<html>Sign in</html>",
        b'<script id="__NEXT_DATA__">{}</script>',
        timeline([{"type": "tweet", "content": {}}]),
        timeline(None),
    ],
)
def test_missing_or_malformed_timeline_is_not_silent_success(wire, window, body):
    wire.responses.append(response(body))
    with pytest.raises(XUnavailable):
        XPlugin().fetch(source("x", url="https://x.com/Example"), window)


def test_actual_empty_timeline_is_valid(wire, window):
    wire.responses.append(response(timeline([])))
    assert XPlugin().fetch(source("x", url="https://x.com/Example"), window) == []


def test_post_count_is_bounded(wire, window):
    wire.responses.append(response(timeline([post(str(i)) for i in range(250)])))
    assert len(XPlugin().fetch(source("x", url="https://x.com/Example"), window)) == 200
