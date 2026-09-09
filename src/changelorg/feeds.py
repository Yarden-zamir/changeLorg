"""Shared feed reads for discovery and saved-source refreshes."""

from string import ascii_letters, digits

import feedparser
import httpx

from changelorg.network import PublicFetcher, PublicFetchError, validate_public_url


def github_release_repository(url: httpx.URL) -> str | None:
    parts = url.raw_path.partition(b"?")[0].decode("ascii").split("/")
    if (
        url.host not in {"github.com", "www.github.com"}
        or len(parts) != 4
        or parts[3] != "releases.atom"
        or not parts[1]
        or any(char not in ascii_letters + digits + "-" for char in parts[1])
        or parts[2] in {"", ".", ".."}
        or any(char not in ascii_letters + digits + "._-" for char in parts[2])
    ):
        return None
    return "/".join(parts[1:3])


def fetch_feed(
    client: PublicFetcher, url: str
) -> tuple[httpx.Response, feedparser.FeedParserDict, bool]:
    target = validate_public_url(url)
    response = client.get(str(target))
    parsed = feedparser.parse(response.content)
    client.check_deadline()
    repository = github_release_repository(target)
    fallback = False
    if repository is not None:
        if parsed.get("version") != "atom10" or parsed.get("bozo"):
            raise PublicFetchError("Source release feed could not be parsed")
        final_repository = github_release_repository(response.url)
        # Query filters can hide releases. Revisit only with an unfiltered release check.
        if (
            not parsed.entries
            and not target.query
            and not response.url.query
            and final_repository is not None
        ):
            response = client.get(f"https://github.com/{final_repository}/commits.atom")
            parsed = feedparser.parse(response.content)
            client.check_deadline()
            if parsed.get("version") != "atom10" or parsed.get("bozo"):
                raise PublicFetchError("Source commit feed could not be parsed")
            fallback = True
    return response, parsed, fallback
