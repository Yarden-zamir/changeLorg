"""Best-effort reader for X's public embedded timeline. No keys or login cookies."""

import html
import json
from datetime import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

import httpx

from changelorg.models import ChangeInput, Source, TimeWindow
from changelorg.network import (
    MAX_ENTRIES,
    PublicFetcher,
    PublicFetchError,
    validate_source_config,
)
from changelorg.social import x_username


class XUnavailable(PublicFetchError):
    """The public timeline could not be read; never pretend it is empty."""


class _TimelineData(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.active = dict(attrs).get("id") == "__NEXT_DATA__"

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


class XPlugin:
    key = "x"
    name = "X public posts"
    description = "Keyless public embedded timeline. Availability and recent-post coverage depend on X."
    config_schema: dict[str, object] = {
        "type": "object",
        "required": ["url"],
        "additionalProperties": False,
        "properties": {
            "url": {"type": "string"},
            "profile": {"type": "string"},
            "include_any": {"type": "array", "items": {"type": "string"}},
            "exclude_any": {"type": "array", "items": {"type": "string"}},
        },
    }

    def fetch(self, source: Source, window: TimeWindow) -> list[ChangeInput]:
        validate_source_config(source)
        username = x_username(httpx.URL(str(source.config["url"])))
        client = PublicFetcher()
        try:
            response = client.get(
                f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{username}"
            )
            parser = _TimelineData()
            parser.feed(response.text)
            parser.close()
            payload = json.loads("".join(parser.parts))
            entries = payload["props"]["pageProps"]["timeline"]["entries"]
            if not isinstance(entries, list):
                raise ValueError
        except (PublicFetchError, ValueError, KeyError, TypeError):
            raise XUnavailable(
                "X public timeline is unavailable or rate-limited. No keyless timeline could be read."
            ) from None

        def terms(key: str) -> list[str]:
            value = source.config.get(key, [])
            return [
                term.strip().casefold()
                for term in (value.splitlines() if isinstance(value, str) else value)
                if term.strip()
            ]

        include, exclude = terms("include_any"), terms("exclude_any")
        changes = []
        seen = set()
        for entry in entries[:MAX_ENTRIES]:
            client.check_deadline()
            if not isinstance(entry, dict):
                raise XUnavailable("X returned an invalid public timeline")
            if entry.get("type") != "tweet":
                continue
            try:
                post = entry["content"]["tweet"]
                post_id = post["id_str"]
                text = post.get("full_text") or post["text"]
                if (
                    not isinstance(post_id, str)
                    or not post_id.isascii()
                    or not post_id.isdigit()
                    or not isinstance(text, str)
                ):
                    raise ValueError
                date = post["created_at"]
                try:
                    published = datetime.fromisoformat(date.replace("Z", "+00:00"))
                except ValueError:
                    published = parsedate_to_datetime(date)
                if published.tzinfo is None:
                    raise ValueError
            except (KeyError, TypeError, ValueError, AttributeError):
                raise XUnavailable("X returned an invalid public post") from None
            if post_id in seen or not window.start <= published <= window.end:
                continue
            seen.add(post_id)
            if (
                include and not any(term in text.casefold() for term in include)
            ) or any(term in text.casefold() for term in exclude):
                continue
            changes.append(
                ChangeInput(
                    external_id=f"x:{post_id}",
                    title=" ".join(text.split())[:160] or f"Post by @{username}",
                    url=f"https://x.com/{username}/status/{post_id}",
                    summary=text,
                    content="<p>" + html.escape(text).replace("\n", "<br>") + "</p>",
                    published_at=published,
                    metadata={
                        "platform": "x",
                        "username": username,
                        "provider": "public-embed",
                    },
                )
            )
        client.check_deadline()
        return changes
