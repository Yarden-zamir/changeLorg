from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable


GENERIC_SUMMARY_PHRASES = (
    "learn what's new",
    "learn what is new",
    "is here",
    "is out",
    "release date:",
)


@dataclass(frozen=True)
class EnrichmentProfile:
    name: str
    fetch_link_when: frozenset[str] = field(default_factory=frozenset)
    max_items: int = 6
    drop_phrases: tuple[str, ...] = ()
    compress_overloaded: bool = True


def enrichment_profile(
    name: str,
    *,
    fetch_link_when: tuple[str, ...] = (),
    max_items: int = 6,
    drop_phrases: tuple[str, ...] = (),
    compress_overloaded: bool = True,
) -> EnrichmentProfile:
    return EnrichmentProfile(
        name=name,
        fetch_link_when=frozenset(fetch_link_when),
        max_items=max_items,
        drop_phrases=drop_phrases,
        compress_overloaded=compress_overloaded,
    )


PROFILES = {
    "default": enrichment_profile("default"),
    "linked-release-notes": enrichment_profile(
        "linked-release-notes",
        fetch_link_when=("thin_summary", "summary_repeats_title", "generic_summary"),
        max_items=8,
    ),
    "compact-release-notes": enrichment_profile("compact-release-notes", max_items=8),
    "vscode-updates": enrichment_profile(
        "vscode-updates",
        fetch_link_when=("thin_summary", "summary_repeats_title", "generic_summary", "version_only_title"),
        max_items=8,
        drop_phrases=(
            "Edit",
            "Build",
            "Debug",
            "Extensions",
            "Follow us on",
            "Last updated",
            "Downloads:",
            "Release date:",
            "Rewatch VS Code Live",
            "You can still track our progress",
            "Happy Coding!",
        ),
    ),
    "minecraft-news": enrichment_profile(
        "minecraft-news",
        fetch_link_when=("thin_summary", "summary_repeats_title", "generic_summary"),
        max_items=8,
        drop_phrases=("Share this story", "Community Creations", "News"),
    ),
    "official-game-news": enrichment_profile(
        "official-game-news",
        max_items=8,
        drop_phrases=("Back to Top", "Buy now", "Cookie Settings", "Privacy Policy"),
    ),
    "raycast-changelog": enrichment_profile("raycast-changelog", max_items=7),
    "status-feed": enrichment_profile("status-feed", compress_overloaded=False),
}


URL_PROFILE_HINTS = {
    "code.visualstudio.com/feed.xml": "vscode-updates",
    "github.com/microsoft/vscode/releases.atom": "vscode-updates",
    "minecraft.net/en-us/feeds/community-content/rss": "minecraft-news",
    "www.python.org/downloads/feed.rss": "linked-release-notes",
    "docs.slack.dev/changelog/rss.xml": "linked-release-notes",
    "api.slack.com/changelog.rss": "linked-release-notes",
    "blog.jetbrains.com/idea/feed/": "linked-release-notes",
    "raycast.com/changelog/feed.xml": "raycast-changelog",
    "githubstatus.com/history.rss": "status-feed",
    "reachthefinals.com/patchnotes?format=rss": "official-game-news",
    "arcraiders.com/news": "official-game-news",
}


@dataclass(frozen=True)
class HtmlBlock:
    tag: str
    text: str


@dataclass(frozen=True)
class EnrichmentResult:
    title: str
    summary: str
    content: str
    quality_flags: list[str]
    profile: str


class _TextBlockParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[HtmlBlock] = []
        self._skip_depth = 0
        self._current_tag: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "nav", "footer", "header", "svg"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._current_tag = tag
            self._current_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "nav", "footer", "header", "svg"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if self._current_tag == tag:
            text = normalize_text("".join(self._current_text))
            if text:
                self.blocks.append(HtmlBlock(tag=tag, text=text))
            self._current_tag = None
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and self._current_tag is not None:
            self._current_text.append(data)


def normalize_text(value: str) -> str:
    return " ".join(value.split())


def text_blocks(html: str) -> list[HtmlBlock]:
    parser = _TextBlockParser()
    parser.feed(html)
    return parser.blocks


def plain_text(value: str) -> str:
    if "<" not in value or ">" not in value:
        return normalize_text(value)
    return normalize_text(" ".join(block.text for block in text_blocks(value)))


def is_simple_version_title(title: str) -> bool:
    normalized = title.strip().lower()
    if normalized.startswith("v"):
        normalized = normalized[1:]
    if not normalized:
        return False
    return all(part.isdigit() for part in normalized.split(".")) and "." in normalized


