from __future__ import annotations

from changelorg.models import SourceCreate
from changelorg.store import add_source, list_sources


DEFAULT_SOURCES: list[SourceCreate] = [
    SourceCreate(name="Github Blog Changelog", plugin="rss-atom", config={"profile": "dev", "url": "https://github.blog/changelog/feed/"}),
    SourceCreate(name="GitHub Blog", plugin="rss-atom", config={"profile": "dev", "url": "https://github.blog/feed/"}),
    SourceCreate(name="GitHub Status", plugin="rss-atom", config={"profile": "dev", "url": "https://www.githubstatus.com/history.rss"}),
    SourceCreate(name="ty Releases", plugin="rss-atom", config={"profile": "dev", "url": "https://github.com/astral-sh/ty/releases.atom"}),
    SourceCreate(name="uv Releases", plugin="rss-atom", config={"profile": "dev", "url": "https://github.com/astral-sh/uv/releases.atom"}),
    SourceCreate(name="Python Releases", plugin="rss-atom", config={"profile": "dev", "url": "https://www.python.org/downloads/feed.rss"}),
    SourceCreate(name="Python Insider", plugin="rss-atom", config={"profile": "dev", "url": "https://blog.python.org/feeds/posts/default"}),
    SourceCreate(name="GitHub Desktop Releases", plugin="rss-atom", config={"profile": "dev", "url": "https://github.com/desktop/desktop/releases.atom"}),
    SourceCreate(name="iTerm2 Stable", plugin="rss-atom", config={"profile": "dev", "url": "https://iterm2.com/appcasts/final.xml"}),
    SourceCreate(name="IntelliJ IDEA Blog", plugin="rss-atom", config={"profile": "dev", "url": "https://blog.jetbrains.com/idea/feed/"}),
    SourceCreate(name="Slack API Changelog", plugin="rss-atom", config={"profile": "dev", "url": "https://api.slack.com/changelog.rss"}),
    SourceCreate(name="Slack Developer Docs Blog", plugin="rss-atom", config={"profile": "dev", "url": "https://docs.slack.dev/changelog/rss.xml"}),
    SourceCreate(name="VS Code Releases", plugin="rss-atom", config={"profile": "dev", "url": "https://github.com/microsoft/vscode/releases.atom"}),
    SourceCreate(name="VS Code Feed", plugin="rss-atom", config={"profile": "dev", "url": "https://code.visualstudio.com/feed.xml"}),
    SourceCreate(name="FastAPI Releases", plugin="rss-atom", config={"profile": "dev", "url": "https://github.com/fastapi/fastapi/releases.atom"}),
    SourceCreate(name="OpenAI News", plugin="rss-atom", config={"profile": "dev", "url": "https://openai.com/news/rss.xml"}),
    SourceCreate(name="OpenAI Python SDK Releases", plugin="rss-atom", config={"profile": "dev", "url": "https://github.com/openai/openai-python/releases.atom"}),
    SourceCreate(name="Anthropic Python SDK Releases", plugin="rss-atom", config={"profile": "dev", "url": "https://github.com/anthropics/anthropic-sdk-python/releases.atom"}),
    SourceCreate(
        name="Gemini and Gemma on Google Developers Blog",
        plugin="rss-atom",
        config={
            "url": "https://developers.googleblog.com/feeds/posts/default?alt=rss",
            "profile": "dev",
            "include_any": ["gemini", "gemma", "google ai studio", "gemini api", "diffusiongemma"],
        },
    ),
    SourceCreate(name="Google Gen AI Python SDK Releases", plugin="rss-atom", config={"profile": "dev", "url": "https://github.com/googleapis/python-genai/releases.atom"}),
    SourceCreate(name="GitHub CLI Releases", plugin="rss-atom", config={"profile": "dev", "url": "https://github.com/cli/cli/releases.atom"}),
    SourceCreate(name="OpenCode Releases", plugin="rss-atom", config={"profile": "dev", "url": "https://github.com/anomalyco/opencode/releases.atom"}),
    SourceCreate(name="DuckDB Releases", plugin="rss-atom", config={"profile": "dev", "url": "https://github.com/duckdb/duckdb/releases.atom"}),
    SourceCreate(name="Raycast Changelog", plugin="rss-atom", config={"profile": "dev", "url": "https://raycast.com/changelog/feed.xml"}),
    SourceCreate(name="Steam News", plugin="rss-atom", config={"profile": "games", "url": "https://store.steampowered.com/feeds/news.xml?cc=US&l=english"}),
    SourceCreate(name="Minecraft News", plugin="rss-atom", config={"profile": "games", "url": "https://www.minecraft.net/en-us/feeds/community-content/rss"}),
    SourceCreate(name="THE FINALS News", plugin="rss-atom", config={"profile": "games", "url": "https://store.steampowered.com/feeds/news/app/2073850/?cc=US&l=english"}),
    SourceCreate(name="ARC Raiders News", plugin="rss-atom", config={"profile": "games", "url": "https://store.steampowered.com/feeds/news/app/1808500/?cc=US&l=english"}),
]


def seed_default_sources() -> int:
    existing_urls = {
        source.config.get("url")
        for source in list_sources()
        if source.plugin == "rss-atom" and isinstance(source.config.get("url"), str)
    }
    added = 0
    for source in DEFAULT_SOURCES:
        url = source.config.get("url")
        if not isinstance(url, str) or url in existing_urls:
            continue
        add_source(source)
        existing_urls.add(url)
        added += 1
    return added
