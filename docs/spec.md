# changelorg Behavior Spec

## Product Goal

changelorg tracks source changes and generates private, time-windowed feeds for anonymous owners and GitHub accounts.
The website displays the feed. The CLI exports structured data for analysis and summaries.

This spec defines the approved target behavior, not completed implementation or deployment verification.
[Frontend API Contract](../frontend/API.md) defines request and response contracts. [User Data](user-data.md) defines storage, ownership, authentication, and imports.
These requirements replace the previous shared-feed and browser-local state claims.

## Data Model

### Source

A source is a private subscription that belongs to one owner.

Required fields:

- `name`: Human-readable label.
- `plugin`: Plugin key that knows how to fetch this source.
- `config`: Plugin-specific JSON object.
- `enabled`: Disabled sources retain data but do not appear in the API feed or refreshes.

Stored sources also include `id`, `owner_id`, `created_at`, and `updated_at`. The server assigns ownership from the request identity.

Common config fields:

- `profile`: The source belongs to exactly one profile through `config.profile`.
- Web source saves require an explicit profile that exists for the current owner. Discovery and preview require no profile.
- The CLI defaults to `dev` when the profile is omitted.

Behavior:

- DuckDB 1.5.5 stores sources durably. Data has no automatic expiry.
- Source deletion explicitly deletes its cached changes, shelf state, and notes in the same transaction.
- Source IDs remain stable within the database and through the SQLite migration.
- Default sources use `dev` and `games` for `github:8178413`, not shared subscriptions for every owner.
- Startup seeds defaults once. It does not restore sources that the owner deletes.

### Profile

- Profiles are explicit records with the key `(owner_id, name)`.
- Empty profiles persist and appear in the owner's profile list.
- `source_count` counts enabled sources only.
- Profile rename updates `config.profile` for its sources in the same transaction.
- Profile deletion explicitly deletes its sources, changes, shelf state, and notes in the same transaction.
- Profile names contain 1 to 100 characters. Source names contain 1 to 200 characters.

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

- DuckDB stores changes durably under their source owner. Equal source URLs do not create shared data across owners.
- Changes dedupe by `(source_id, external_id)`.
- If a plugin does not provide `external_id`, changelorg derives one from source ID, URL, title, and publication time.
- A refresh updates known changes: title, URL, text, publication time, fetched time, and metadata.
- A refresh preserves `dismissed`, `saved`, `note`, and `state_updated_at`.
- The backend owns this private state. Browser `localStorage` is not the state store.
- Feed windows filter results. They do not expire or delete data.

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

Web requests use only the built-in `rss-atom` and `html-news` plugins. The web API accepts no arbitrary plugin uploads.
Web requests never load local plugins or accept local overrides of built-in plugins.

The trusted CLI loads built-in plugins first, then local plugins from:

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
- `fetch` returns changes only inside the requested window.
- Invalid source config raises a clear exception.
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

- Fetches public HTTP or HTTPS feeds under the network limits below.
- Parses RSS and Atom entries.
- Uses published, updated, or created timestamps when available.
- Entries without timestamps are assigned the generation window end time.
- Filters entries to the requested time window.
- Applies `include_any` before `exclude_any` against title, URL, summary, and content.

### Built-In HTML News Plugin

Key: `html-news`

- `url` identifies a public HTML news index.
- `article_path_prefix` selects article links and is required.
- Optional `limit` accepts an integer from 1 to 100.
- Article requests use the same network budget as the index request.

### Source Discovery

