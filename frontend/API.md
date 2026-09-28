# Frontend API Contract

This contract describes the requests from `src/lib/api.ts`, `App.tsx`, `AccountArea.tsx`, and `SourceEditor.tsx`.
The backend must enforce account isolation. Client checks do not replace server authorization.
The editor rules define approved behavior, not proof of tests or deployment. They introduce no backend route or identity change.

## Account Access

1. Except for session reset, every API request includes `X-Anonymous-Token` with a persistent UUIDv4 from browser `localStorage`.
2. Every network API request includes cookies through `credentials: "include"` and bypasses the HTTP browser cache. Successful profiles, sources, plugins, catalog, and changes reads have a bounded 15-second in-memory cache keyed by owner, browser token, and exact path. Writes invalidate it before and after requests, and account changes or explicit reloads clear it. Entries never persist to browser storage.
3. `GET /me` resolves the current owner before the frontend accesses account data.
4. Except for session reset, every subsequent API request includes `X-Changelorg-Owner` with the exact `id` from `/me`.
5. Every `POST`, `PATCH`, and `DELETE` includes `X-Changelorg-Request: 1`.
6. JSON request bodies use `Content-Type: application/json`.
7. Before any account access, the server compares the owner header with the current request identity.
8. On an owner mismatch, the server returns `412` without any mutation. It never redirects the mutation into an anonymous account.
9. On `401` or `412`, the frontend discards account access and loads `/me` again. It never retries the mutation automatically.
10. An account change discards the old feed, source draft, note drafts, and undo state. Late responses cannot update the new account.
11. The anonymous token remains intact after sign-in, sign-out, or import. An invalid stored token blocks access instead of replacement.
12. Private responses require `Cache-Control: private, no-store`. Cross-origin development requires explicit frontend origins, credentials, and all three custom headers in CORS.

The authentication proxy handles `/auth/start?rd=/` and `/auth/sign_out?rd=/` as browser navigation routes, not JSON API mutations.

## Session Recovery

1. `POST /session/reset` requires no identity, anonymous token, or owner header. It works even when OAuth is disabled.
2. The request requires `X-Changelorg-Request: 1` for CSRF protection and includes cookies through `credentials: "include"`.
3. The server returns `204` and clears all `_changelorg_oauth` cookies, including session chunks and CSRF cookies.
4. The exported `resetSession()` helper fetches the configured API origin, bypasses the cache, and rejects redirects or responses other than `204`.
5. The helper does not read or modify browser storage. Reset never modifies anonymous tokens or account data.
6. The account error screen offers `Continue anonymously` as its primary recovery action, plus account retry and `/auth/start?rd=/`.
7. The screen explains that reset works without OAuth, but GitHub sign-in requires server support.
8. Reset requires an explicit click. While reset is pending, the controls block duplicate actions and the screen displays progress.
9. After reset succeeds, the frontend invalidates identity and reloads account access. It never replays a failed mutation.
10. Reset failures display a safe error without response details. A retry requires an explicit user action.

## JSON Types

```ts
type Identity = {
  id: string;
  authenticated: boolean;
  login: string | null;
  auth_enabled: boolean;
  anonymous_has_data: boolean;
};
type Profile = { name: string; source_count: number };
type SourceCreate = {
  name: string;
  plugin: string;
  config: Record<string, unknown>;
  enabled: boolean;
};
type Source = SourceCreate & {
  config: Record<string, unknown> & { profile: string };
  id: number;
  owner_id: string;
  created_at: string;
  updated_at: string;
};
type Plugin = {
  key: string;
  name: string;
  description: string;
  config_schema: {
    properties?: Record<string, { enum?: unknown[] }>;
  };
};
type TimeWindow = { start: string; end: string };
type Change = {
  id: number;
  external_id: string | null;
  source_id: number;
  source_name: string;
  source_profile: string;
  plugin: string;
  title: string;
  url: string | null;
  summary: string;
  content: string;
  metadata: Record<string, unknown>;
  published_at: string;
  fetched_at: string;
  dismissed: boolean;
  saved: boolean;
  note: string;
  state_updated_at: string | null;
};
type ChangeState = { dismissed?: boolean; saved?: boolean; note?: string };
type PreviewChange = Pick<Change,
  "title" | "url" | "summary" | "content" | "published_at" | "external_id" | "metadata"
>;
```

