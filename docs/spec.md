# changelorg Behavior Spec

## Product Goal

changelorg tracks changes from different source types and generates a time-windowed feed on demand. The feed can be consumed directly by a human, rendered by the website, or exported as structured data for AI-assisted analysis and summaries.

## Data Model

### Source

A source is a user subscription.

Required fields:

- `name`: Human-readable label.
- `plugin`: Plugin key that knows how to fetch this source.
- `config`: Plugin-specific JSON object.
- `enabled`: Disabled sources are skipped during generation.

Behavior:

- Sources are stored in SQLite.
- Deleting a source deletes its cached changes.
- Source IDs are stable within the local database.

### Change

A change is a normalized item returned by a plugin.

Required fields:

- `title`
- `published_at`

Optional fields:

- `external_id`
- `url`
- `summary`
- `content`
- `metadata`
- `dismissed`
- `saved`
- `note`

Behavior:

- Changes are cached in SQLite.
- Changes dedupe by `(source_id, external_id)`.
- If a plugin does not provide `external_id`, changelorg derives one from source ID, URL, title, and publication time.
- Re-fetching a known change updates its title, URL, text, publication time, fetched time, and metadata.
- Re-fetching a known change does not overwrite dismissed, saved, or note state.
- Dismissed changes are hidden from normal feed responses by default.

## Time Windows

CLI accepts:

- Relative durations: `30m`, `24h`, `7d`, `2w`.
- ISO dates: `2026-07-04`.
- ISO datetimes: `2026-07-04T12:00:00Z`.

API accepts the same formats for `since` and ISO date/datetime for `until`.

Behavior:

- Default generation window is the last 7 days.
- `until` defaults to now.
- Naive datetimes are treated as UTC.
- `end` must be after `start`.

## Plugin System

### Loading

Built-in plugins are loaded first. Local plugins are loaded from:

- `./plugins`
- `~/.config/changelorg/plugins`
- Paths in `CHANGELORG_PLUGIN_PATH`

Local plugin files:

- Must be Python files ending in `.py`.
- Files starting with `_` are ignored.
- Must expose either `plugin` or `get_plugin()`.
- Later plugins with the same key replace earlier plugins.

### Plugin Contract

A plugin object must provide:

```python
key: str
name: str
description: str
config_schema: dict[str, object]

def fetch(source: Source, window: TimeWindow) -> list[ChangeInput]: ...
```

Behavior:

- `fetch` returns normalized `ChangeInput` values.
- `fetch` should only return changes inside the requested window.
- Invalid source config should raise a clear exception.
- One failing source must not stop other sources from generating changes.

### Built-In RSS/Atom Plugin

Key: `rss-atom`

Required config:

- `url`: RSS or Atom feed URL.

Optional config:

- `user_agent`: Custom HTTP user agent.
- `include_any`: String or list of case-insensitive terms. If set, only matching entries are kept.
- `exclude_any`: String or list of case-insensitive terms. If set, matching entries are dropped.

Behavior:

- Fetches the feed over HTTP with redirects enabled.
- Parses RSS and Atom entries.
- Uses published, updated, or created timestamps when available.
- Entries without timestamps are assigned the generation window end time.
- Filters entries to the requested time window.
- Applies `include_any` before `exclude_any` against title, URL, summary, and content.

### Enrichment

RSS/Atom items are passed through a generic enrichment layer before caching.

Behavior:

- Enrichment profiles are defined in Python code using a small typed DSL, not JSON blobs.
- Source config may opt into a named profile with `enrichment_profile`, but known feed URLs can also map to profile presets automatically.
- Raw feed title, summary, content, quality flags, and selected profile are stored in change metadata.
- Version-only titles such as `v0.115.1` are replaced with the first meaningful non-header release-note line when available.
- Thin or generic summaries such as `Learn what's new...` can trigger linked-page fetching for profiles that allow it.
- Overloaded release bodies are compacted to the first meaningful bullets or paragraphs.
- Common boilerplate such as changelog compare links, release dates, download lists, social links, and feed footer text is dropped when detected.

## CLI Behavior

Base command: `changelorg`.

Commands:

- `changelorg init`: Create/update the SQLite schema.
- `changelorg plugins`: List available plugins and plugin directories.
- `changelorg serve`: Run the FastAPI backend.
- `changelorg sources add NAME --plugin KEY --config key=value`: Add any plugin-backed source.
- `changelorg sources add-rss NAME URL`: Convenience command for RSS/Atom.
- `changelorg sources list`: List sources.
- `changelorg sources remove ID`: Delete a source.
- `changelorg sources enable ID`: Enable a source.
- `changelorg sources disable ID`: Disable a source.
- `changelorg changes generate --since 7d`: Fetch enabled sources, cache results, and print changes.
- `changelorg changes list`: Print cached changes without fetching.

Behavior:

- `--db PATH` overrides the SQLite database path.
- `--json` returns structured JSON where supported.
- Generation continues when one source fails and reports source-level errors.

## API Behavior

Routes:

- `GET /health`
- `GET /plugins`
- `GET /sources`
- `POST /sources`
- `GET /sources/{source_id}`
- `PATCH /sources/{source_id}`
- `DELETE /sources/{source_id}`
- `POST /changes/generate`
- `GET /changes`
- `PATCH /changes/{change_id}`

Behavior:

- Startup creates the SQLite schema.
- CORS is open for local frontend development.
- `POST /changes/generate` fetches sources, caches changes, and returns generated results plus source-level errors.
- `GET /changes` reads cached changes only.
- `GET /changes` hides dismissed changes unless `include_dismissed=true` is provided.
- `GET /changes?saved=true` returns saved changes only.
- `PATCH /changes/{change_id}` updates `dismissed`, `saved`, and/or `note`.

## Frontend Behavior

The first frontend is intentionally minimal.

Behavior:

- Loads cached changes from the backend.
- Can request generation for the last 7 days.
- Renders HTML and Markdown safely in card previews.
- Renders changes as card-style items with source, publication time, title, summary/content preview, and link.
- Supports sorting by newest, oldest, source, and saved-first.
- Supports dismissing entries from the normal feed.
- Supports saving entries for later.
- Shows saved entries in a notes section at the top.
- The note-for-later control includes an arrow that expands a text box for annotating the saved entry.
- Shows an empty state when no changes are cached.

## Non-Goals For This Version

- Background scheduling.
- Authentication.
- Multi-user accounts.
- Twitter/X support.
- GitHub releases support.
- Hosted deployment configuration.
