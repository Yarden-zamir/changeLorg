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

- `profile`: The source belongs to exactly one profile through the known string field `config.profile` on saved sources.
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

### GitHub Release Fallback

- Only exact `/owner/repo/releases.atom` URLs on `github.com` or `www.github.com` qualify, both before and after redirects.
- Fallback requires valid Atom 1.0 with zero entries globally, before window or local term filters, and no query string on either URL.
- A qualifying empty release feed fetches `https://github.com/{owner}/{repo}/commits.atom` for the final repository's default branch.
- Any release entry prevents fallback, including old releases outside the requested window or releases excluded by local filters.
- Query-filtered feeds, tags, direct commit feeds, lookalike hosts, and non-exact release paths never trigger fallback.
- Malformed or non-Atom release responses, HTTP/network failures, and private or nonexistent repositories never count as no releases.
- Both requests use one `PublicFetcher` and the public network budgets below. The commit response also requires valid Atom 1.0.
- HTTP failures at either endpoint remain visible as safe discovery/preview failures or refresh source errors, never successful empty results.
- Discovery retains the normalized original release URL and names the candidate `{owner}/{repo} updates`. Save retains that release URL in `config.url`.
- Every preview and refresh reevaluates releases. Once the first release appears, subsequent fetches use releases without a source edit.
- The switch retains cached commit cards, IDs, dismissed flags, shelf flags, notes, and state timestamps. Normal windows still control visibility.
- Each eligible commit becomes one normal change/card with its unique Atom ID as `external_id`, commit link, timestamp, and text.
- Normal window and include/exclude filters apply to commits. Empty valid commit results produce no cards.
- Fallback metadata adds `feed_kind: "commit"`, `fallback_reason: "no_releases"`, and `effective_feed_url` for the final commit response URL.
- Existing `metadata.feed_url` retains the configured release URL. Direct commit feeds do not receive no-release fallback markers.

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
- GitHub `/tags` maps to `/tags.atom`; `/commits` maps to default-branch `/commits.atom`. Direct release, tag, and commit Atom URLs retain their stream and query string.
- Direct `/commits/{branch}.atom` accepts valid normalized branch names, including slash-separated and percent-encoded branches, without loss of URL encoding.
- Other GitHub paths, such as `/issues` and branch HTML `/commits/main`, are rejected rather than mapped to a releases feed.
- Discovery reads the normalized target and, only for qualifying empty releases, its commit fallback. It recognizes RSS/Atom from the body, including valid empty feeds.
- HTML discovery accepts `<link rel="alternate">` with RSS/Atom media types. Relative URLs use the final response URL and the first valid `<base href>`.
- Discovery rejects structurally unsafe candidate URLs and returns at most 10 distinct URLs. It does not resolve candidate DNS or fetch alternate candidates.
- Preview verifies a selected candidate through the normal public fetch checks. An advertised alternate is not proof of a valid feed.
- Discovery returns `[]` when the response contains no direct feed or structurally safe advertised alternate.
- Discovery never guesses HTML selectors or persists profiles, sources, changes, or state.
- Discovery uses the account and request-header checks. Errors include `401`, `412`, `403`, `422`, `429`, and `502`, as defined in the frontend contract.

### Public Fetches and Previews

