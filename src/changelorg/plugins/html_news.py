from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from changelorg.enrichment import compact_blocks, plain_text, profile_for
from changelorg.models import ChangeInput, Source, TimeWindow
from changelorg.plugin import PluginConfigError


MONTHS = {
    "January": 1,
    "February": 2,
    "March": 3,
    "April": 4,
    "May": 5,
    "June": 6,
    "July": 7,
    "August": 8,
    "September": 9,
    "October": 10,
    "November": 11,
    "December": 12,
}


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(href)


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title: str | None = None
        self.h1: str | None = None
        self.visible_text: list[str] = []
        self._skip_depth = 0
        self._capture_h1 = False
        self._h1_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "svg"}:
            self._skip_depth += 1
            return
        if tag == "meta":
            data = dict(attrs)
            key = data.get("property") or data.get("name") or ""
            content = data.get("content")
            if key in {"og:title", "twitter:title"} and content and self.title is None:
                self.title = content
        if self._skip_depth == 0 and tag == "h1":
            self._capture_h1 = True
            self._h1_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "svg"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "h1" and self._capture_h1:
            h1 = " ".join("".join(self._h1_text).split())
            if h1 and self.h1 is None:
                self.h1 = h1
            self._capture_h1 = False
            self._h1_text = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if data.strip():
            self.visible_text.append(data)
        if self._capture_h1:
            self._h1_text.append(data)


def _string_config(config: dict[str, Any], key: str) -> str | None:
    value = config.get(key)
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise PluginConfigError(f"html-news config {key!r} must be a non-empty string")


def _string_list_config(config: dict[str, Any], key: str) -> list[str]:
    value = config.get(key, [])
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise PluginConfigError(f"html-news config {key!r} must be a string or list of strings")


def _limit_config(config: dict[str, Any]) -> int:
    value = config.get("limit", 20)
    if isinstance(value, int) and 1 <= value <= 100:
        return value
    raise PluginConfigError("html-news config 'limit' must be an integer from 1 to 100")


def _article_links(html: str, base_url: str, include_prefixes: list[str], exclude_prefixes: list[str]) -> list[str]:
    parser = _LinkParser()
    parser.feed(html)
    seen: set[str] = set()
    links: list[str] = []
    base_netloc = urlparse(base_url).netloc

    for href in parser.links:
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"} or parsed.netloc != base_netloc:
            continue
        if include_prefixes and not any(parsed.path.startswith(prefix) for prefix in include_prefixes):
            continue
        if exclude_prefixes and any(parsed.path.startswith(prefix) for prefix in exclude_prefixes):
            continue
        clean = parsed._replace(fragment="").geturl()
        if clean not in seen and clean.rstrip("/") != base_url.rstrip("/"):
            seen.add(clean)
            links.append(clean)
    return links


def _published_at(html: str, fallback: datetime) -> datetime:
    parser = _PageParser()
    parser.feed(html)
    text = " ".join(" ".join(parser.visible_text).split())
    tokens = text.replace(",", " ,").split()
    for index, token in enumerate(tokens):
        month = MONTHS.get(token)
        if month is None or index + 3 >= len(tokens):
            continue
        day = tokens[index + 1].rstrip(",")
        comma = tokens[index + 2]
        year = tokens[index + 3].rstrip(",")
        if comma != "," or not day.isdigit() or not year.isdigit():
            continue
        try:
            return datetime(int(year), month, int(day), tzinfo=timezone.utc)
        except ValueError:
            continue
    return fallback


def _title(html: str, suffixes: list[str]) -> str:
    parser = _PageParser()
    parser.feed(html)
    title = parser.h1 or parser.title or "Untitled news item"
    for suffix in suffixes:
        if title.endswith(suffix):
            title = title[: -len(suffix)]
    return " ".join(title.split()).strip() or "Untitled news item"


class HtmlNewsPlugin:
    key = "html-news"
    name = "HTML News"
    description = "Fetches official news article pages from a static HTML listing page."
    config_schema = {
        "type": "object",
        "required": ["url", "article_path_prefix"],
        "properties": {
            "url": {"type": "string", "format": "uri"},
            "article_path_prefix": {"type": "string"},
            "exclude_path_prefixes": {"items": {"type": "string"}, "type": "array"},
            "title_suffixes": {"items": {"type": "string"}, "type": "array"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "user_agent": {"type": "string"},
        },
        "additionalProperties": True,
    }

    def fetch(self, source: Source, window: TimeWindow) -> list[ChangeInput]:
        url = _string_config(source.config, "url")
        if url is None:
            raise PluginConfigError("html-news source config requires a non-empty url")
        article_prefix = _string_config(source.config, "article_path_prefix")
        if article_prefix is None:
            raise PluginConfigError("html-news source config requires a non-empty article_path_prefix")
        exclude_prefixes = _string_list_config(source.config, "exclude_path_prefixes")
        title_suffixes = _string_list_config(source.config, "title_suffixes")
        limit = _limit_config(source.config)
        enrichment_profile_name = source.config.get("enrichment_profile")
        if enrichment_profile_name is not None and not isinstance(enrichment_profile_name, str):
            raise PluginConfigError("html-news config 'enrichment_profile' must be a string")
        enrichment_profile = profile_for(url, enrichment_profile_name)

        headers = {"User-Agent": source.config.get("user_agent", "changelorg/0.1")}
        with httpx.Client(follow_redirects=True, timeout=20.0, headers=headers) as client:
            index_response = client.get(url)
            index_response.raise_for_status()
            links = _article_links(index_response.text, url, [article_prefix], exclude_prefixes)[:limit]

            changes: list[ChangeInput] = []
            for link in links:
                article_response = client.get(link)
                article_response.raise_for_status()
                html = article_response.text
                published_at = _published_at(html, window.end)
                if published_at < window.start or published_at > window.end:
                    continue
                title = _title(html, title_suffixes)
                summary = compact_blocks(html, enrichment_profile) or plain_text(html)
                changes.append(
                    ChangeInput(
                        external_id=link,
                        title=title,
                        url=link,
                        summary=summary,
                        content="",
                        published_at=published_at,
                        metadata={
                            "feed_url": url,
                            "enrichment_profile": enrichment_profile.name,
                            "quality_flags": ["official_html_news"],
                            "raw_title": title,
                        },
                    )
                )
        return changes
