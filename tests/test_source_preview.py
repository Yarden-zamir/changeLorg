from datetime import UTC, datetime

import pytest
from test_network import Wire, response

from changelorg import network
from changelorg.enrichment import PROFILES
from changelorg.models import ChangeInput, Source, SourceCreate, TimeWindow
from changelorg.network import (
    FetchBudgetExceeded,
    PublicFetchError,
    validate_source_config,
)
from changelorg.plugins.html_news import HtmlNewsPlugin
from changelorg.plugins.rss_atom import RssAtomPlugin


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    return Wire(monkeypatch)


@pytest.fixture
def window() -> TimeWindow:
    return TimeWindow(
        start=datetime(2026, 7, 1, tzinfo=UTC), end=datetime(2026, 7, 3, tzinfo=UTC)
    )


def source(plugin: str = "rss-atom", **config: object) -> Source:
    now = datetime(2026, 7, 3, tzinfo=UTC)
    return Source(
        id=1,
        name="Preview",
        plugin=plugin,
        config={"url": "http://example.com/feed", **config},
        created_at=now,
        updated_at=now,
    )


def feed(count: int, *, link: str = "http://example.com/article") -> bytes:
    items = "".join(
        f"<item><guid>{index}</guid><title>Release {index}</title><link>{link}/{index}</link><description>New release</description></item>"
        for index in range(count)
    )
    return f"<rss version='2.0'><channel><title>Example</title>{items}</channel></rss>".encode()


def test_builtin_configs_validate_without_network(wire: Wire) -> None:
    for profile in PROFILES:
        validate_source_config(
            source(
                enrichment_profile=profile,
                include_any="release",
                exclude_any=["deprecated"],
                profile="dev",
            )
        )
        validate_source_config(
            source(
                "html-news",
                enrichment_profile=profile,
                article_path_prefix="/news/",
                title_suffixes=" | News",
                exclude_path_prefixes=["/news/tag/"],
                limit=100,
            )
        )
    assert not wire.lookups
    assert not wire.sockets


@pytest.mark.parametrize(
    ("plugin", "config"),
    [
        ("local-plugin", {"url": "https://example.com"}),
        ("rss-atom", {}),
        ("rss-atom", {"url": 123}),
        ("rss-atom", {"url": "http://127.0.0.1"}),
        (
            "rss-atom",
            {"url": "https://example.com", "user_agent": "bad\r\nHost: secret"},
        ),
        ("rss-atom", {"url": "https://example.com", "include_any": [1]}),
        ("rss-atom", {"url": "https://example.com", "include_any": None}),
        ("rss-atom", {"url": "https://example.com", "exclude_any": False}),
        ("rss-atom", {"url": "https://example.com", "profile": 1}),
        ("rss-atom", {"url": "https://example.com", "enrichment_profile": "unknown"}),
        ("rss-atom", {"url": "https://example.com", "enrichment_profile": None}),
        ("rss-atom", {"url": "https://example.com", "timeout": 1000}),
        ("html-news", {"url": "https://example.com"}),
        ("html-news", {"url": "https://example.com", "article_path_prefix": ""}),
        ("html-news", {"url": "https://example.com", "article_path_prefix": ["/news"]}),
        (
            "html-news",
            {
                "url": "https://example.com",
                "article_path_prefix": "/news",
                "title_suffixes": [1],
            },
        ),
        (
            "html-news",
            {
                "url": "https://example.com",
                "article_path_prefix": "/news",
                "limit": True,
            },
        ),
        (
            "html-news",
            {
                "url": "https://example.com",
                "article_path_prefix": "/news",
                "limit": 101,
            },
        ),
        (
            "html-news",
            {"url": "https://example.com", "article_path_prefix": "/news", "limit": 0},
        ),
        (
            "html-news",
            {
                "url": "https://example.com",
                "article_path_prefix": "/news",
                "limit": "10",
            },
        ),
    ],
)
def test_invalid_custom_config_raises_value_error_without_network(
    wire: Wire, plugin: str, config: dict
) -> None:
    with pytest.raises(ValueError):
        validate_source_config(
            SourceCreate(name="Preview", plugin=plugin, config=config)
        )
    assert not wire.lookups