Dates use ISO strings. Required text fields use empty strings rather than `null`.
Feed windows use `24h`, `7d`, `30d`, `90d`, or `365d`.
`source_count` counts enabled sources. The profile list includes empty profiles and excludes other owners.
Saved `Source.config.profile` is a known string. `SourceCreate` remains profile-optional for discovery and preview; source writes require an existing owner profile.
Successful responses use JSON unless the route permits `204` below. Extra response fields are allowed.

## Routes

| Method | Path | Request | Response |
| --- | --- | --- | --- |
| GET | `/me` | No body; no owner header | `Identity` |
| POST | `/session/reset` | No body, token, or owner header; CSRF header and cookies | `204`; clears OAuth cookies |
| GET | `/profiles` | No body | `Profile[]` |
| POST | `/profiles` | `{ name: string }` | JSON or `204`; body unused |
| PATCH | `/profiles/{encodedName}` | `{ name: string }` | JSON or `204`; body unused |
| DELETE | `/profiles/{encodedName}` | No body | JSON or `204`; body unused |
| GET | `/sources` | No body | `Source[]`, including disabled sources |
| GET | `/plugins` | No body | `Plugin[]` |
| GET | `/catalog?q={encodedQuery}` | No body | `SourceCreate[]` |
| POST | `/sources/discover` | `{ url: string }`; 1 to 2000 characters | `SourceCreate[]`; at most 10 candidates without `config.profile` |
| POST | `/sources/preview` | `SourceCreate` | `{ changes: PreviewChange[], window: TimeWindow }` |
| POST | `/sources` | `SourceCreate` | Complete `Source` |
| PATCH | `/sources/{id}` | `SourceCreate` or `{ enabled: boolean }` | Complete `Source` |
| DELETE | `/sources/{id}` | No body | JSON or `204`; body unused |
| POST | `/sources/{id}/refresh` | No body | `{ changes: Change[], errors: SourceError[], window: TimeWindow }` |
| GET | `/changes?since={window}&limit=200&include_dismissed=true&profile={encodedName}` | No body | `Change[]` |
| PATCH | `/changes/{id}` | `ChangeState` | Complete updated `Change` |
| POST | `/changes/restore` | `{ ids: number[] }` | Complete updated `Change[]` for the supplied IDs |
| POST | `/me/import` | No body | `{ profiles: number, sources: number, changes: number }` |
| POST | `/me/import-state` | `{ state: Record<string, ChangeState> }` | JSON or `204`; body unused |

`SourceError` contains `source_id: number`, `source_name: string`, `plugin: string`, and `message: string`.
The frontend displays only the error count. It does not display raw source errors or API error bodies.
An active URL `source` filter adds `source_id={id}` to `GET /changes`. The server applies this filter before `limit=200`.

## Source Discovery

1. One search input matches saved sources and optional catalog suggestions by name. Name search is not full-web search.
2. Absolute HTTP or HTTPS URLs, bare domains, and GitHub `owner/repo` expose an explicit Discover action. Bare inputs use HTTPS.
3. Keystrokes never trigger discovery or external source fetches. Name queries can request catalog suggestions after a debounce.
4. Catalog failure does not block link discovery. Discovery requires no catalog preset or profile.
5. `POST /sources/discover` uses the account, owner, cookie, CSRF, and private-cache rules above. It writes no profiles, sources, changes, or state.
6. GitHub repository roots, optional `.git` suffixes, `/releases`, `/releases/latest`, and `/releases/tag/...` map to `/releases.atom`.
7. A release tag or latest-release URL selects the repository's releases feed, not a feed limited to one release.
8. GitHub `/tags` maps to `/tags.atom`; `/commits` maps to default-branch `/commits.atom`. Direct release, tag, and commit Atom URLs preserve their stream and query string.
9. Unsupported GitHub paths, such as `/issues` and branch HTML `/commits/main`, return `422`. Direct `/commits/{branch}.atom` accepts valid branch names, including slash-separated or percent-encoded branches.
10. Discovery reads the normalized target, with an additional commit request only under GitHub Release Fallback below. Body parsing recognizes RSS/Atom, including valid empty feeds; suffixes and media types alone prove nothing.
11. HTML discovery accepts `<link rel="alternate">` with `application/rss+xml` or `application/atom+xml`. Relative links use the final response URL and first valid `<base href>`.
12. Candidates pass structural public-URL checks, deduplicate by resolved URL, and stop at 10. Discovery neither resolves candidate DNS nor fetches alternate candidates.
13. Feed candidates use `plugin: "rss-atom"`; X candidates use `plugin: "x"`. Both include `enabled: true`, a display name, and `config.url`, contain no profile, and remain unverified until preview.
14. A successful response with no direct feed or advertised alternates returns `[]`. It does not invent an HTML handler or selectors.
15. One result automatically selects its handler and starts preview. With multiple results, an explicit choice automatically starts preview.
16. Preview requires no profile. Save requires explicit profile selection or creation and an explicit save action.
17. Only a normalized fetch-signature change invalidates preview, as defined in Preview Validity. Name, profile, and enabled-state edits retain preview.
18. All remaining manual controls stay under Advanced, collapsed for RSS/Atom drafts: handler, URL, enrichment, and HTML options. HTML requires an explicit `article_path_prefix`.
19. Discovery uses one `PublicFetcher` for the release check and fallback, with 12 requests and a 60-second total deadline. Each response permits 2 MiB; each request permits three redirects. Redirects share the request budget; preview has its own invocation.
20. The shared semaphore permits four concurrent operations across discovery, previews, manual refreshes, and scheduled refreshes. Discovery has no separate capacity pool.
21. Branch validation uses the normalized, decoded branch name and preserves valid URL encoding, including `%2F`. Branch HTML paths remain unsupported.