- `POST /sources/discover` accepts `{ "url": "..." }` with 1 to 2000 characters and returns at most 10 `SourceCreate` drafts.
- Input accepts absolute HTTP or HTTPS URLs, bare domains with optional paths, and GitHub `owner/repo` shorthand. Bare inputs use HTTPS.
- GitHub repository roots, optional `.git` suffixes, `/releases`, `/releases/latest`, and `/releases/tag/...` map to `/releases.atom`.
- The releases feed covers all repository releases, not only the supplied tag or latest release.
- GitHub `/tags` maps to `/tags.atom`. Direct `/releases.atom` and `/tags.atom` URLs retain their stream and query string.
- Other GitHub paths, such as `/issues`, are rejected rather than mapped to a releases feed.
- Discovery fetches one normalized target under the public network limits below. It recognizes RSS/Atom from the response body, including valid empty feeds.
- HTML discovery accepts `<link rel="alternate">` with RSS/Atom media types. Relative URLs use the final response URL and the first valid `<base href>`.
- Discovery rejects structurally unsafe candidate URLs and returns at most 10 distinct URLs. It does not resolve candidate DNS or fetch alternate candidates.
- Preview verifies a selected candidate through the normal public fetch checks. An advertised alternate is not proof of a valid feed.
- Discovery returns `[]` when the response contains no direct feed or structurally safe advertised alternate.
- Discovery never guesses HTML selectors or persists profiles, sources, changes, or state.
- Discovery uses the account and request-header checks. Errors include `401`, `412`, `403`, `422`, `429`, and `502`, as defined in the frontend contract.

### Public Fetches and Previews

- `POST /sources/preview` accepts a built-in source draft and previews the last 30 days.
- Preview returns at most 10 changes and the window. It never writes profiles, sources, changes, or state.
- Any draft change requires another preview before save, including source name and profile changes. An empty preview permits save.
- Source save never fetches changes. Explicit refresh fetches the saved source.
- Source URLs require absolute HTTP or HTTPS URLs, ports 80 or 443, and no URL credentials.
- Every source request validates all DNS answers and rejects private, loopback, link-local, reserved, and other non-public addresses.
- Connections pin validated numeric addresses. They do not repeat DNS resolution before connection.
- Redirects and article or enrichment links receive the same validation to prevent server-side request forgery.
- Each request follows at most 3 redirects. Each response body has a 2 MiB limit.
- Each source invocation allows at most 12 requests, a 60-second total deadline, and at most 200 candidate entries.
- Redirects, article requests, and enrichment requests share the invocation budget.
- Compressed responses are rejected. Revisit bounded decompression if a required source needs compression.
- Discovery uses the same invocation budget and global semaphore as other source network operations.
- A global limit permits at most four concurrent source network operations across discovery, previews, manual refreshes, and scheduled refreshes.
- Initially, concurrency and invocation budgets bound network work. No exact per-owner preview rate or cooldown forms part of this contract.
- Revisit per-owner rate limits if abuse or owner starvation occurs.

### Enrichment

RSS/Atom items pass through a generic enrichment layer before storage.

Behavior:

- Enrichment profiles are defined in Python code using a small typed DSL, not JSON blobs.
- Source config selects a named enrichment profile with `enrichment_profile`. Known feed URLs also map to presets automatically.
- Enrichment profiles are distinct from owner subscription profiles. `GET /enrichment-profiles` lists built-in enrichment presets.
- Raw feed title, summary, content, quality flags, and selected profile are stored in change metadata.
- Version-only titles such as `v0.115.1` are replaced with the first meaningful non-header release-note line when available.
- Thin or generic summaries such as `Learn what's new...` can trigger linked-page fetching for profiles that allow it.
- Overloaded release bodies are compacted to the first meaningful bullets or paragraphs.
- Common boilerplate such as changelog compare links, release dates, download lists, social links, and feed footer text is dropped when detected.

## CLI Behavior

Base command: `changelorg`.

Commands:

- `changelorg init`: Create the DuckDB schema or migrate the old SQLite database.
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

- `--db PATH` overrides the DuckDB database path.
- `--owner ID` selects `github:<numeric-id>` or `anon:<sha256>`. The trusted CLI defaults to `github:8178413`.
- Stop the app before CLI database access. One process owns the database.
- `--json` returns structured JSON where supported.
- Generation continues when one source fails and reports source-level errors.

## API Behavior

The API must implement every route and payload in [Frontend API Contract](../frontend/API.md), plus `GET /enrichment-profiles`.

Required contract routes:

