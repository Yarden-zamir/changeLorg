# Follow Source Research

Verified on 2026-07-06. Prefer the RSS/Atom-compatible URLs first because they work with the current `rss-atom` plugin.

## Ready With Current RSS/Atom Plugin

| Topic | Best source | URL | Notes |
| --- | --- | --- | --- |
| GitHub | GitHub Changelog | `https://github.blog/changelog/feed/` | Product and platform changelog. |
| GitHub | GitHub Blog | `https://github.blog/feed/` | Broader engineering/product news. |
| GitHub | GitHub Status | `https://www.githubstatus.com/history.rss` | Incident history, useful but not a changelog. |
| ty | GitHub releases | `https://github.com/astral-sh/ty/releases.atom` | Release notes for Astral's Python type checker. |
| uv | GitHub releases | `https://github.com/astral-sh/uv/releases.atom` | Release notes for uv. |
| Python | Python releases | `https://www.python.org/downloads/feed.rss` | Official Python release feed. |
| Python | Python Insider | `https://blog.python.org/feeds/posts/default` | Official Python blog. |
| GitHub Desktop | GitHub releases | `https://github.com/desktop/desktop/releases.atom` | Desktop app release notes. |
| iTerm2 | Final appcast | `https://iterm2.com/appcasts/final.xml` | Stable iTerm2 release feed. |
| iTerm2 | Test appcast | `https://iterm2.com/appcasts/testing.xml` | Beta/test release feed. |
| IntelliJ IDEA | JetBrains IDEA Blog | `https://blog.jetbrains.com/idea/feed/` | Product news and release announcements. |
| Slack | Slack API Changelog | `https://api.slack.com/changelog.rss` | Slack platform/API changes. |
| Slack | Slack Developer Docs Blog | `https://docs.slack.dev/changelog/rss.xml` | Developer docs changelog. |
| VS Code | GitHub releases | `https://github.com/microsoft/vscode/releases.atom` | VS Code release tags. |
| VS Code | VS Code site feed | `https://code.visualstudio.com/feed.xml` | Includes release notes and site updates. |
| FastAPI | GitHub releases | `https://github.com/fastapi/fastapi/releases.atom` | FastAPI release notes. |
| GitHub CLI | GitHub releases | `https://github.com/cli/cli/releases.atom` | GitHub CLI release notes. |
| OpenCode | GitHub releases | `https://github.com/sst/opencode/releases.atom` | OpenCode release notes. |
| DuckDB | GitHub releases | `https://github.com/duckdb/duckdb/releases.atom` | DuckDB release notes. |
| Raycast | Changelog feed | `https://raycast.com/changelog/feed.xml` | Hidden feed advertised in the changelog page head. |
| OpenAI | OpenAI News | `https://openai.com/news/rss.xml` | Company/product news. |
| OpenAI | OpenAI Python SDK releases | `https://github.com/openai/openai-python/releases.atom` | Python SDK release notes. |
| Anthropic | Anthropic Python SDK releases | `https://github.com/anthropics/anthropic-sdk-python/releases.atom` | Python SDK release notes. |
| Gemini/Gemma | Google Developers Blog | `https://developers.googleblog.com/feeds/posts/default?alt=rss` | Use `include_any` filters for `gemini`, `gemma`, `google ai studio`, `gemini api`, and `diffusiongemma`. |
| Gemini | Google Gen AI Python SDK releases | `https://github.com/googleapis/python-genai/releases.atom` | Python SDK release notes. |

## Suggested CLI Adds