Discovery errors use the shared safe-error display. The following statuses distinguish request failures from a successful empty result:

| Status | Meaning |
| --- | --- |
| `401` | Invalid browser identity or rejected GitHub session; resolve `/me` again without an automatic retry. |
| `412` | Missing or mismatched owner header; resolve `/me` again without an automatic retry. |
| `403` | Rejected origin or missing CSRF request header. |
| `422` | Invalid request body, invalid initial URL, or unsupported GitHub path; no source fetch occurs. |
| `429` | The shared source-fetch semaphore has no available slot. |
| `502` | Discovery fetch failure, including DNS, redirect, upstream HTTP, response-size, or deadline failures. |

The general request-body limit and authentication-service failures still apply.

## Social Sources

X/Twitter profile and post links normalize to the account profile and select a keyless public-embed handler without a discovery fetch. Preview returns `424` when the public timeline is blocked, rate-limited, or malformed. Up to 200 embedded entries are read through the bounded public fetcher, one card per stable post ID. Coverage is best-effort, not a complete historical timeline. X supports URL/profile/include/exclude configuration; no API key, login cookie, or enrichment setting is accepted. Bluesky profile/post links normalize to the public account RSS feed.

## GitHub Release Fallback

1. Fallback requires an exact `/owner/repo/releases.atom` path on `github.com` or `www.github.com`, before and after redirects.
2. The release response must be valid Atom 1.0 with zero entries before time-window or include/exclude filters. No query string can exist on either URL.
3. A globally empty release feed triggers `https://github.com/{owner}/{repo}/commits.atom` for the default branch of the final repository.
4. Any release entry prevents fallback, even when all releases fall outside the requested window or fail local filters.
5. Query-filtered release feeds, tag feeds, direct commit feeds, lookalike hosts, and non-exact release paths never trigger fallback.
6. Malformed feeds, non-Atom release responses, network errors, and HTTP failures never count as no releases. Private or nonexistent repositories receive no fallback.
7. Both requests use the same `PublicFetcher`, DNS/address checks, redirect limits, request budget, deadline, and shared source-fetch slot. The commit response also requires valid Atom 1.0.
8. HTTP failures at either endpoint remain visible through safe discovery/preview errors or refresh source errors. They never become successful empty results.
9. Discovery retains the normalized original release URL in `config.url` and names the candidate `{owner}/{repo} updates`. Save never replaces it with the commit URL.
10. Every preview or refresh checks releases again. Once the first release appears, later fetches use releases, without a source edit.
11. Cached commit cards and their IDs, shelf flags, dismissed flags, notes, and state timestamps remain intact after the switch. Existing window rules control visibility.
12. Each eligible commit produces one normal change/card with its unique Atom ID as `external_id`, commit URL, timestamp, and text. Normal window and include/exclude filters apply.
13. Fallback metadata adds `feed_kind: "commit"`, `fallback_reason: "no_releases"`, and `effective_feed_url` for the final commit response URL. Existing `feed_url` retains the configured release URL.
14. Cards show a Commit badge when both fallback markers match. Preview explains no releases and one item per commit when returned metadata contains `fallback_reason: "no_releases"`.
15. Empty commit results retain the normal empty-preview message; no card metadata exists to signal fallback. Direct commit feeds do not receive no-release fallback markers.