- `POST /sources/preview` accepts a built-in source draft and previews the last 30 days.
- Preview returns at most 10 changes and the window. It never writes profiles, sources, changes, or state.
- The editor requires a successful preview of the current normalized fetch signature before save. An empty preview permits save.
- The signature contains the plugin and normalized config except `profile`, with canonical object-key order.
- URL, include/exclude terms, enrichment, article options, limits, user agent, and other retained config affect the signature.
- A changed signature invalidates preview. Whitespace edits that produce the same normalized fetch inputs do not invalidate preview.
- Source name, profile, and enabled state are metadata outside the fetch signature. Metadata-only edits retain preview.
- Source-write API calls never fetch changes. `Save only` preserves this behavior.
- Explicit `Save and fetch` writes the source, then refreshes only the enabled source from the successful save response.
- Source URLs require absolute HTTP or HTTPS URLs, ports 80 or 443, and no URL credentials.
- Every source request validates all DNS answers and rejects private, loopback, link-local, reserved, and other non-public addresses.
- Connections pin validated numeric addresses. They do not repeat DNS resolution before connection.
- Redirects and article or enrichment links receive the same validation to prevent server-side request forgery.
- Each request follows at most 3 redirects. Each response body has a 2 MiB limit.
- Each source invocation allows at most 12 requests, a 60-second total deadline, and at most 200 candidate entries.
- Redirects, release checks, commit fallback requests, article requests, and enrichment requests share the invocation budget.
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
- Versioned frontend assets under `/assets/` use public immutable caching for one year; HTML and private API responses remain uncached by HTTP caches.
- The browser keeps up to 32 account/token/path-scoped read results in memory for 15 seconds. Writes, account changes, and explicit reloads invalidate them; late pre-invalidation reads cannot refill the cache.
- GitHub identity lookup results are cached in process for 60 seconds by a token digest, up to 256 entries. Cookie validation still runs on every request; `/me` always revalidates with GitHub.
- Public request handlers always pass the resolved owner to store and service calls. They never use a global default or `owner_id=None`.
- Only internal scheduled refreshes can use the all-owner scope. Each write still uses the source owner.
- Manual refresh and optional generation fetch enabled sources for the current owner and return changes, errors, and the window.
- `GET /changes` reads stored changes only and excludes disabled sources, with or without a profile filter.
- `GET /changes` hides dismissed changes unless `include_dismissed=true` is provided.
- `GET /changes?saved=true` returns saved changes only.
- `GET /changes?profile=dev` filters to the owner's enabled sources in the `dev` profile. Other profile names work identically.
- `GET /changes?source_id={id}` filters within the owner's selected profile and window before the result limit, not after it.
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
- Supports source search, discovery, preview, creation, edit, enable, disable, draft duplication, confirmed deletion, and explicit refresh.
- One source search input matches names against saved sources and optional catalog suggestions. It does not search the full web.
- URLs, bare domains, and GitHub `owner/repo` offer an explicit Discover action. Keystrokes never trigger discovery or external source fetches.
- Name queries can request catalog suggestions after a debounce. Catalog failure does not block link discovery; no preset is required.
- A single discovery result automatically selects the RSS/Atom handler and starts preview. Multiple results require a choice that automatically starts preview.
- Discovery and preview work without a profile. Profile creation, profile selection, and source save remain explicit actions.
- Only a normalized fetch-signature change invalidates preview. Name, profile selection, inline profile creation, profile rename, and enabled-state changes retain preview.
- Profile rename updates the selected source and draft membership without a source write. A clean draft stays clean.
- Normalization trims URLs and HTML article prefixes, trims and removes empty RSS/Atom terms, and converts HTML limits to numbers.
- Canonical signatures ignore object-key order. Unknown config fields remain intact for the same plugin and participate in the signature.
- Plugin switches remove incompatible config fields. [Frontend API Contract](../frontend/API.md#preview-validity) defines normalization details.
- Preview stays near the name and profile controls. Filters collapse with an include/exclude count summary and open for configured filters.
- All remaining manual controls stay under Advanced, including handler, URL, enrichment, and HTML article options. Advanced starts collapsed for RSS/Atom drafts.
- Custom HTML news is an advanced fallback that requires an explicit `article_path_prefix`. The editor never guesses selectors.
- Inline `Create and select` creates a profile and assigns it to the draft without another preview.
- The independent profile manager retains create, rename, and confirmed delete actions without a source draft.
- A sticky action bar keeps preview and save actions accessible on desktop and mobile, with progress and explicit save-block reasons.
- Save requires a valid name, supported plugin, current preview, explicit existing profile, and unsaved changes. Concurrent actions remain blocked.
- Enabled drafts offer `Save and fetch` and `Save only`. Disabled creation retains `Save only` and never requests a refresh.
- A successful save retains the returned source ID and marks the draft clean before any refresh or synchronization.
- Fetch or synchronization failure never rolls back a successful save or repeats source creation.
- A new candidate needs Save, but only manual normalized changes from its selected baseline require a discard warning.
- Untouched candidates, discovery, preview, and search text require no discard prompt. Reverted metadata or fetch edits and equivalent normalized URL/filter whitespace also require no prompt.
- Close, editor Escape, source/candidate replacement, discovery, and `Add another` protect actual source edits with a custom discard dialog.
- Unsubmitted profile creation or rename text warns only on editor exit, including `View source`, not source replacement. Created profiles persist after draft discard.
- A successful save resets the draft baseline before fetch or synchronization. Failure in either later step creates no unsaved-draft warning.
- `Duplicate draft` preserves current edits and preview without a discard prompt. Save creates a separate subscription and leaves the original unchanged.
- Custom confirmations use an editor-owned nested modal `<dialog>`, with `Keep editing` or Cancel as the default focus.
- Confirmation traps focus; Escape cancels only the top dialog and restores focus. Confirm runs the captured action exactly once for its owner.
- Owner change or editor unmount invalidates pending confirmation actions. Source disable, source delete, and profile delete also use custom dialogs, never browser `confirm()`.
- Native `beforeunload` protection applies only to actual source edits, unsubmitted profile text, or a pending mutation, not untouched candidates or preview.
- Browsers own unavoidable reload, navigation-away, and tab-close prompts. Custom in-app dialogs do not replace or control those prompts.
- Fetch failures offer an explicit fetch-only retry against the saved ID. Synchronization retry reloads data without source writes or refreshes.
- Saved-source enable, disable, duplicate draft, confirmed delete, and `Fetch latest` remain independent actions.
- `Add another` resets the source entry and returns focus to search. New candidates require explicit profile selection, without an implicit default.
- After successful fetch and synchronization, `View source` selects the saved source's profile, ID, and name in the URL. It requires the current owner.
- `View source` closes the editor and preserves the current time window and sort order. It never changes identity.
- If the window excludes fetched items, offer a wider window through the existing Window control. Never widen the window automatically.
- Resolves `/me` before account data and preserves the anonymous capability across sign-in, sign-out, and imports.
- Offers GitHub sign-in when authentication is enabled. All GitHub users can sign in.
- The account-error screen offers explicit cookie reset without loss of anonymous data, including when authentication is disabled.
- Account details, sign-in/out, anonymous import, and legacy browser-state import live inside a top-right GitHub icon menu. Imports remain explicit; no automatic import prompt appears on the main view.
- Treats URL query parameters as the source of truth for visible feed selections.
- Writes selected profile, time window, sort order, and source filter to the URL immediately.
- Reads those query parameters on page load and browser back/forward navigation.
- URL state updater callbacks remain pure, with no history side effects. Each navigation action writes history once outside the updater.
- One Back action restores the prior selection, including after `View source`. Back/forward navigation never adds a history entry.
- An active source filter sends API `source_id` before the 200-item limit. Source-filter changes request new data, not just client-side filtering.
- Newer editor profile synchronization supersedes older profile loads. Late successes and errors cannot replace the newer profiles, close the editor, or discard its draft.
- Genuine current identity failures still block account access. Stale-profile handling never bypasses identity checks.
- Renders HTML and Markdown safely in card previews.
- Renders changes as card-style items with source, publication time, title, summary/content preview, and link.
- `Change` and preview types include `metadata: Record<string, unknown>`. Fallback commit cards show a Commit badge when both fallback markers match.
- Preview explains no releases and one item per commit when returned metadata signals `fallback_reason: "no_releases"`. Empty results retain the normal empty-preview message.
- Supports sorting by newest, oldest, and source. The order applies to the shelf and the desk.
- Shows two queues of the same card style: the shelf above the desk.
- The desk holds entries that are neither cleared nor shelved. Desk actions: open source, shelf, note, clear from desk.
- The shelf holds shelved entries that are not marked read. Shelf actions: open source, mark read, note, back to desk.
- Mark read clears the entry from both queues. Restoring cleared entries puts a shelved entry back on the shelf.
- Cards support horizontal swipe gestures with pointer or touch. Desk: swipe left clears, swipe right shelves. Shelf: swipe left marks read, swipe right opens the source and the card stays.
- A gesture picks one axis after 10px of movement. Flatter than 45 degrees is a swipe and the page does not scroll. Steeper is a browser scroll and the card does not move. A short fast flick counts as a swipe.
- Swipes start anywhere on the card except form controls and buttons, including on links and preview text, with mouse, pen, or touch. A click or tap on a link still opens it. A horizontal mouse drag over preview text swipes instead of selecting text.
- The action buttons on a card run the same animation as the matching swipe.
- During the 240ms exit, the card's layout space and queue gap collapse with the same easing, moving the following card into place continuously.
- At animation completion the queue updates optimistically, without waiting for the server. Other cards remain usable while writes finish serially in the background.
- Failed writes restore the failed and unsent optimistic changes, cancel unsent jobs, and require an explicit reload. Acknowledged changes remain saved. Undo waits until queued writes finish.
- Desktop cards place Clear or Mark read at the top right, independent of content height. Mobile cards retain bottom action controls.
- When a card leaves a queue, the card after it takes its place in the viewport. Shelving does not move the viewport even though the shelf above grows.
- Keyboard: `j`/`k` or arrow keys move the current card, `x` clears or marks read, `s` shelves or unshelves, `o` or Enter opens the source, `n` toggles the note, `z` undoes. Keys are ignored while typing.
- Every clear, shelf, and unshelf shows an undo toast for a few seconds.
- Press `?` or select Keyboard shortcuts to open shortcut help. Press `?` or Escape to close it. Help blocks card shortcuts.
- Additional shortcuts: `u` undoes, `r` restores cleared items, `f` filters to the current source, and `a` shows all sources.
- Press `e` to open the source editor. The editor blocks feed shortcuts.
- Press `p`, `w`, or `t` to focus the profile, window, or order control. Native form keys control the selection.
- Enter preserves native button and link activation. Held action keys do not repeat; card navigation keys repeat.
- The control panel offers Restore when loaded dismissed entries exist. Restore covers only the loaded profile, window, and active source filter, up to 200 items.
- Without a source filter, Restore covers loaded dismissed entries across the profile. It preserves notes and shelf flags; it never restores unloaded entries.
- The note control is an arrow that expands a text box on the card. Saving a note from the desk shelves the entry. Cmd/Ctrl+Enter saves, Escape closes.
- An empty shelf shows only its compact header. An empty desk uses a one-line message in its header, without a duplicate panel.
- The desk header distinguishes feed load in progress, load error, no active sources, items on the shelf, cleared items, and no unread items.
- Empty-desk guidance points to source management, restore, or the existing Window control as appropriate. Account setup remains separate.
- Below the large breakpoint, the control panel has a compact stats strip, a full-width profile selector, and a two-column window/order row. Dropdown labels omit redundant counts and captions.
- Clicking Sources opens the source/profile editor. Clicking Cleared opens the loaded cleared-item list with individual restore and restore-all controls. Restore is absent from the main view.
- The main header contains the title and GitHub menu, without a subtitle or account explanation. Populated queue headers omit repeated explanatory text. Source filter names wrap.
- At 320px and wider, long card titles, source names, notes, and account names wrap without page-level horizontal overflow.
- Mobile card actions and editor confirmation buttons have at least 44px height. Source metadata uses at least 14px text.
- Mobile page and queue gutters are 4px; card body padding is 12px and nested article padding is 8px to preserve reading width. Larger layouts keep their existing spacing.
- Supported browsers provide a best-effort 10ms vibration on button/link/menu actions, selection changes, and committed swipes. Feedback is throttled to one pulse per 80ms; typing and scrolling do not vibrate. Unsupported or blocked vibration does not affect the action.
- The mobile editor action bar uses two columns and scrolls within 40% of viewport height on short screens.
- The backend persists dismissed, saved, and note state for the current owner.
- Anonymous browser `localStorage` holds a UUIDv4 bearer capability, not the authoritative change state.
- Feed data, profiles, sources, shelf state, and notes are private to the owner, not shared across users.
- Old browser-state import requires an explicit action. It only matches the destination owner's source keys and preserves existing server state.
- An account change discards the old feed, drafts, and undo state. Late responses cannot update the new account.
- Unsaved card notes remain in memory across source, profile, window, and feed reloads for the same owner.
- Successful note writes clear their local overrides. Notes without local edits always display the current server value.
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
