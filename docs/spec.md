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

Common config fields:

- `profile`: Subscription profile/category. Defaults to `dev` when omitted.

Behavior:

- Sources are stored in SQLite.
- Deleting a source deletes its cached changes.
- Source IDs are stable within the local database.
- Seeded sources currently use `dev` and `games` profiles.

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

Behavior:

- Changes are cached in SQLite.
- Changes dedupe by `(source_id, external_id)`.
- If a plugin does not provide `external_id`, changelorg derives one from source ID, URL, title, and publication time.
- Re-fetching a known change updates its title, URL, text, publication time, fetched time, and metadata.
- Backend responses may include legacy `dismissed`, `saved`, and `note` fields for API compatibility, but the web UI treats user-specific state as browser-local data.

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
- `GET /profiles`
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
- `GET /changes?profile=dev` filters to enabled sources in the `dev` profile.
- `GET /changes?profile=games` filters to enabled sources in the `games` profile.
- `PATCH /changes/{change_id}` updates `dismissed`, `saved`, and/or `note`.

## Frontend Behavior

The first frontend is intentionally minimal.

Behavior:

- Loads cached changes from the backend.
- Lets the user switch source profiles, starting with `dev` and `games`.
- Reads the backend cache, which is refreshed hourly by the server.
- Treats URL query parameters as the source of truth for visible feed selections.
- Writes selected profile, time window, sort order, and source filter to the URL immediately.
- Reads those query parameters on page load and browser back/forward navigation.
- Renders HTML and Markdown safely in card previews.
- Renders changes as card-style items with source, publication time, title, summary/content preview, and link.
- Supports sorting by newest, oldest, and source. The order applies to the shelf and the desk.
- Shows two queues of the same card style: the shelf above the desk.
- The desk holds entries that are neither cleared nor shelved. Desk actions: open source, shelf, note, clear from desk.
- The shelf holds shelved entries that are not marked read. Shelf actions: open source, mark read, note, back to desk.
- Mark read clears the entry from both queues. Restoring cleared entries puts a shelved entry back on the shelf.
- Cards support horizontal swipe gestures with pointer or touch. Desk: swipe left clears, swipe right shelves. Shelf: swipe left marks read, swipe right opens the source and the card stays.
- A gesture picks one axis after 10px of movement. Flatter than 45 degrees is a swipe and the page does not scroll. Steeper is a browser scroll and the card does not move. A short fast flick counts as a swipe.
- Swipes start anywhere on the card except form controls and buttons, including on links and preview text, with mouse, pen, or touch. A click or tap on a link still opens it. A horizontal mouse drag over preview text swipes instead of selecting text.
- The action buttons on a card run the same animation as the matching swipe.
- When a card leaves a queue, the card after it takes its place in the viewport. Shelving does not move the viewport even though the shelf above grows.
- Keyboard: `j`/`k` or arrow keys move the current card, `x` clears or marks read, `s` shelves or unshelves, `o` or Enter opens the source, `n` toggles the note, `z` undoes. Keys are ignored while typing.
- Every clear, shelf, and unshelf shows an undo toast for a few seconds.
- The control panel shows a restore control when cleared entries exist in the loaded window. It clears the dismissed flag on all of them.
- The note control is an arrow that expands a text box on the card. Saving a note from the desk shelves the entry. Cmd/Ctrl+Enter saves, Escape closes.
- Empty queues render a one-line message inside the queue header.
- The control panel collapses to one row of stats and one row of selects below the large breakpoint.
- Dismissed, saved, and note state is persisted in browser `localStorage`, not the backend.
- Backend-generated feed data is shared; browser-specific reading state stays private to that browser/profile.
- Shows an empty state when no changes are cached.

Query parameters:

- `profile`: Source profile, such as `dev` or `games`.
- `since`: Feed window, one of `24h`, `7d`, `30d`, `90d`, or `365d`.
- `sort`: Feed sort, one of `newest`, `oldest`, or `source`. Unknown values fall back to `newest`.
- `source`: Optional numeric source ID filter.
- `sourceName`: Optional display name for the active source filter.

## Non-Goals For This Version

- Background scheduling.
- Authentication.
- Multi-user accounts.
- Twitter/X support.
- GitHub releases support.
- Hosted deployment configuration.