## Preview Validity

1. `fetchSignature` uses `normalizedSource` from `src/lib/sourceDraft.ts`. It serializes the plugin and normalized config except `profile`, with canonical object-key order.
2. A changed signature invalidates preview. URL, include/exclude terms, enrichment, article options, limits, user agent, and all other retained config participate.
3. Name, `config.profile`, and enabled state do not participate. Metadata-only changes retain a successful preview, including an empty preview.
4. Profile selection, inline profile creation with selection, and profile rename retain preview. Profile rename updates membership without a source write or a clean-to-dirty transition.
5. Normalization trims URL whitespace. RSS/Atom terms split on newlines, trim each term, and omit empty terms.
6. HTML normalization trims `article_path_prefix`, omits an empty `limit`, and converts a string limit to a number. An empty enrichment selection uses the default.
7. Whitespace edits that yield equal normalized fetch inputs retain preview. This rule does not strip whitespace from every config value.
8. Unknown config fields remain intact for the same plugin and participate in the signature. Canonical serialization sorts object keys, including nested keys.
9. RSS/Atom normalization removes `article_path_prefix`, `exclude_path_prefixes`, `title_suffixes`, and `limit`. HTML normalization removes `include_any` and `exclude_any`.
10. Preview validity does not replace save validation. Save still requires an explicit existing owner profile, a valid draft, and unsaved changes.

## Mutation Rules

1. Clear and mark-read send `{ dismissed: true }`. The server preserves the shelf flag and note.
2. Shelf sends `{ saved: true }`. Back to desk sends `{ saved: false, note: "" }`.
3. Save note sends `{ saved: true, note }`. The note limit is 10,000 characters.
4. Undo sends the complete previous `dismissed`, `saved`, and `note` values.
5. Restore sets only `dismissed: false` for the supplied IDs. It preserves notes and shelf flags.
6. Restore applies only to loaded dismissed entries within the profile, time window, and active source filter, up to 200 items. Without a source filter, it covers loaded entries across the profile.
7. The server validates ownership of every ID in a batch before it changes any state.
8. The frontend serializes feed writes while applying queued changes optimistically. Only the affected cards are blocked. A failed write rolls back failed and unsent changes, cancels unsent jobs, and requires a feed reload. Undo and batch restore wait for the queue to finish.
9. The source editor previews the last 30 days and displays at most 10 results. Preview writes no source or cached data.
10. Save requires a successful preview of the current normalized fetch signature. Metadata-only edits retain preview; an empty preview still permits save.
11. Source-write API calls and `Save only` never fetch changes. `Save and fetch` explicitly writes, then refreshes the enabled saved source through its existing route.
12. Disable excludes a source from the feed and refreshes, but preserves its cached data and state.
13. Source deletion deletes its cached changes, shelf state, and notes. Profile deletion also deletes its sources.
14. Profile rename updates its source membership. Profile names require 1 to 100 characters; source names require 1 to 200 characters.
15. Every source config contains an absolute HTTP or HTTPS `url` without URL credentials. Save requires an existing `profile`; discovery and preview do not.
16. RSS/Atom config supports `include_any` and `exclude_any` as term arrays, plus an optional `enrichment_profile`.
17. HTML news config requires `article_path_prefix`. Its optional `limit` is an integer from 1 to 100.
18. Plugin switches remove incompatible fields. Other config fields remain intact for the same plugin.
19. Anonymous import requires confirmation and a signed-in owner. It copies data without removal of anonymous data or the token.
20. The existing browser-state import remains explicit and optional. It never replaces server state during normal feed loads.
21. Imports are idempotent. Existing destination state takes precedence, and browser-state import only matches sources that belong to the destination owner.
22. Import actions are inside the GitHub account menu. Anonymous import remains available when the account has anonymous data; there is no automatic import prompt on the main view.

Browser-state import keys retain the existing format: `source_id:external_id`, with URL and then title as fallbacks.

