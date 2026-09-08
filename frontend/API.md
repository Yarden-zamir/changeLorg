# Frontend API Contract

This contract describes the requests from `src/lib/api.ts`, `App.tsx`, `AccountArea.tsx`, and `SourceEditor.tsx`.
The backend must enforce account isolation. Client checks do not replace server authorization.

## Account Access

1. Except for session reset, every API request includes `X-Anonymous-Token` with a persistent UUIDv4 from browser `localStorage`.
2. Every API request includes cookies through `credentials: "include"` and bypasses the browser cache.
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
  published_at: string;
  fetched_at: string;
  dismissed: boolean;
  saved: boolean;
  note: string;
  state_updated_at: string | null;
};
type ChangeState = { dismissed?: boolean; saved?: boolean; note?: string };
type PreviewChange = Pick<Change,
  "title" | "url" | "summary" | "content" | "published_at" | "external_id"
>;
```

Dates use ISO strings. Required text fields use empty strings rather than `null`.
Feed windows use `24h`, `7d`, `30d`, `90d`, or `365d`.
`source_count` counts enabled sources. The profile list includes empty profiles and excludes other owners.
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

## Source Discovery

1. One search input matches saved sources and optional catalog suggestions by name. Name search is not full-web search.
2. Absolute HTTP or HTTPS URLs, bare domains, and GitHub `owner/repo` expose an explicit Discover action. Bare inputs use HTTPS.
3. Keystrokes never trigger discovery or external source fetches. Name queries can request catalog suggestions after a debounce.
4. Catalog failure does not block link discovery. Discovery requires no catalog preset or profile.
5. `POST /sources/discover` uses the account, owner, cookie, CSRF, and private-cache rules above. It writes no profiles, sources, changes, or state.
6. GitHub repository roots, optional `.git` suffixes, `/releases`, `/releases/latest`, and `/releases/tag/...` map to `/releases.atom`.
7. A release tag or latest-release URL selects the repository's releases feed, not a feed limited to one release.
8. GitHub `/tags` maps to `/tags.atom`. Direct `/releases.atom` and `/tags.atom` URLs preserve their stream and query string.
9. Unsupported GitHub paths, such as `/issues`, return `422` rather than silently select a releases feed.
10. Discovery fetches one normalized target and recognizes RSS/Atom from the body, including valid empty feeds. Suffixes and response media types alone prove nothing.
11. HTML discovery accepts `<link rel="alternate">` with `application/rss+xml` or `application/atom+xml`. Relative links use the final response URL and first valid `<base href>`.
12. Candidates pass structural public-URL checks, deduplicate by resolved URL, and stop at 10. Discovery neither resolves candidate DNS nor fetches alternate candidates.
13. Candidates use `plugin: "rss-atom"`, `enabled: true`, a display name, and `config.url`. They contain no profile and remain unverified until preview.
14. A successful response with no direct feed or advertised alternates returns `[]`. It does not invent an HTML handler or selectors.
15. One result automatically selects its handler and starts preview. With multiple results, an explicit choice automatically starts preview.
16. Preview requires no profile. Save requires explicit profile selection or creation and an explicit save action.
17. Any draft change invalidates preview, including source name and profile changes. Profile creation that assigns the draft's profile also invalidates preview.
18. Manual handler, URL, and enrichment controls stay under Advanced, collapsed for RSS/Atom drafts. The custom HTML fallback requires an explicit `article_path_prefix`.
19. Discovery uses the same public fetch limits and invocation budget as preview. Redirects share that budget; preview has its own invocation.
20. The shared semaphore permits four concurrent operations across discovery, previews, manual refreshes, and scheduled refreshes. Discovery has no separate capacity pool.

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

## Mutation Rules

1. Clear and mark-read send `{ dismissed: true }`. The server preserves the shelf flag and note.
2. Shelf sends `{ saved: true }`. Back to desk sends `{ saved: false, note: "" }`.
3. Save note sends `{ saved: true, note }`. The note limit is 10,000 characters.
4. Undo sends the complete previous `dismissed`, `saved`, and `note` values.
5. Restore sets only `dismissed: false` for the supplied IDs. It preserves notes and shelf flags.
6. Restore applies only to dismissed entries in the loaded profile and time window, up to the 200-item feed limit.
7. The server validates ownership of every ID in a batch before it changes any state.
8. The frontend serializes feed mutations. A failed mutation requires a feed reload before another feed mutation.
9. The source editor previews the last 30 days and displays at most 10 results. Preview writes no source or cached data.
10. Any source draft change requires a new preview before save, including name and profile changes. An empty preview still permits save.
11. Save does not fetch source changes. Fetch latest explicitly refreshes the saved source.
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
22. Dismissal hides the anonymous import prompt, not the action. Authenticated accounts with anonymous data retain an unobtrusive `Import anonymous data` control.

Browser-state import keys retain the existing format: `source_id:external_id`, with URL and then title as fallbacks.

## Interface Checks

Desktop Clear and Mark read controls remain at the top right. Mobile controls remain below the content.
Card removal preserves the next card position. Failed swipe mutations return the card to its queue.
Existing card and navigation shortcuts remain available. The `e` shortcut opens the source editor.
Form fields and modal dialogs block feed shortcuts. Cmd/Ctrl+Enter saves a note; Escape closes its editor.

Run `npm test` in `frontend` for discovery-input unit tests and API safety checks.
Run `npm run build` in `frontend` for TypeScript and production bundle checks.
Run `uv run pytest` at the repository root for backend discovery, public-network, preview, and API tests.
Verify normalization, valid empty feeds, alternate resolution, candidate limits, deferred candidate fetches, no persistence, error statuses, and shared concurrency.
Verify automatic preview after result selection, profile-free preview, explicit save, and preview invalidation after every draft change in the editor.
These checks describe local editor verification, not an updated live deployment.
[Behavior Spec](../docs/spec.md) defines private server-backed feeds. [User Data](../docs/user-data.md) defines ownership, storage, and explicit imports.
The session reset rules above define the exception to the account-token and owner-header requirements.