- `GET /me`
- `GET /plugins`
- `GET /enrichment-profiles`
- `GET /catalog`
- `GET /sources`
- `GET /profiles`
- `POST /profiles`
- `PATCH /profiles/{encodedName}`
- `DELETE /profiles/{encodedName}`
- `POST /sources/discover`
- `POST /sources/preview`
- `POST /sources`
- `PATCH /sources/{source_id}`
- `DELETE /sources/{source_id}`
- `POST /sources/{source_id}/refresh`
- `GET /changes`
- `PATCH /changes/{change_id}`
- `POST /changes/restore`
- `POST /me/import`
- `POST /me/import-state`
- `POST /session/reset`

Additional routes:

- `GET /health`: Public service health without owner data.
- `GET /sources/{source_id}`: Current owner's source only.
- `GET /refresh/status`: No cross-owner source details or errors in public responses.
- `POST /changes/generate`: Optional owner-scoped generation, disabled unless `CHANGELORG_MANUAL_GENERATE_API=true`.

Behavior:

- Startup initializes DuckDB and migrates old SQLite data under the [User Data](user-data.md) contract.
- `/me` resolves the request identity before account access. All later frontend requests carry the exact `X-Changelorg-Owner` value.
- An owner mismatch returns `412` before account access or mutation. The server never redirects a mutation into another owner.
- Mutations require `X-Changelorg-Request: 1` and an explicit allowed origin for browser requests.
- Mutation bodies have a 2 MiB limit, including requests without `Content-Length`.
- CORS uses an explicit origin allowlist, credentials, and all three custom headers from the frontend contract. Wildcard origins are forbidden.
- Private responses use `Cache-Control: private, no-store`.
- Public request handlers always pass the resolved owner to store and service calls. They never use a global default or `owner_id=None`.
- Only internal scheduled refreshes can use the all-owner scope. Each write still uses the source owner.
- Manual refresh and optional generation fetch enabled sources for the current owner and return changes, errors, and the window.
- `GET /changes` reads stored changes only and excludes disabled sources, with or without a profile filter.
- `GET /changes` hides dismissed changes unless `include_dismissed=true` is provided.
- `GET /changes?saved=true` returns saved changes only.
- `GET /changes?profile=dev` filters to the owner's enabled sources in the `dev` profile. Other profile names work identically.
- `PATCH /changes/{change_id}` updates only supplied `dismissed`, `saved`, and `note` fields. The note limit is 10,000 characters.
- Restore changes only `dismissed` to `false` for supplied IDs. It preserves notes and shelf flags.
- Batch mutations validate every ID's ownership before any state change.
- Catalog results contain built-in source templates, never another owner's subscriptions.

## Frontend Behavior

The frontend manages private profiles, sources, feeds, and change state for the current owner.

Behavior:

- Loads cached changes from the backend.
- Lists the owner's profiles, including empty profiles, and permits profile creation, rename, and deletion.
- Reads durable backend data. The server refreshes enabled sources hourly with a default 30-day fetch window.
- Supports source search, discovery, preview, creation, edit, enable, disable, deletion, and explicit refresh.
- One source search input matches names against saved sources and optional catalog suggestions. It does not search the full web.
- URLs, bare domains, and GitHub `owner/repo` offer an explicit Discover action. Keystrokes never trigger discovery or external source fetches.
- Name queries can request catalog suggestions after a debounce. Catalog failure does not block link discovery; no preset is required.
- A single discovery result automatically selects the RSS/Atom handler and starts preview. Multiple results require a choice that automatically starts preview.
- Discovery and preview work without a profile. Profile creation, profile selection, and source save remain explicit actions.
- Any draft change invalidates preview, including source name, profile selection, or a newly created profile assigned to the draft.
- Manual handler, URL, and enrichment controls stay under Advanced, collapsed for RSS/Atom drafts.
- Custom HTML news is an advanced fallback that requires an explicit `article_path_prefix`. The editor never guesses selectors.
- Resolves `/me` before account data and preserves the anonymous capability across sign-in, sign-out, and imports.
- Offers GitHub sign-in when authentication is enabled. All GitHub users can sign in.
- The account-error screen offers explicit cookie reset without loss of anonymous data, including when authentication is disabled.
- After sign-in, prompts for an explicit anonymous-data copy when anonymous data exists. It never imports automatically.
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
- Desktop cards place Clear or Mark read at the top right, independent of content height. Mobile cards retain bottom action controls.
- When a card leaves a queue, the card after it takes its place in the viewport. Shelving does not move the viewport even though the shelf above grows.
- Keyboard: `j`/`k` or arrow keys move the current card, `x` clears or marks read, `s` shelves or unshelves, `o` or Enter opens the source, `n` toggles the note, `z` undoes. Keys are ignored while typing.
- Every clear, shelf, and unshelf shows an undo toast for a few seconds.
- Press `?` or select Keyboard shortcuts to open shortcut help. Press `?` or Escape to close it. Help blocks card shortcuts.
- Additional shortcuts: `u` undoes, `r` restores cleared items, `f` filters to the current source, and `a` shows all sources.
- Press `e` to open the source editor. The editor blocks feed shortcuts.
- Press `p`, `w`, or `t` to focus the profile, window, or order control. Native form keys control the selection.
- Enter preserves native button and link activation. Held action keys do not repeat; card navigation keys repeat.
- The control panel shows a restore control when cleared entries exist in the loaded window. It clears the dismissed flag on all of them.
- The note control is an arrow that expands a text box on the card. Saving a note from the desk shelves the entry. Cmd/Ctrl+Enter saves, Escape closes.
- Empty queues render a one-line message inside the queue header.
- The control panel collapses to one row of stats and one row of selects below the large breakpoint.
- The backend persists dismissed, saved, and note state for the current owner.
- Anonymous browser `localStorage` holds a UUIDv4 bearer capability, not the authoritative change state.
- Feed data, profiles, sources, shelf state, and notes are private to the owner, not shared across users.
- Old browser-state import requires an explicit action. It only matches the destination owner's source keys and preserves existing server state.
- An account change discards the old feed, drafts, and undo state. Late responses cannot update the new account.
- On `401` or `412`, the frontend resolves `/me` again. It never retries the mutation automatically.
- Feed mutations are serial. After a failed mutation, the frontend reloads the feed before another mutation.
- Shows an empty state when no changes are cached.

Query parameters:

- `profile`: Source profile, such as `dev` or `games`.
- `since`: Feed window, one of `24h`, `7d`, `30d`, `90d`, or `365d`.
- `sort`: Feed sort, one of `newest`, `oldest`, or `source`. Unknown values fall back to `newest`.
- `source`: Optional numeric source ID filter.
- `sourceName`: Optional display name for the active source filter.

## Deployment and Retention

- Embedded DuckDB 1.5.5 uses native `JSON`, `TIMESTAMPTZ`, and `BOOLEAN` columns, with sequences for source and change IDs.
- One app container runs one Uvicorn worker. Database transactions serialize within that process; network fetches stay outside the database lock.
- Revisit storage before multiple workers, replicas, or concurrent CLI database access.
- `/data/changelorg.duckdb` uses the persistent `${KITSHN_DATA_DIR}/data` mount. No separate database service is required.
- Optional GitHub authentication uses `oauth2-proxy`. The app verifies its cookie through the internal auth endpoint and GitHub `/user`.
- The app never treats public identity headers as authentication. [User Data](user-data.md) defines the identity checks and token rules.
- Data survives restarts and deploys. No automatic expiry applies to profiles, sources, changes, or state, including anonymous data.
- Feed and refresh windows limit selection, not retention.
- [KitSHn Recipe](../kitshn.md) describes deployment configuration. This spec does not certify a deployment or test result.

## Non-Goals For This Version

- Multiple database owner processes or app replicas.
- Automatic data expiry or automatic account imports.
- Arbitrary web plugin uploads.
- Google sign-in. The separate Google sign-in proposal is superseded by the GitHub account contract.
- Twitter/X support.
- A dedicated GitHub releases API plugin. Public release feeds can use RSS/Atom.