Unsaved card note edits remain in memory across same-owner source, profile, window, and feed reloads.
A successful note write removes its local override. Without a local edit, the card uses the current server note.
Account changes discard these local edits. They do not replace durable server state.

## Editor Actions

1. Preview stays near name and profile controls. RSS/Atom Filters collapse with an include/exclude count summary and open for configured terms.
2. A sticky action bar exposes preview, save, progress, and save-block reasons on desktop and mobile.
3. Save-block reasons distinguish unsupported plugin, invalid name, clean draft, absent current preview, and absent existing profile. Busy actions block concurrent requests.
4. Inline `Create and select` creates a profile and selects it for the draft. The independent profile manager retains create, rename, and confirmed delete.
5. Enabled drafts offer both `Save and fetch` and `Save only`. Disabled creation offers `Save only` without a refresh.
6. A save sends `POST /sources` for creation or `PATCH /sources/{id}` for an edit. The frontend retains the returned `Source` and ID immediately.
7. The saved draft becomes clean before refresh or synchronization. Only a successful save response with `enabled: true` permits the requested follow-up refresh.
8. Fetch uses `POST /sources/{saved.id}/refresh`. Fetch failure includes request failure or a response with source errors; the successful save remains intact.
9. Fetch failure offers explicit `Retry Fetch latest` against the saved ID, without another source write or duplicate creation.
10. Synchronization reloads editor data and requests a profile/feed reload. Synchronization failure retains the successful save and its ID.
11. `Retry sync only` repeats data reads without source writes or refreshes. It never replays creation, update, or deletion.
12. Saved-source enable, disable, duplicate draft, confirmed delete, and `Fetch latest` remain independent actions. Refresh requires an enabled saved source and a clean draft.
13. `Add another` clears the source draft, selection, preview, search, and save/fetch result. It returns focus to search without an implicit profile selection.
14. `View source` requires successful fetch and synchronization, an enabled saved source, and a clean draft. It checks the source owner against the current identity.
15. `View source` sets URL `profile`, `source`, and `sourceName` from the saved source and closes the editor. It preserves `since` and `sort`.
16. If fetched items fall outside the current window, offer the existing Window control for an explicit wider selection. Never widen it automatically.
17. Empty-desk status uses a one-line message in the existing queue header, not a duplicate panel. It distinguishes load progress, error, no active sources, shelved items, cleared items, and no unread items.
18. Empty-desk guidance uses source management, restore, or the existing Window control. Account setup remains separate from the empty queue message.

The header exposes account/import actions through the GitHub icon menu. The Sources count opens the editor, and Cleared opens the scoped cleared-item list with individual and batch restore controls. An empty shelf uses only its compact header.

## Discard Boundaries

1. `needsSave` distinguishes a new unsaved candidate from a saved source with edits. It does not determine discard warnings.
2. A discard warning requires manual changes relative to the selected baseline. `draftSignature` compares the trimmed name, profile, enabled state, and normalized fetch signature.
3. Untouched candidates, discovery results, previews, and search text require no discard prompt. A new candidate still requires explicit Save.
4. Reverted name, profile, enabled-state, or fetch edits require no prompt when the normalized draft matches its baseline. Equivalent normalized URL/filter whitespace also requires no prompt.
5. Close, editor Escape, source/candidate replacement, discovery, and `Add another` guard actual source edits before loss. `View source` also guards editor exit.
6. Unsubmitted profile creation or rename text warns only on editor exit, not source replacement. Successfully created profiles persist after source draft discard or editor close.
7. Successful save resets the baseline before fetch or synchronization. Later fetch or synchronization failure does not create an unsaved-draft warning.
8. `Duplicate draft` preserves current draft edits and preview without a discard prompt. Save creates a separate subscription; the original remains unchanged.
9. In-app discard uses a custom nested modal `<dialog>` owned by the source editor. `Keep editing` is the default focus and cancel action.
10. The confirmation traps focus. Escape cancels only the top dialog and restores focus without editor closure or feed shortcuts.
11. Confirm executes the captured action exactly once. Pending actions belong to the current owner and become invalid on owner change or editor unmount.
12. Source disable, source delete, and profile delete use custom confirmations, never browser `confirm()`. Their descriptions state data retention or deletion effects.
13. `beforeunload` applies only to actual source edits, unsubmitted profile text, or a pending mutation. Discovery and preview alone never register it.
14. The browser owns reload, navigation-away, and tab-close prompts, including their text and availability. These unavoidable native prompts are not custom in-app dialogs.
15. These rules change no API mutation routes, payloads, ownership checks, or persistence boundaries.

