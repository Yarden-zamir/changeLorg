from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_discovery import EMPTY_ATOM, EMPTY_RSS
from test_discovery import wire as wire  # noqa: PLC0414
from test_network import Wire, response
from test_source_preview import source
from test_source_preview import window as window  # noqa: PLC0414

from changelorg import network, store
from changelorg.discovery import discover_sources
from changelorg.feeds import fetch_feed
from changelorg.models import ChangeUpdate, SourceCreate, TimeWindow
from changelorg.network import (
    FetchBudgetExceeded,
    PublicFetcher,
    PublicFetchError,
    RequestBudgetExceeded,
)
from changelorg.plugin import PluginManager
from changelorg.plugins.rss_atom import RssAtomPlugin
from changelorg.service import generate_changes

RELEASE_URL = "https://github.com/Other/Repo/releases.atom"
COMMIT_URL = "https://github.com/Other/Repo/commits.atom"
COMMITS = b"""<feed xmlns='http://www.w3.org/2005/Atom'><title>Recent commits to Repo:main</title>
<entry><id>tag:github.com,2008:Grit::Commit/abc123</id><title>Fix the parser</title>
<updated>2026-07-02T12:00:00Z</updated><link href='https://github.com/Other/Repo/commit/abc123'/>
<content type='html'>&lt;p&gt;Handle empty input.&lt;/p&gt;</content></entry>
<entry><id>tag:github.com,2008:Grit::Commit/def456</id><title>Add a test</title>
<updated>2026-07-02T13:00:00Z</updated><link href='https://github.com/Other/Repo/commit/def456'/>
<content type='html'>&lt;p&gt;Check empty input.&lt;/p&gt;</content></entry></feed>"""
RELEASE = b"""<feed xmlns='http://www.w3.org/2005/Atom'><title>Release notes from Repo</title>
<entry><id>tag:github.com,2008:Repository/123/v1</id><title>First stable release</title>
<updated>2026-07-02T14:00:00Z</updated></entry></feed>"""


def test_empty_releases_produce_one_normal_card_per_commit(
    wire: Wire, window: TimeWindow
) -> None:
    wire.responses.extend([response(EMPTY_ATOM), response(COMMITS)])
    changes = RssAtomPlugin().fetch(source(url=RELEASE_URL), window)
    assert [change.external_id for change in changes] == [
        "tag:github.com,2008:Grit::Commit/abc123",
        "tag:github.com,2008:Grit::Commit/def456",
    ]
    assert [change.title for change in changes] == ["Fix the parser", "Add a test"]
    assert changes[0].published_at == datetime(2026, 7, 2, 12, tzinfo=UTC)
    assert "Handle empty input" in changes[0].summary + changes[0].content
    for change in changes:
        assert change.url and "/commit/" in change.url
        assert change.metadata["feed_url"] == RELEASE_URL
        assert change.metadata["effective_feed_url"] == COMMIT_URL
        assert change.metadata["feed_kind"] == "commit"
        assert change.metadata["fallback_reason"] == "no_releases"
    assert len(wire.sockets) == 2
    assert b"GET /Other/Repo/commits.atom HTTP/1.1" in wire.sockets[1].sent


@pytest.mark.parametrize("old", [False, True])
def test_any_release_prevents_commit_supplement_even_outside_window(
    wire: Wire, window: TimeWindow, old: bool
) -> None:
    body = RELEASE.replace(b"2026-07-02", b"2020-07-02") if old else RELEASE
    wire.responses.append(response(body))
    changes = RssAtomPlugin().fetch(source(url=RELEASE_URL), window)
    assert len(changes) == (0 if old else 1)
    assert all("fallback_reason" not in change.metadata for change in changes)
    assert len(wire.sockets) == 1


def test_fallback_commit_cards_use_standard_window_and_filters(
    wire: Wire, window: TimeWindow
) -> None:
    wire.responses.extend([response(EMPTY_ATOM), response(COMMITS)])
    changes = RssAtomPlugin().fetch(
        source(url=RELEASE_URL, include_any="PARSER"), window
    )
    assert len(changes) == 1
    wire.responses.extend(
        [response(EMPTY_ATOM), response(COMMITS.replace(b"2026-07-02", b"2020-07-02"))]
    )
    assert RssAtomPlugin().fetch(source(url=RELEASE_URL), window) == []


