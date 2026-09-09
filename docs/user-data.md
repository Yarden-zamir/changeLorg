# User Data Contract

This document defines approved target behavior alongside [Behavior Spec](spec.md) and [Frontend API Contract](../frontend/API.md).
It does not certify implementation, tests, migration execution, or deployment.

## Durable Storage

- Embedded DuckDB 1.5.5 stores profiles, sources, changes, and change state.
- Config and metadata use native `JSON`. Timestamps use `TIMESTAMPTZ`. Flags use `BOOLEAN`.
- Source and change IDs use database sequences. New IDs cannot collide with migrated IDs or reuse old sequence values.
- One process owns the database. It serializes database transactions and performs network fetches outside the database lock.
- The deployment uses one app container and one Uvicorn worker with a persistent data mount.
- Before any worker or replica increase, revisit the storage model. Do not run the CLI against the live database.
- Data survives restarts and deploys. No automatic expiry applies, including for anonymous owners.
- Time windows filter feed results and source fetches. They never delete older data.

## SQLite Migration

- Migrate all old SQLite sources, changes, and existing change state to DuckDB.
- Preserve source IDs, change IDs, external IDs, source membership, timestamps, metadata, flags, and notes.
- The shipped SQLite schema has no profile table. It derives all profiles from sources.
- Create explicit profiles from old source `config.profile` values. Missing, non-string, null, or blank legacy values retain the `dev` default.
- Assign all migrated data to `github:8178413`.
- GitHub's user API confirms that `Yarden-zamir` has numeric ID `8178413`. The numeric ID, not the login, controls ownership.
- Default startup migration reads `changelorg.db` when `changelorg.duckdb` does not exist.
- An explicit database path that contains SQLite also triggers migration.
- Stop all old database owner processes before migration.
- Commit and checkpoint the complete DuckDB migration before replacement of the database or removal of SQLite.
- If conversion fails, retain SQLite and report failure. Do not serve a partial migration.
- After successful migration, delete SQLite and its journal files. Create no backup.
- Record completed migration and default seeding. Startup must not restore defaults that the owner deletes.

This migration is irreversible without an external copy. A SQLite-only release cannot read the migrated database.

## Ownership and Profiles

- Each source has one `owner_id`. Changes and their state inherit ownership from their source.
- Profiles use the key `(owner_id, name)`. Empty profiles remain explicit records.
- Each saved source belongs to one profile through the known string field `config.profile`, not a separate membership list.
- Source creation and updates require an existing profile for the current owner. Discovery and preview require no profile.
- Profile lists include empty profiles. `source_count` counts enabled sources only.
- The same public URL under different owners represents separate private subscriptions and changes.
- Owner data includes profiles, sources, cached changes, dismissed flags, shelf flags, and notes.
- Source deletion explicitly removes its changes and state in one transaction.
- Profile deletion explicitly removes its sources, changes, and state in one transaction.
- Profile rename updates source membership in the same transaction.
- Disable preserves cached data and state but excludes the source from API feeds and all refresh paths.
- Public requests pass the resolved owner explicitly. They never use a default owner or the store's `owner_id=None` scope.
- Only internal scheduled refreshes use the all-owner scope. Public responses never expose cross-owner refresh details.

## Repository Feed Data