## Feed Selection

1. An active URL `source` selection becomes API `source_id`. The server filters within the selected profile and window before the 200-item limit.
2. A source-filter change requests new data; it never relies only on a client filter over a profile-wide limited response.
3. Restore uses the loaded response scope from Mutation Rules 6. An active source filter excludes other sources from Restore.
4. Without a source filter, Restore covers loaded dismissed entries across the profile and window, not every stored entry.
5. A newer editor profile synchronization supersedes older profile loads. Late successes cannot replace the newer list; late errors cannot close the editor or discard its draft.
6. Genuine current identity failures still block account access and apply Account Access rules. Stale-profile suppression never bypasses identity validation.
7. URL state updates remain pure: no history side effects occur inside React state updater callbacks. Each navigation action writes history once outside those callbacks.
8. One Back action returns to the prior selection. Back/forward reads the URL without another push, including after `View source`.

## Interface Checks

Desktop Clear and Mark read controls remain at the top right. Mobile controls remain below the content.
Card removal preserves the next card position. Failed swipe mutations return the card to its queue.
Existing card and navigation shortcuts remain available. The `e` shortcut opens the source editor.
Form fields and modal dialogs block feed shortcuts. Cmd/Ctrl+Enter saves a note; Escape closes its editor.

Run `npm test` in `frontend` for discovery-input, source-draft, and API safety checks.
Run `npm run build` in `frontend` for TypeScript and production bundle checks.
Run `uv run pytest` at the repository root for backend discovery, public-network, preview, and API tests.
Optional browser checks use an existing Playwright module and browser installation. This contract adds no dependency.
Run from `frontend` with an absolute module path:

```sh
PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs node --test tests/sourceEditor.browser.mjs
```

Without `PLAYWRIGHT_MODULE`, `node --test tests/sourceEditor.browser.mjs` skips the browser suite. A skip does not verify browser behavior.
Verify normalization, valid empty feeds, alternate resolution, candidate limits, deferred candidate fetches, no persistence, error statuses, and shared concurrency.
Verify automatic preview after result selection and profile-free preview.
Verify preview retention after name, profile, enabled-state, inline profile creation, and profile rename changes.
Verify that profile rename leaves a clean saved draft clean.
Verify invalidation when any fetch-signature input changes.
Verify preview retention for equivalent normalized whitespace and object-key order changes.
Verify unknown config preservation and participation in the fetch signature.
Verify both save actions, disabled creation, write-before-refresh order, and retained IDs after fetch or synchronization failure.
Verify that fetch retry performs no source write and synchronization retry performs no write or refresh.
Verify independent source actions, profile management, sticky actions, blocked reasons, filter summaries, and Advanced controls on desktop and mobile.
Verify `Add another` reset and explicit profile selection for each new candidate.
Verify owner-safe `View source`, its fetch/synchronization prerequisites, preserved URL window/sort, and explicit wider-window selection.
Verify distinct empty-desk header messages without a duplicate empty-desk panel.
Verify exact empty-release fallback, excluded URLs, out-of-window releases, visible HTTP failures, shared budgets, and branch-feed encoding.
Verify original release URL retention, one card per Atom commit ID, fallback metadata, and cached state after releases appear.
Verify untouched candidates, reverted edits, normalized whitespace, duplicate drafts, and successful saves with failed fetch or synchronization.
Verify custom discard, disable, and delete dialogs, default cancel focus, focus trap, top-only Escape, and exactly-once owner-bound confirmation.
Verify created profile persistence and exit-only warnings for unsubmitted profile text.
Verify native unload protection only for actual unsaved changes or pending mutations.
Verify server source filters before the limit and fresh data after filter changes.
Verify Restore scope with and without a source filter.
Verify stale profile successes and errors after newer synchronization, draft retention, and genuine identity failure handling.
Verify one history write per navigation action and one Back action to the prior selection.
These are required checks, not claims of completed tests or live deployment.
[Behavior Spec](../docs/spec.md) defines private server-backed feeds. [User Data](../docs/user-data.md) defines ownership, storage, and explicit imports.
The session reset rules above define the exception to the account-token and owner-header requirements.