def test_empty_valid_commit_feed_returns_no_cards(
    wire: Wire, window: TimeWindow
) -> None:
    wire.responses.extend([response(EMPTY_ATOM), response(EMPTY_ATOM)])
    assert RssAtomPlugin().fetch(source(url=RELEASE_URL), window) == []
    assert len(wire.sockets) == 2


@pytest.mark.parametrize("configured", [None, "compact-release-notes"])
def test_fallback_uses_effective_feed_profile_but_honors_explicit_profile(
    wire: Wire, window: TimeWindow, configured: str | None
) -> None:
    wire.responses.extend([response(EMPTY_ATOM), response(COMMITS)])
    config = {"enrichment_profile": configured} if configured else {}
    changes = RssAtomPlugin().fetch(
        source(url="https://github.com/microsoft/vscode/releases.atom", **config),
        window,
    )
    assert all(
        change.metadata["enrichment_profile"] == (configured or "default")
        for change in changes
    )
    assert len(wire.sockets) == 2


@pytest.mark.parametrize("entrypoint", ["discovery", "refresh"])
@pytest.mark.parametrize("commit_failure", [False, True])
@pytest.mark.parametrize("status", [403, 404, 429, 500, 503])
def test_http_failures_remain_visible_without_another_fallback(
    wire: Wire, window: TimeWindow, entrypoint: str, commit_failure: bool, status: int
) -> None:
    if commit_failure:
        wire.responses.append(response(EMPTY_ATOM))
    wire.responses.append(response(b"SECRET", status=status))
    with pytest.raises(PublicFetchError, match=str(status)) as error:
        if entrypoint == "discovery":
            discover_sources(RELEASE_URL)
        else:
            RssAtomPlugin().fetch(source(url=RELEASE_URL), window)
    assert "SECRET" not in str(error.value)
    assert len(wire.sockets) == (2 if commit_failure else 1)


@pytest.mark.parametrize("commit_failure", [False, True])
@pytest.mark.parametrize(
    "body",
    [b"", b"<html>SECRET</html>", b"<SECRET malformed", EMPTY_ATOM[:-7], EMPTY_RSS],
)
def test_fallback_requires_valid_atom_at_both_endpoints(
    wire: Wire, commit_failure: bool, body: bytes
) -> None:
    if commit_failure:
        wire.responses.append(response(EMPTY_ATOM))
    wire.responses.append(response(body))
    with pytest.raises(PublicFetchError, match="could not be parsed") as error:
        fetch_feed(PublicFetcher(), RELEASE_URL)
    assert "SECRET" not in str(error.value)
    assert len(wire.sockets) == (2 if commit_failure else 1)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/Other/Repo/tags.atom",
        "https://github.com/Other/Repo/commits.atom",
        "https://github.com/Other/Repo/commits/main.atom",
        "https://github.com.evil.example/Other/Repo/releases.atom",
        "https://www.github.com.evil.example/Other/Repo/releases.atom",
        "https://github.com./Other/Repo/releases.atom",
        "https://github.com/Other/Repo/releases.atom/extra",
        RELEASE_URL + "?after=v1",
    ],
)
def test_only_unfiltered_exact_github_release_sources_fall_back(
    wire: Wire, url: str
) -> None:
    wire.responses.append(response(EMPTY_ATOM))
    _, _, fallback = fetch_feed(PublicFetcher(), url)
    assert not fallback
    assert len(wire.sockets) == 1


@pytest.mark.parametrize(
    "location",
    [
        "https://elsewhere.example/Other/Repo/releases.atom",
        RELEASE_URL + "?after=v1",
        "https://github.com/Other/Repo/tags.atom",
    ],
)
def test_redirected_nonrelease_or_filtered_feed_does_not_trigger_fallback(
    wire: Wire, location: str
) -> None:
    wire.responses.extend(
        [
            response(status=302, headers=f"Location: {location}\r\n".encode()),
            response(EMPTY_ATOM),
        ]
    )
    _, _, fallback = fetch_feed(PublicFetcher(), RELEASE_URL)
    assert not fallback
    assert len(wire.sockets) == 2


