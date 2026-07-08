from datetime import datetime, timezone

import pytest

from changelorg.enrichment import enrich_change, enrichment_profile, profile_for
from changelorg.models import Source, TimeWindow
from changelorg.plugin import PluginConfigError
from changelorg.plugins.rss_atom import _matches_filters, _string_list_config


def test_cli_parse_config_collects_repeated_keys() -> None:
    from changelorg.cli import _parse_config

    config = _parse_config(["url=https://example.com/feed.xml", "include_any=gemini", "include_any=gemma"])

    assert config == {"url": "https://example.com/feed.xml", "include_any": ["gemini", "gemma"]}


def test_matches_include_any_case_insensitive() -> None:
    assert _matches_filters("Gemini API release notes", include_any=["gemini"], exclude_any=[])
    assert not _matches_filters("Android release notes", include_any=["gemini"], exclude_any=[])


def test_matches_exclude_any_case_insensitive() -> None:
    assert not _matches_filters("Gemini deprecated preview model", include_any=["gemini"], exclude_any=["deprecated"])


def test_string_list_config_accepts_string_or_list() -> None:
    assert _string_list_config({"include_any": "gemini"}, "include_any") == ["gemini"]
    assert _string_list_config({"include_any": ["gemini", "genai"]}, "include_any") == ["gemini", "genai"]


def test_string_list_config_rejects_invalid_values() -> None:
    with pytest.raises(PluginConfigError):
        _string_list_config({"include_any": ["gemini", 1]}, "include_any")


def test_enrichment_replaces_simple_version_with_first_meaningful_release_note_line() -> None:
    body = """
    <h2>0.115.1 (2026-07-01)</h2>
    <p>Full Changelog: <a href="https://example.com">v0.115.0...v0.115.1</a></p>
    <h3>Chores</h3>
    <ul>
      <li><strong>api:</strong> remove some nonfunctional types from the SDKs (<a href="https://example.com">5e7c431</a>)</li>
    </ul>
    """

    enriched = enrich_change(
        title="v0.115.1",
        summary=body,
        content="",
        url="https://example.com/release",
        feed_title="Release notes from anthropic-sdk-python",
        profile=enrichment_profile("test"),
    )

    assert enriched.title == "anthropic-sdk-python - api: remove some nonfunctional types from the SDKs (5e7c431)"
    assert "version_only_title" in enriched.quality_flags


def test_enrichment_preserves_descriptive_titles() -> None:
    enriched = enrich_change(
        title="v1.4.2 Bugfix Release",
        summary="<p>Bug fixes</p>",
        content="",
        url="https://example.com/release",
        feed_title="Release notes from duckdb",
        profile=enrichment_profile("test"),
    )

    assert enriched.title == "v1.4.2 Bugfix Release"


def test_enrichment_skips_release_date_boilerplate() -> None:
    body = """
    <h2>Release Notes</h2>
    <p>Released on 2026-07-01.</p>
    <h3>Bug fixes</h3>
    <ul><li>Avoid MRO cycle when collecting NamedTuple fields (#26464)</li></ul>
    """

    enriched = enrich_change(
        title="0.0.56",
        summary=body,
        content="",
        url="https://example.com/release",
        feed_title="Release notes from ty",
        profile=enrichment_profile("test"),
    )

    assert enriched.title == "ty - Avoid MRO cycle when collecting NamedTuple fields (#26464)"


def test_enrichment_fetches_linked_content_for_thin_generic_summaries() -> None:
    profile = enrichment_profile("linked", fetch_link_when=("thin_summary", "generic_summary"), max_items=2)

    enriched = enrich_change(
        title="Visual Studio Code 1.128 (Insiders)",
        summary="Learn what's new in Visual Studio Code 1.128 (Insiders)",
        content="",
        url="https://code.visualstudio.com/updates/v1_128",
        feed_title="VS Code Feed",
        profile=profile,
        fetch_link=lambda url: "<h2>Editor</h2><ul><li>Improved chat editing.</li><li>Better terminal diagnostics.</li></ul>",
    )

    assert enriched.summary == "**Editor**\n- Improved chat editing.\n- Better terminal diagnostics."
    assert "linked_content_fetched" in enriched.quality_flags


def test_enrichment_drops_common_linked_page_boilerplate() -> None:
    profile = profile_for("https://code.visualstudio.com/feed.xml")

    enriched = enrich_change(
        title="Visual Studio Code 1.128 (Insiders)",
        summary="Learn what's new in Visual Studio Code 1.128 (Insiders)",
        content="",
        url="https://code.visualstudio.com/updates/v1_128",
        feed_title="VS Code Feed",
        profile=profile,
        fetch_link=lambda url: """
        <p>Follow us on LinkedIn, X, Bluesky</p>
        <p>Last updated: July 3, 2026</p>
        <p>Release date: July 1, 2026</p>
        <p>Downloads: Windows, Mac, Linux</p>
        <p>You can still track our progress in the Commit log and Closed issues.</p>
        <p>Happy Coding!</p>
        <ul><li>Browser tools for agents now support richer context.</li></ul>
        """,
    )

    assert enriched.summary == "- Browser tools for agents now support richer context."