- Exact public GitHub release feeds use default-branch commits only after a valid, globally empty Atom response, before window or local filters.
- Old releases outside the window do not trigger fallback. Query-filtered feeds, tags, malformed/error responses, private/nonexistent repositories, and lookalike hosts do not qualify.
- [GitHub Release Fallback](../frontend/API.md#github-release-fallback) defines URL checks, visible failures, and shared network budgets for discovery, preview, and refresh.
- Discovery names release candidates `{owner}/{repo} updates` and retains the normalized release URL. Saved `config.url` never becomes the fallback commit URL.
- Every fetch checks releases again. The first release switches subsequent fetches to releases without a source edit or new subscription.
- Each eligible commit uses its unique Atom ID as `external_id`. Normal `(source_id, external_id)` deduplication and owner isolation apply.
- Commit metadata adds `feed_kind: "commit"`, `fallback_reason: "no_releases"`, and `effective_feed_url`. Existing `feed_url` retains the configured release URL.
- The switch never deletes cached commit cards or changes their IDs, dismissed flags, shelf flags, notes, or state timestamps.
- Time windows control cached card visibility, not retention. Fallback introduces no separate data store, migration, or API mutation.

## Editor Save Boundaries

- Discovery and preview write no profiles, sources, changes, or state. Explicit profile creation persists a profile independently of source save.
- Preview validity follows the normalized plugin/config signature except `profile`, as defined in [Preview Validity](../frontend/API.md#preview-validity).
- Name, profile, and enabled-state edits retain preview. Inline profile creation with selection and profile rename also retain preview.
- Profile rename updates source membership transactionally. The editor mirrors that change without a source write or a clean-to-dirty transition.
- Source-write API calls persist the subscription without a fetch. `Save only` keeps that boundary, including for disabled source creation.
- Explicit `Save and fetch` first writes the source, then refreshes only the enabled source from the successful save response.
- Fetch or synchronization failure does not roll back a successful source write or discard its returned ID.
- Fetch retry targets that saved ID without another source write. Synchronization retry performs data reads, without writes or refreshes.
- Unknown config fields remain intact for the same plugin. The editor introduces no storage migration, backend API, or identity change.
- `View source` selects the saved source's profile and source URL parameters only for the current owner. It preserves the window and sort.
- `Add another` starts a separate source entry without an implicit profile selection. It does not change saved subscriptions or profiles.
- A new candidate needs Save but requires no discard warning until manual normalized edits differ from its selected baseline.
- Untouched preview/candidates, reverted metadata or fetch edits, and equivalent normalized URL/filter whitespace require no warning. Successful save resets that baseline before fetch or synchronization.
- Failed fetch or synchronization after successful save creates no unsaved-draft warning. It never rolls back persisted source data.
- Custom in-app discard protects actual source edits. Unsubmitted profile creation or rename text warns only on editor exit, not source replacement.
- Created profiles remain durable after draft discard or editor close. Discard never reverses profile creation.
- `Duplicate draft` preserves current draft edits and preview without a discard prompt. Explicit Save creates another subscription and leaves the original unchanged.
- Discard, source disable, source delete, and profile delete use editor-owned custom modal confirmations, not browser `confirm()`.
- Cancel receives default focus; discard labels it `Keep editing`. Focus stays in the top dialog; Escape cancels only that dialog.
- Confirm runs its captured action exactly once for the same owner. Owner change or editor unmount invalidates the action.
- `beforeunload` protects only actual source edits, unsubmitted profile text, or a pending mutation. Browsers own unavoidable tab-close and navigation-away prompts.
- Newer profile synchronization supersedes older profile loads. Late successes and errors cannot replace profiles, close the editor, or discard its draft.
- Genuine current identity failures still block access and clear old account data under Request Safety. Stale-profile checks never override identity validation.

## Loaded Feed State

- An active URL `source` filter becomes API `source_id`. The server applies profile, window, and source filters before the 200-item limit.
- Source-filter changes request new data rather than filter an already limited profile response in the browser.
- Restore changes only loaded dismissed entries in that scope. With a source filter, it excludes every other source.
- Without a source filter, Restore covers loaded dismissed entries across the profile and window, up to 200 items, not all stored entries.
- Restore preserves notes and shelf flags. [Mutation Rules 6](../frontend/API.md#mutation-rules) defines its scope without a mutation payload change.
- URL state updater callbacks contain no history side effects. Each navigation action writes history once; one Back action restores the prior selection.

## Anonymous Access

- The browser creates a cryptographically random UUIDv4 and retains it in `localStorage`.
- Every frontend API request sends this value as `X-Anonymous-Token`, including requests after GitHub sign-in.
- The token is a bearer capability. Anyone with the token can access that anonymous owner's data.
- The backend validates the UUIDv4 and derives `anon:<sha256>` from the token. It never stores or logs the raw token.
- The hash identifies the owner but is not itself an accepted bearer capability.
- Anonymous data stays durable on the backend. The browser token grants access; it does not contain the data.
- Loss of the token means loss of anonymous access. Sign-in does not recover data for an unknown anonymous token.
- An invalid stored token blocks access instead of silent replacement.
- Sign-in, sign-out, and import leave the anonymous token intact.

## GitHub Sign-In

- GitHub sign-in is optional through `oauth2-proxy`. Anonymous access remains available when authentication is disabled.
- All GitHub users can sign in. No user, email, or organization allowlist applies.
- The browser navigates to `/auth/start?rd=/` or `/auth/sign_out?rd=/` for sign-in or sign-out.
- The proxy cookie uses Secure, HttpOnly, and SameSite=Lax. The app does not trust cookie contents directly.
- With authentication enabled, the app sends the browser cookie to internal `GET http://oauth2-proxy:4180/auth/auth`.
- Only a successful internal response supplies `X-Auth-Request-Access-Token`.
- The app uses that token for an authenticated server-side request to `https://api.github.com/user`.
- The app validates the returned numeric `id` and derives `github:<numeric-id>`. Login and email never identify the owner.
- Public identity headers, browser-supplied GitHub tokens, and an owner header alone never grant access.
- A failed or invalid authenticated session blocks account access. It never silently redirects a mutation into the anonymous owner.
- The account-error screen offers explicit session recovery through `POST /session/reset`, even when authentication is disabled.
- Reset requires the mutation header and a trusted browser origin, but no identity. It clears OAuth cookies and preserves anonymous data.
- The app uses GitHub access tokens only in memory. It never writes them to `localStorage`, its database, or logs.
- Public auth responses never expose access-token headers. The proxy's encrypted HttpOnly session cookie is not frontend token storage.
- Logs must exclude raw anonymous tokens, OAuth tokens, and session cookies.

## Request Safety

- `GET /me` resolves identity before account access and needs no owner header.
- Later frontend requests include `X-Changelorg-Owner` with the exact owner ID from `/me`.
- The server checks that header against the current request identity before account access.
- An owner mismatch returns `412` before any mutation. The owner header is a consistency check, not authentication.
- Every `POST`, `PATCH`, and `DELETE` requires `X-Changelorg-Request: 1` for cross-site request forgery protection.
- Browser mutation origins must match an explicit allowlist. CORS allows credentials and the three contract headers, never wildcard origins.
- KitSHn deployments derive their public origin from `prod` or `pr-<number>`, unless an explicit public origin overrides it.
- Unknown deployment names require an explicit public origin. Deployment defaults exclude localhost origins.
- Mutation bodies cannot exceed 2 MiB, including requests without `Content-Length`.
- Private responses use `Cache-Control: private, no-store`.
- Batch mutations check every supplied ID's ownership before any write.
- On `401` or `412`, the frontend discards account access and resolves `/me` again. It never retries the mutation automatically.
- An account change clears old account data, drafts, and undo state. Late responses cannot alter the new account.

## Explicit Imports

- After sign-in, the frontend prompts for anonymous import if `/me` reports anonymous data.
- After prompt dismissal or a previous import, an account control still permits explicit anonymous import.
- `POST /me/import` requires user confirmation and a signed-in GitHub destination owner.
- The backend copies anonymous profiles, sources, changes, and state into that account, including empty profiles.
- Import leaves anonymous data and the browser token intact. It is a copy, not an owner reassignment.
- Repeated imports are idempotent. Existing destination data and state take precedence: keep server.
- Equivalent sources match by plugin and config. Duplicate changes match by destination source and external ID.
- Derived external IDs that include a source ID use the destination source ID after import.
- `POST /me/import-state` imports old browser state only after an explicit user action.
- Browser-state keys retain `source_id:external_id`, with URL and then title as fallbacks.
- Only keys for sources that belong to the destination owner can match. Another owner's source IDs never grant access.
- Browser-state import is idempotent and preserves existing server state. Normal feed loads never import browser state.