def test_fallback_uses_the_same_request_budget(wire: Wire) -> None:
    wire.responses.extend([response(EMPTY_ATOM)] * network.MAX_REQUESTS)
    client = PublicFetcher()
    for _ in range(network.MAX_REQUESTS - 1):
        client.get("https://example.com/feed")
    with pytest.raises(RequestBudgetExceeded):
        fetch_feed(client, RELEASE_URL)
    assert len(wire.sockets) == network.MAX_REQUESTS


def test_fallback_and_release_share_deadline(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [0.0]
    monkeypatch.setattr(network.time, "monotonic", lambda: now[0])

    def tick() -> None:
        now[0] += 31

    wire.on_recv = tick
    wire.responses.extend([response(EMPTY_ATOM), response(COMMITS)])
    with pytest.raises(FetchBudgetExceeded):
        fetch_feed(PublicFetcher(), RELEASE_URL)
    assert len(wire.sockets) == 2
    assert all(sock.closed for sock in wire.sockets)


@pytest.mark.parametrize(
    "failure", ["private-redirect", "private-dns", "redirect-limit", "byte-limit"]
)
def test_fallback_obeys_public_fetch_limits(wire: Wire, failure: str) -> None:
    wire.responses.append(response(EMPTY_ATOM))
    if failure == "private-dns":
        wire.on_recv = lambda: wire.answers.update({"github.com": ["127.0.0.1"]})
    elif failure == "private-redirect":
        wire.responses.append(
            response(status=302, headers=b"Location: http://127.0.0.1/SECRET\r\n")
        )
    elif failure == "redirect-limit":
        wire.responses.extend(
            [response(status=302, headers=b"Location: /next\r\n")] * 4
        )
    else:
        wire.responses.append(response(b" " * (network.MAX_RESPONSE_BYTES + 1)))
    with pytest.raises(PublicFetchError) as error:
        fetch_feed(PublicFetcher(), RELEASE_URL)
    assert "SECRET" not in str(error.value)
    assert (
        len(wire.sockets)
        == {
            "private-dns": 1,
            "private-redirect": 2,
            "redirect-limit": 5,
            "byte-limit": 2,
        }[failure]
    )
    assert all(sock.closed for sock in wire.sockets)


def test_saved_source_rechecks_releases_and_preserves_commit_state(
    wire: Wire, window: TimeWindow, tmp_path: Path
) -> None:
    db_path = tmp_path / "fallback.duckdb"
    saved_source = store.add_source(
        SourceCreate(name="Repo updates", config={"url": RELEASE_URL}), db_path=db_path
    )
    manager = PluginManager([RssAtomPlugin()])
    wire.responses.extend([response(EMPTY_ATOM), response(COMMITS)])
    initial = generate_changes(window, db_path=db_path, plugin_manager=manager)
    assert not initial.errors
    commit = initial.changes[0]
    state = store.update_change(
        commit.id, ChangeUpdate(saved=True, note="Keep this commit"), db_path=db_path
    )
    wire.responses.extend(
        [
            response(EMPTY_ATOM),
            response(COMMITS.replace(b"Add a test", b"Improve the test")),
        ]
    )
    repeated = generate_changes(window, db_path=db_path, plugin_manager=manager)
    assert not repeated.errors
    assert len(repeated.changes) == 2
    wire.responses.append(response(RELEASE))
    released = generate_changes(window, db_path=db_path, plugin_manager=manager)
    assert not released.errors
    assert len(released.changes) == 3
    retained = store.get_change(commit.id, db_path=db_path)
    assert retained.external_id == commit.external_id
    assert retained.saved and retained.note == state.note
    assert retained.state_updated_at == state.state_updated_at
    assert retained.title == "Improve the test"
    assert released.changes[0].external_id == "tag:github.com,2008:Repository/123/v1"
    assert (
        store.get_source(saved_source.id, db_path=db_path).config["url"] == RELEASE_URL
    )
    assert len(wire.sockets) == 5
