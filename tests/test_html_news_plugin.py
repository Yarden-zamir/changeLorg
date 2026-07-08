from datetime import datetime, timezone

from changelorg.plugins.html_news import _article_links, _published_at, _title


def test_article_links_keeps_matching_same_site_articles_once() -> None:
    html = """
    <a href="/news/live-update-1-36-0">Live Update</a>
    <a href="/news/tag/patch-notes">Patch Notes</a>
    <a href="https://arcraiders.com/news/live-update-1-36-0#comments">Duplicate</a>
    <a href="https://example.com/news/other">Other site</a>
    """

    links = _article_links(
        html,
        "https://arcraiders.com/news",
        include_prefixes=["/news/"],
        exclude_prefixes=["/news/tag/"],
    )

    assert links == ["https://arcraiders.com/news/live-update-1-36-0"]


def test_published_at_extracts_visible_arc_date() -> None:
    html = """
    <header>Navigation</header>
    <h1>Live Update 1.36.0</h1>
    <div>July 7, 2026</div>
    <p>Raiders!</p>
    """

    published_at = _published_at(html, datetime(2026, 7, 8, tzinfo=timezone.utc))

    assert published_at == datetime(2026, 7, 7, tzinfo=timezone.utc)


def test_title_prefers_h1_and_removes_configured_suffix() -> None:
    html = """
    <meta property="og:title" content="Live Update 1.36.0 | ARC Raiders" />
    <h1>Live Update 1.36.0</h1>
    """

    assert _title(html, [" | ARC Raiders"]) == "Live Update 1.36.0"