def test_enrichment_follows_url_only_release_notes_to_actual_content() -> None:
    profile = profile_for("https://github.com/microsoft/vscode/releases.atom")
    fetched_urls: list[str] = []

    def fetch_link(url: str) -> str:
        fetched_urls.append(url)
        return """
        <p>📼 Rewatch VS Code Live at MS Build 2026</p>
        <p>Welcome to the 1.128 release of Visual Studio Code. This release brings richer multi-chat agent sessions.</p>
        <ul><li>Quick chats: Ask a question without opening a workspace first.</li></ul>
        """

    enriched = enrich_change(
        title="1.128.0",
        summary='<p><a href="https://code.visualstudio.com/updates/v1_128">https://code.visualstudio.com/updates/v1_128</a></p>',
        content="",
        url="https://github.com/microsoft/vscode/releases/tag/1.128.0",
        feed_title="Release notes from vscode",
        profile=profile,
        fetch_link=fetch_link,
    )

    assert fetched_urls == ["https://code.visualstudio.com/updates/v1_128"]
    assert enriched.title == "vscode - Welcome to the 1.128 release of Visual Studio Code. This release brings richer multi-chat agent sessions."
    assert enriched.summary == "Welcome to the 1.128 release of Visual Studio Code. This release brings richer multi-chat agent sessions.\n- Quick chats: Ask a question without opening a workspace first."


def test_enrichment_fetches_minecraft_articles_when_summary_repeats_title() -> None:
    profile = profile_for("https://www.minecraft.net/en-us/feeds/community-content/rss")

    enriched = enrich_change(
        title="Minecraft Preview 26.40.30",
        summary="Minecraft Preview 26.40.30",
        content="",
        url="https://www.minecraft.net/en-us/article/minecraft-preview-26-40-30",
        feed_title="Minecraft",
        profile=profile,
        fetch_link=lambda url: """
        <p>A Minecraft: Bedrock Edition Preview</p>
        <p>Weary from all that hiking? Seek out an abandoned camp to rest your blocky bones.</p>
        <ul><li>The Cushion is an item that the player can place in the world.</li></ul>
        """,
    )

    assert enriched.title == "Minecraft Preview 26.40.30"
    assert enriched.summary == "A Minecraft: Bedrock Edition Preview\nWeary from all that hiking? Seek out an abandoned camp to rest your blocky bones.\n- The Cushion is an item that the player can place in the world."
    assert "linked_content_fetched" in enriched.quality_flags


def test_enrichment_compacts_overloaded_content() -> None:
    profile = enrichment_profile("compact", max_items=2)
    body = "<ul>" + "".join(f"<li>Fix number {index} with a long enough explanation to trigger compaction</li>" for index in range(80)) + "</ul>"

    enriched = enrich_change(
        title="v1.2.3 Bugfix Release",
        summary=body,
        content="",
        url="https://example.com/release",
        feed_title="Release notes from example",
        profile=profile,
    )

    assert enriched.summary == "- Fix number 0 with a long enough explanation to trigger compaction\n- Fix number 1 with a long enough explanation to trigger compaction"


def test_enrichment_compacts_rich_content_when_summary_is_empty() -> None:
    profile = enrichment_profile("compact", max_items=2)

    enriched = enrich_change(
        title="Store Update",
        summary="",
        content="<p>Welcome to the update.</p><ul><li>New cosmetics are available.</li></ul>",
        url="https://example.com/news/store-update",
        feed_title="Game News",
        profile=profile,
    )

    assert enriched.summary == "Welcome to the update.\n- New cosmetics are available."
    assert enriched.content == ""


def test_enrichment_compacts_version_only_categorized_release_notes() -> None:
    body = """
    <h2>Core</h2>
    <h3>Bugfixes</h3>
    <ul>
      <li>Enable adaptive thinking for Claude Sonnet 5.</li>
      <li>Prefer MCP content responses over structured output when both are present.</li>
    </ul>
    <h2>Desktop</h2>
    <h3>Improvements</h3>
    <ul>
      <li>Refresh cached remote skills.</li>
    </ul>
    """

    enriched = enrich_change(
        title="v1.17.12",
        summary=body,
        content="",
        url="https://github.com/anomalyco/opencode/releases/tag/v1.17.12",
        feed_title="Release notes from opencode",
        profile=enrichment_profile("test", max_items=3),
    )

    assert enriched.title == "opencode - Enable adaptive thinking for Claude Sonnet 5."
    assert enriched.summary == "**Core**\n**Bugfixes**\n- Enable adaptive thinking for Claude Sonnet 5.\n- Prefer MCP content responses over structured output when both are present.\n**Desktop**\n**Improvements**\n- Refresh cached remote skills."


def test_profile_for_uses_python_dsl_presets_for_known_urls() -> None:
    assert profile_for("https://code.visualstudio.com/feed.xml").name == "vscode-updates"


def test_source_model_accepts_filter_config() -> None:
    source = Source(
        id=1,
        name="Gemini",
        plugin="rss-atom",
        config={"url": "https://example.com/feed.xml", "include_any": ["gemini"]},
        enabled=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    window = TimeWindow(
        start=datetime(2026, 7, 1, tzinfo=timezone.utc),
        end=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )

    assert source.config["include_any"] == ["gemini"]
    assert window.start < window.end