def test_rss_preview_uses_normal_fetch_and_caps_entries(
    wire: Wire, window: TimeWindow
) -> None:
    wire.responses.append(response(feed(network.MAX_ENTRIES + 50)))
    changes = RssAtomPlugin().fetch(source(), window)
    assert len(changes) == network.MAX_ENTRIES
    assert len(changes[:10]) == 10
    assert all(
        isinstance(change, ChangeInput) and change.published_at == window.end
        for change in changes
    )
    assert changes[0].external_id == "0"
    assert len(wire.sockets) == 1


def test_atom_preview_applies_window_filters_and_enrichment(
    wire: Wire, window: TimeWindow
) -> None:
    body = b"""<feed xmlns='http://www.w3.org/2005/Atom'><title>Example</title>
    <entry><id>old</id><title>Keep old</title><updated>2026-06-30T00:00:00Z</updated></entry>
    <entry><id>new</id><title>Keep new</title><updated>2026-07-04T00:00:00Z</updated></entry>
    <entry><id>drop</id><title>Keep deprecated</title><updated>2026-07-02T00:00:00Z</updated></entry>
    <entry><id>other</id><title>Other</title><updated>2026-07-02T00:00:00Z</updated></entry>
    <entry><id>current</id><title>Keep release</title><updated>2026-07-02T00:00:00Z</updated>
    <link href='http://example.com/article'/><summary>New release</summary></entry></feed>"""
    wire.responses.extend(
        [response(body), response(b"<ul><li>New useful feature.</li></ul>")]
    )
    changes = RssAtomPlugin().fetch(
        source(
            include_any="KEEP",
            exclude_any=["deprecated"],
            enrichment_profile="linked-release-notes",
        ),
        window,
    )
    assert [change.external_id for change in changes] == ["current"]
    assert "New useful feature" in changes[0].summary
    assert "linked_content_fetched" in changes[0].metadata["quality_flags"]
    assert len(wire.sockets) == 2


@pytest.mark.parametrize("via_summary", [False, True])
def test_rss_enrichment_cannot_fetch_private_links(
    wire: Wire, window: TimeWindow, via_summary: bool
) -> None:
    body = feed(
        1,
        link="http://127.0.0.1/private"
        if not via_summary
        else "http://example.com/article",
    )
    if via_summary:
        body = body.replace(b"New release", b"http://127.0.0.1/private")
    wire.responses.append(response(body))
    with pytest.raises(PublicFetchError):
        RssAtomPlugin().fetch(source(enrichment_profile="linked-release-notes"), window)
    assert len(wire.sockets) == 1


def test_rss_enrichment_redirects_share_the_safe_fetcher(
    wire: Wire, window: TimeWindow
) -> None:
    wire.responses.extend(
        [
            response(feed(1)),
            response(
                status=302, headers=b"Location: http://169.254.169.254/secret\r\n"
            ),
        ]
    )
    with pytest.raises(PublicFetchError):
        RssAtomPlugin().fetch(source(enrichment_profile="linked-release-notes"), window)
    assert len(wire.sockets) == 2


def test_rss_budget_exhaustion_preserves_entries_with_explicit_flag(
    wire: Wire, window: TimeWindow
) -> None:
    wire.responses.append(response(feed(20)))
    wire.responses.extend(
        [response(b"<p>Release detail.</p>")] * (network.MAX_REQUESTS - 1)
    )
    changes = RssAtomPlugin().fetch(
        source(enrichment_profile="linked-release-notes"), window
    )
    assert len(changes) == 20
    assert len(wire.sockets) == network.MAX_REQUESTS
    assert "linked_content_fetched" in changes[0].metadata["quality_flags"]
    assert "enrichment_budget_exhausted" in changes[-1].metadata["quality_flags"]


