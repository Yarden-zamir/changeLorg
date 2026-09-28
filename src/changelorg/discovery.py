"""Discover RSS/Atom drafts without a candidate crawl or persistence."""

from html.parser import HTMLParser
from string import ascii_letters, digits
from urllib.parse import urlsplit

import httpx

from changelorg.feeds import fetch_feed, github_release_repository
from changelorg.models import SourceCreate
from changelorg.network import PublicFetcher, PublicFetchError, validate_public_url
from changelorg.social import X_HOSTS, social_url, x_username


def normalize_discovery_url(value: str) -> httpx.URL:
    """Return a public canonical target without DNS. Invalid input raises ValueError."""
    value = value.strip()
    try:
        scheme = urlsplit(value).scheme
    except ValueError:
        raise ValueError("Invalid discovery URL") from None
    bare = not scheme or (
        "." in scheme and not value.lower().startswith(scheme + "://")
    )
    shorthand = bare and "." not in value.partition("/")[0]
    if shorthand:
        if len(value.removesuffix("/").split("/")) != 2 or any(
            char not in ascii_letters + digits + "._-/" for char in value
        ):
            raise ValueError("Enter a public website URL or GitHub owner/repo")
        value = "https://github.com/" + value
    elif bare:
        value = "https://" + value
    try:
        url = validate_public_url(value)
    except PublicFetchError as exc:
        raise ValueError(str(exc)) from None
    if bare and not shorthand:
        labels = url.raw_host.decode("ascii").rstrip(".").split(".")
        if len(labels) < 2 or any(
            not label
            or label.startswith("-")
            or label.endswith("-")
            or any(char not in ascii_letters + digits + "-" for char in label)
            for label in labels
        ):
            raise ValueError("Enter a public domain with a dot and no spaces")
    if url.host not in {"github.com", "www.github.com"}:
        return social_url(url)

    parts = url.path.removeprefix("/").rstrip("/").split("/")
    if len(parts) < 2:
        raise ValueError("Enter a GitHub repository URL with owner/repo")
    owner, repository = parts[:2]
    repository = repository.removesuffix(".git")
    if (
        not owner
        or any(char not in ascii_letters + digits + "-" for char in owner)
        or repository in {"", ".", ".."}
        or any(char not in ascii_letters + digits + "._-" for char in repository)
        or shorthand
        and len(parts) != 2
    ):
        raise ValueError("Invalid GitHub owner/repo")
    suffix = parts[2:]
    if suffix in (["releases.atom"], ["tags.atom"], ["commits.atom"]):
        return url
    if suffix[:1] == ["commits"] and len(suffix) >= 2 and suffix[-1].endswith(".atom"):
        branch = "/".join(suffix[1:]).removesuffix(".atom")
        if (
            branch
            and branch != "@"
            and all(
                part and not part.startswith(".") and not part.endswith((".", ".lock"))
                for part in branch.split("/")
            )
            and ".." not in branch
            and "@{" not in branch
            and not any(
                char in "~^:?*[\\"
                or char.isspace()
                or ord(char) < 32
                or ord(char) == 127
                for char in branch
            )
        ):
            return url
        raise ValueError("Invalid GitHub commit branch")
    if suffix == ["tags"]:
        endpoint = "tags.atom"
    elif suffix == ["commits"]:
        endpoint = "commits.atom"
    elif suffix in ([], ["releases"], ["releases", "latest"]) or (
        suffix[:2] == ["releases", "tag"] and len(suffix) > 2 and all(suffix[2:])
    ):
        endpoint = "releases.atom"
    else:
        raise ValueError(
            "Unsupported GitHub path; use a repository, releases, tags, or commits feed URL"
        )
    return httpx.URL(f"https://github.com/{owner}/{repository}/{endpoint}")


class _DiscoveryParser(HTMLParser):
    def __init__(self, url: httpx.URL) -> None:
        super().__init__()
        self.url = url
        self.base: httpx.URL | None = None
        self.links: list[tuple[str, str]] = []
        self.title: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self.in_title = True
        if tag not in {"base", "link"}:
            return
        data = dict(attrs)
        href = data.get("href")
        if not href:
            return
        if tag == "base" and self.base is None:
            try:
                self.base = validate_public_url(href, base_url=self.url)
            except PublicFetchError:
                pass
        if (
            tag == "link"
            and "alternate" in (data.get("rel") or "").lower().split()
            and (data.get("type") or "").partition(";")[0].strip().lower()
            in {"application/rss+xml", "application/atom+xml"}
        ):
            self.links.append((href, data.get("title") or ""))

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title.append(data)


class _NameParser(HTMLParser):
    # The enrichment text helper only retains block elements, not inline source titles.
    def __init__(self) -> None:
        super().__init__()
        self.text: list[str] = []

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def _source(url: httpx.URL, *titles: str) -> SourceCreate:
    name = ""
    for title in titles:
        parser = _NameParser()
        parser.feed(title)
        parser.close()
        name = " ".join("".join(parser.text).split())
        if name:
            break
    return SourceCreate(
        plugin="rss-atom",
        name=(name or url.host + url.path)[:200],
        config={"url": str(url)},
        enabled=True,
    )


def discover_sources(value: str) -> list[SourceCreate]:
    """Read a target with repository fallback. Preview verifies alternates. The API owns the shared fetch slot."""
    url = normalize_discovery_url(value)
    if url.host in X_HOSTS:
        return [SourceCreate(name=f"@{x_username(url)} on X", plugin="x", config={"url": str(url)}, enabled=True)]
    client = PublicFetcher()
    response, parsed, _ = fetch_feed(client, str(url))
    if parsed.get("version"):
        title = parsed.feed.get("title", "")
        repository = github_release_repository(url)
        result = [
            _source(url, f"{repository} updates")
            if repository is not None
            else _source(response.url, title if isinstance(title, str) else "")
        ]
    else:
        parser = _DiscoveryParser(response.url)
        parser.feed(response.text)
        parser.close()
        result = []
        seen: set[httpx.URL] = set()
        for href, title in parser.links:
            client.check_deadline()
            try:
                candidate = validate_public_url(
                    href, base_url=parser.base or response.url
                )
            except PublicFetchError:
                continue
            if candidate in seen:
                continue
            seen.add(candidate)
            result.append(_source(candidate, title, "".join(parser.title)))
            if len(result) == 10:
                break
    client.check_deadline()
    return result
