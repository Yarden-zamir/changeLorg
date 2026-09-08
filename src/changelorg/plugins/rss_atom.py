from __future__ import annotations

import calendar
from datetime import datetime, timezone
from typing import Any

import feedparser

from changelorg.enrichment import PROFILES, enrich_change, profile_for
from changelorg.models import ChangeInput, Source, TimeWindow
from changelorg.network import (
    MAX_ENTRIES,
    PublicFetcher,
    PublicFetchError,
    RequestBudgetExceeded,
    validate_public_url,
    validate_source_config,
)
from changelorg.plugin import PluginConfigError


def _entry_value(entry: Any, key: str) -> Any:
    if hasattr(entry, "get"):
        return entry.get(key)
    return getattr(entry, key, None)


def _entry_link(entry: Any) -> str | None:
    link = _entry_value(entry, "link")
    if isinstance(link, str) and link:
        return link
    links = _entry_value(entry, "links")
    if isinstance(links, list):
        for item in links:
            href = item.get("href") if isinstance(item, dict) else getattr(item, "href", None)
            if isinstance(href, str) and href:
                return href
    return None


def _entry_datetime(entry: Any, window: TimeWindow) -> datetime:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = _entry_value(entry, key)
        if parsed:
            return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
    return window.end


def _string_list_config(config: dict[str, Any], key: str) -> list[str]:
    value = config.get(key, [])
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise PluginConfigError(f"rss-atom config {key!r} must be a string or list of strings")


def _matches_filters(text: str, include_any: list[str], exclude_any: list[str]) -> bool:
    normalized = text.lower()
    if include_any and not any(term.lower() in normalized for term in include_any):
        return False
    if exclude_any and any(term.lower() in normalized for term in exclude_any):
        return False
    return True


def _metadata_text(value: str, limit: int = 5000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


class RssAtomPlugin:
    key = "rss-atom"
    name = "RSS / Atom"
    description = "Fetches entries from RSS and Atom feeds."
    config_schema: dict[str, object] = {
        "type": "object",
        "required": ["url"],
        "properties": {
            "url": {"type": "string", "format": "uri"},
            "user_agent": {"type": "string"},
            "profile": {"type": "string"},
            "enrichment_profile": {"type": "string", "enum": list(PROFILES)},
            "include_any": {
                "description": "Only keep entries containing at least one of these case-insensitive terms.",
                "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
            },
            "exclude_any": {
                "description": "Drop entries containing any of these case-insensitive terms.",
                "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
            },
        },
        "additionalProperties": False,
    }

    def fetch(self, source: Source, window: TimeWindow) -> list[ChangeInput]:
        validate_source_config(source)
        url = source.config.get("url")
        if not isinstance(url, str) or not url.strip():
            raise PluginConfigError("rss-atom source config requires a non-empty url")
        include_any = _string_list_config(source.config, "include_any")
        exclude_any = _string_list_config(source.config, "exclude_any")
        enrichment_profile_name = source.config.get("enrichment_profile")
        if enrichment_profile_name is not None and not isinstance(enrichment_profile_name, str):
            raise PluginConfigError("rss-atom config 'enrichment_profile' must be a string")
        enrichment_profile = profile_for(url, enrichment_profile_name)

        client = PublicFetcher(user_agent=source.config.get("user_agent", "changelorg/0.1"))
        response = client.get(url)
        enrichment_budget_exhausted = False

        def fetch_link(link: str) -> str | None:
            nonlocal enrichment_budget_exhausted
            try:
                return client.get(str(validate_public_url(link, base_url=response.url))).text
            except RequestBudgetExceeded:
                enrichment_budget_exhausted = True
                return None

        parsed_feed = feedparser.parse(response.content)
        feed_title = str(getattr(parsed_feed.feed, "title", "")) if hasattr(parsed_feed, "feed") else ""
        entries = getattr(parsed_feed, "entries", [])
        if not entries and getattr(parsed_feed, "bozo", False):
            raise PublicFetchError("Source feed could not be parsed")

        changes: list[ChangeInput] = []
        for entry in entries[:MAX_ENTRIES]:
            client.check_deadline()
            published_at = _entry_datetime(entry, window)
            if published_at < window.start or published_at > window.end:
                continue

            link = _entry_link(entry)
            title = _entry_value(entry, "title") or link or "Untitled feed entry"
            summary = _entry_value(entry, "summary") or _entry_value(entry, "description") or ""
            content = ""
            content_items = _entry_value(entry, "content")
            if isinstance(content_items, list) and content_items:
                first = content_items[0]
                value = first.get("value") if isinstance(first, dict) else getattr(first, "value", None)
                content = value if isinstance(value, str) else ""
            searchable_text = "\n".join([str(title), link or "", str(summary), content])
            if not _matches_filters(searchable_text, include_any, exclude_any):
                continue
            enriched = enrich_change(
                title=str(title),
                summary=str(summary),
                content=content,
                url=link,
                feed_title=feed_title,
                profile=enrichment_profile,
                fetch_link=fetch_link,
            )
            if enrichment_budget_exhausted:
                enriched.quality_flags.append("enrichment_budget_exhausted")

            external_id = _entry_value(entry, "id") or _entry_value(entry, "guid") or link
            changes.append(
                ChangeInput(
                    external_id=str(external_id) if external_id else None,
                    title=enriched.title,
                    url=link,
                    summary=enriched.summary,
                    content=enriched.content,
                    published_at=published_at,
                    metadata={
                        "feed_url": url,
                        "enrichment_profile": enriched.profile,
                        "quality_flags": enriched.quality_flags,
                        "raw_title": _metadata_text(str(title)),
                        "raw_summary": _metadata_text(str(summary)),
                        "raw_content": _metadata_text(content),
                    },
                )
            )
        client.check_deadline()
        return changes