```sh
uv run changelorg sources add-rss "GitHub Changelog" "https://github.blog/changelog/feed/"
uv run changelorg sources add-rss "GitHub Blog" "https://github.blog/feed/"
uv run changelorg sources add-rss "GitHub Status" "https://www.githubstatus.com/history.rss"
uv run changelorg sources add-rss "ty Releases" "https://github.com/astral-sh/ty/releases.atom"
uv run changelorg sources add-rss "uv Releases" "https://github.com/astral-sh/uv/releases.atom"
uv run changelorg sources add-rss "Python Releases" "https://www.python.org/downloads/feed.rss"
uv run changelorg sources add-rss "Python Insider" "https://blog.python.org/feeds/posts/default"
uv run changelorg sources add-rss "GitHub Desktop Releases" "https://github.com/desktop/desktop/releases.atom"
uv run changelorg sources add-rss "iTerm2 Stable" "https://iterm2.com/appcasts/final.xml"
uv run changelorg sources add-rss "IntelliJ IDEA Blog" "https://blog.jetbrains.com/idea/feed/"
uv run changelorg sources add-rss "Slack API Changelog" "https://api.slack.com/changelog.rss"
uv run changelorg sources add-rss "Slack Developer Docs Blog" "https://docs.slack.dev/changelog/rss.xml"
uv run changelorg sources add-rss "VS Code Releases" "https://github.com/microsoft/vscode/releases.atom"
uv run changelorg sources add-rss "VS Code Feed" "https://code.visualstudio.com/feed.xml"
uv run changelorg sources add-rss "FastAPI Releases" "https://github.com/fastapi/fastapi/releases.atom"
uv run changelorg sources add-rss "GitHub CLI Releases" "https://github.com/cli/cli/releases.atom"
uv run changelorg sources add-rss "OpenCode Releases" "https://github.com/sst/opencode/releases.atom"
uv run changelorg sources add-rss "DuckDB Releases" "https://github.com/duckdb/duckdb/releases.atom"
uv run changelorg sources add-rss "Raycast Changelog" "https://raycast.com/changelog/feed.xml"
uv run changelorg sources add-rss "OpenAI News" "https://openai.com/news/rss.xml"
uv run changelorg sources add-rss "OpenAI Python SDK Releases" "https://github.com/openai/openai-python/releases.atom"
uv run changelorg sources add-rss "Anthropic Python SDK Releases" "https://github.com/anthropics/anthropic-sdk-python/releases.atom"
uv run changelorg sources add "Gemini and Gemma on Google Developers Blog" --plugin rss-atom --config url="https://developers.googleblog.com/feeds/posts/default?alt=rss" --config include_any=gemini --config include_any=gemma --config include_any="google ai studio" --config include_any="gemini api" --config include_any=diffusiongemma
uv run changelorg sources add-rss "Google Gen AI Python SDK Releases" "https://github.com/googleapis/python-genai/releases.atom"
```

## Games Profile Sources

These sources are seeded under the `games` profile.

| Topic | Source | URL | Notes |
| --- | --- | --- | --- |
| Steam | Steam News | `https://store.steampowered.com/feeds/news.xml?cc=US&l=english` | Broad Steam news. |
| Minecraft | Minecraft RSS | `https://www.minecraft.net/en-us/feeds/community-content/rss` | Official Minecraft posts and preview notes. |
| THE FINALS | Steam app news | `https://store.steampowered.com/feeds/news/app/2073850/?cc=US&l=english` | Official Steam news for THE FINALS. |
| ARC Raiders | Steam app news | `https://store.steampowered.com/feeds/news/app/1808500/?cc=US&l=english` | Official Steam news for ARC Raiders. |

## Better Future Plugins

These are valuable sources but are not RSS/Atom feeds, so they should get dedicated scraper plugins.

| Topic | Page | Why a plugin is needed |
| --- | --- | --- |
| IntelliJ IDEA | `https://www.jetbrains.com/idea/whatsnew/` | Official release highlights page is HTML, not a feed. |
| IntelliJ IDEA | `https://www.jetbrains.com/updates/updates.xml` | JetBrains updates XML is parseable, but it is not RSS/Atom and needs product filtering. |
| Slack desktop app | `https://slack.com/release-notes/mac` | Slack redirects release notes to platform-specific HTML pages. |
| OpenAI API | `https://developers.openai.com/api/docs/changelog` | Official API changelog is HTML. |
| Anthropic API | `https://platform.claude.com/docs/en/release-notes/overview` | Official release notes are HTML. |
| Gemini API | `https://ai.google.dev/gemini-api/docs/changelog` | Official Gemini API changelog is HTML. |

## Optional High-Volume Feeds

These work today but may be too noisy for normal use.

```sh
uv run changelorg sources add-rss "ty Main Commits" "https://github.com/astral-sh/ty/commits/main.atom"
uv run changelorg sources add-rss "uv Main Commits" "https://github.com/astral-sh/uv/commits/main.atom"
uv run changelorg sources add-rss "iTerm2 Testing" "https://iterm2.com/appcasts/testing.xml"
```