def test_html_preview_uses_redirected_listing_and_normal_articles(
    wire: Wire, window: TimeWindow
) -> None:
    wire.responses.extend(
        [
            response(status=302, headers=b"Location: http://news.example/news/\r\n"),
            response(
                b"<a href='article'>Article</a><a href='tag/no'>Tag</a><a href='http://elsewhere.example/news/other'>Other</a>"
            ),
            response(
                b"<h1>Release | Example</h1><p>July 2, 2026</p><p>A useful feature.</p>"
            ),
        ]
    )
    changes = HtmlNewsPlugin().fetch(
        source(
            "html-news",
            article_path_prefix="/news/",
            exclude_path_prefixes=["/news/tag/"],
            title_suffixes=[" | Example"],
        ),
        window,
    )
    assert len(changes) == 1
    assert changes[0].url == "http://news.example/news/article"
    assert changes[0].title == "Release"
    assert changes[0].published_at == datetime(2026, 7, 2, tzinfo=UTC)
    assert "A useful feature" in changes[0].summary
    assert len(wire.sockets) == 3


def test_html_article_redirect_cannot_fetch_private_addresses(
    wire: Wire, window: TimeWindow
) -> None:
    wire.responses.extend(
        [
            response(b"<a href='/news/article'>Article</a>"),
            response(status=302, headers=b"Location: http://127.0.0.1/private\r\n"),
        ]
    )
    with pytest.raises(PublicFetchError):
        HtmlNewsPlugin().fetch(
            source("html-news", article_path_prefix="/news/"), window
        )
    assert len(wire.sockets) == 2


def test_html_request_budget_preserves_completed_entries(
    wire: Wire, window: TimeWindow
) -> None:
    wire.responses.append(
        response(
            "".join(
                f"<a href='/news/{index}'>Article</a>" for index in range(100)
            ).encode()
        )
    )
    wire.responses.extend(
        [response(b"<h1>Release</h1><p>A useful feature.</p>")]
        * (network.MAX_REQUESTS - 1)
    )
    changes = HtmlNewsPlugin().fetch(
        source("html-news", article_path_prefix="/news/", limit=100), window
    )
    assert len(changes) == network.MAX_REQUESTS - 1
    assert len(wire.sockets) == network.MAX_REQUESTS
    assert all(
        "article_budget_exhausted" in change.metadata["quality_flags"]
        for change in changes
    )


@pytest.mark.parametrize("plugin", ["rss-atom", "html-news"])
def test_linked_fetches_share_the_source_deadline(
    wire: Wire, window: TimeWindow, monkeypatch: pytest.MonkeyPatch, plugin: str
) -> None:
    now = [0.0]
    monkeypatch.setattr(network.time, "monotonic", lambda: now[0])

    def tick() -> None:
        now[0] += 31

    wire.on_recv = tick
    if plugin == "rss-atom":
        wire.responses.append(response(feed(1)))
        source_plugin = RssAtomPlugin()
        item = source(enrichment_profile="linked-release-notes")
    else:
        wire.responses.append(response(b"<a href='/news/article'>Article</a>"))
        source_plugin = HtmlNewsPlugin()
        item = source("html-news", article_path_prefix="/news/")
    wire.responses.append(response(b"<p>Release detail.</p>"))
    with pytest.raises(FetchBudgetExceeded):
        source_plugin.fetch(item, window)
    assert len(wire.sockets) == 2
    assert all(sock.closed for sock in wire.sockets)


def test_feed_parse_errors_do_not_include_response_content(
    wire: Wire, window: TimeWindow
) -> None:
    wire.responses.append(response(b"<SECRET malformed document"))
    with pytest.raises(ValueError) as error:
        RssAtomPlugin().fetch(source(), window)
    assert "SECRET" not in str(error.value)