def feed_label(feed_title: str) -> str | None:
    prefix = "Release notes from "
    if feed_title.startswith(prefix):
        return feed_title[len(prefix) :].strip() or None
    return feed_title.strip() or None


def is_ignored_release_note_line(tag: str, text: str, profile: EnrichmentProfile) -> bool:
    lowered = text.lower()
    if tag.startswith("h"):
        return True
    if is_url_only_line(text):
        return True
    if "full changelog" in lowered or "compare" in lowered:
        return True
    if lowered.startswith("released on "):
        return True
    if lowered.startswith(("follow us on", "last updated:", "downloads:", "release date:")):
        return True
    if lowered in {"happy coding!"} or lowered.startswith("you can still track our progress"):
        return True
    return any(phrase.lower() in lowered for phrase in profile.drop_phrases)


def is_url_only_line(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized.startswith(("http://", "https://")):
        return False
    return " " not in normalized


def first_url_line(html: str) -> str | None:
    for block in text_blocks(html):
        if is_url_only_line(block.text):
            return block.text
    for token in plain_text(html).split():
        if token.startswith(("http://", "https://")):
            return token.rstrip(".,)")
    return None


def first_meaningful_line(html: str, profile: EnrichmentProfile) -> str | None:
    for block in text_blocks(html):
        if not is_ignored_release_note_line(block.tag, block.text, profile):
            return block.text
    return None


def compact_blocks(html: str, profile: EnrichmentProfile) -> str:
    lines: list[str] = []
    content_items = 0
    for block in text_blocks(html):
        if block.tag.startswith("h"):
            lowered_heading = block.text.lower()
            if "release notes" not in lowered_heading and not is_simple_version_title(block.text):
                lines.append(f"**{block.text}**")
            continue
        if is_ignored_release_note_line(block.tag, block.text, profile):
            continue
        if block.tag == "li":
            lines.append(f"- {block.text}")
            content_items += 1
        else:
            lines.append(block.text)
            content_items += 1
        if content_items >= profile.max_items:
            break
    return "\n".join(lines)


def profile_for(feed_url: str, configured_name: str | None = None) -> EnrichmentProfile:
    if configured_name:
        return PROFILES.get(configured_name, PROFILES["default"])
    for needle, profile_name in URL_PROFILE_HINTS.items():
        if needle in feed_url:
            return PROFILES[profile_name]
    return PROFILES["default"]


def classify_quality(title: str, summary: str, content: str) -> list[str]:
    flags: list[str] = []
    summary_text = plain_text(summary)
    content_text = plain_text(content)
    title_text = plain_text(title)
    lowered_summary = summary_text.lower()

    if is_simple_version_title(title):
        flags.append("version_only_title")
    if not summary_text or len(summary_text) < 40:
        flags.append("thin_summary")
    if summary_text and title_text and lowered_summary == title_text.lower():
        flags.append("summary_repeats_title")
    if any(phrase in lowered_summary for phrase in GENERIC_SUMMARY_PHRASES):
        flags.append("generic_summary")
    if len(summary_text) > 1800 or len(content_text) > 1800:
        flags.append("overloaded_content")
    if "appeared first on" in lowered_summary or "the post " in lowered_summary:
        flags.append("html_boilerplate")

    return flags


def enrich_change(
    *,
    title: str,
    summary: str,
    content: str,
    url: str | None,
    feed_title: str,
    profile: EnrichmentProfile,
    fetch_link: Callable[[str], str | None] | None = None,
) -> EnrichmentResult:
    flags = classify_quality(title, summary, content)
    body = content or summary
    linked_body: str | None = None

    if url and fetch_link and profile.fetch_link_when.intersection(flags):
        linked_body = fetch_link(first_url_line(body) or url)
        if linked_body:
            flags.append("linked_content_fetched")
            body = linked_body

    display_title = title
    if "version_only_title" in flags:
        meaningful_line = first_meaningful_line(body, profile)
        if meaningful_line:
            label = feed_label(feed_title)
            display_title = f"{label} - {meaningful_line}" if label else meaningful_line

    display_summary = summary
    display_content = content
    if linked_body or "version_only_title" in flags or "overloaded_content" in flags or "html_boilerplate" in flags or "generic_summary" in flags or ("thin_summary" in flags and content):
        compact = compact_blocks(body, profile)
        if compact:
            display_summary = compact
            display_content = ""

    return EnrichmentResult(
        title=display_title,
        summary=display_summary,
        content=display_content,
        quality_flags=flags,
        profile=profile.name,
    )
