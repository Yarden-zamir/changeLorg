# changelorg

[![kitshn](https://kitshn.yarden-zamir.com/b/Yarden-zamir/changeLorg.svg)](https://changelorg.yarden-zamir.com)

Paste X/Twitter or Bluesky profile/post links into source discovery to follow an account without API keys. X uses its public embedded timeline, which may be unavailable or rate-limited; Bluesky uses public RSS. Social subscriptions produce one card per post and retain the usual profile, shelf, note, and read-state controls.

Personal changelog and news tracker with durable profiles, source subscriptions, and GitHub or anonymous browser identity.

changelorg provides:

- A uv Python FastAPI backend.
- A uv Python Typer CLI.
- A local Python plugin system for adding new source types.
- A React feed and source editor with URL/GitHub feed discovery, saved-source search, optional catalog suggestions, previews, and explicit profile saves.

## Quick Start

```sh
uv sync
uv run changelorg init
uv run changelorg sources add-rss "GitHub Blog Changelog" "https://github.blog/changelog/feed/"
uv run changelorg changes generate --since 7d
uv run changelorg serve --reload
```

The API runs at `http://127.0.0.1:8000` by default.
The CLI defaults to your GitHub owner, `github:8178413`. Anonymous browser accounts start empty and use the source editor.

## Frontend

```sh
npm install --prefix frontend
npm run dev --prefix frontend
```

The frontend expects the API at `http://127.0.0.1:8000`. Override it with `VITE_API_URL`.

### Source Editor

The approved editor contract covers these actions. This summary does not certify tests or deployment.

- Discover a website or GitHub feed, then preview before save. Preview needs no profile and writes nothing.
- Exact GitHub release feeds fall back to default-branch commits only after valid, globally empty Atom responses. Each eligible commit becomes one card.
- Old releases outside the window never trigger fallback. Neither do query-filtered feeds, tags, malformed/error responses, private/nonexistent repositories, or lookalike hosts.
- Discovery retains the release URL and names the candidate `{owner}/{repo} updates`. Every fetch rechecks releases; the first release switches future fetches back.
- Cached commit cards and their shelf state, dismissed state, and notes remain intact. Fallback cards show a Commit badge; nonempty fallback previews explain it.
- Direct `/commits.atom`, `/commits`, and `/commits/{branch}.atom` inputs work, including valid encoded branches. Branch HTML paths such as `/commits/main` remain unsupported.
- Release checks and fallback share public fetch limits: 12 requests, 60 seconds, 2 MiB per response, and three redirects per request.
- Discovery shares four concurrent source operations with previews and refreshes. HTTP failures remain visible, never successful empty results.
- Select a profile or use inline `Create and select`. The independent profile manager retains create, rename, and confirmed delete.
- Name, profile, and enabled-state edits retain preview, including inline profile creation and profile rename. Profile rename leaves a clean source draft clean.
- Only a normalized fetch-signature change invalidates preview: plugin and all retained config except `profile`.
- Equivalent normalized whitespace retains preview. URL, filters, enrichment, article options, limits, user agent, and unknown retained config participate in the signature.
- Preview stays near name and profile controls. Filters collapse with a count summary and open when configured; Advanced retains the manual controls.
- The sticky action bar exposes preview, save actions, progress, and blocked reasons on desktop and mobile.
- `Save only` writes without a fetch. `Save and fetch` writes first, then refreshes the enabled saved source; disabled creation retains `Save only`.
- Fetch or synchronization failure preserves the successful save and source ID. Fetch retry never repeats the save; synchronization retry performs reads only.
- Untouched candidates and previews require no discard prompt, even when the candidate still needs Save. Only manual normalized edits trigger source-draft warnings.
- Reverted edits and equivalent normalized whitespace require no prompt. A successful save stays clean after later fetch or synchronization failure.
- In-app discard, source disable, and source/profile deletion use custom modal confirmations. `Keep editing` or Cancel receives default focus.
- Confirmation traps focus; Escape cancels only the top dialog. Confirm runs once for the current owner and becomes invalid after owner change.
- Created profiles persist after draft discard. Unsubmitted profile names warn only on editor exit; duplicate drafts preserve current edits without a discard prompt.
- Native unload protection covers only actual unsaved edits or pending mutations. The browser owns unavoidable tab-close and navigation-away prompts, not custom dialogs.
- Saved sources retain independent enable, disable, duplicate draft, confirmed delete, and refresh actions.
- `Add another` resets the source entry without an implicit profile selection.
- After successful fetch and synchronization, `View source` selects that source and its profile in the URL and closes the editor.
- `View source` preserves the current window and sort. Choose a wider window through the existing Window control; the editor never changes it automatically.
- Existing queue headers distinguish load progress, error, no active sources, shelved items, cleared items, and no unread items without a duplicate empty-desk panel.
- Source-filter changes fetch new data with server-side `source_id` before the 200-item limit. Restore covers only loaded dismissed entries in that scope.
- Without a source filter, Restore covers loaded entries across the profile and window. It never restores unloaded data.
- Newer profile synchronization supersedes stale successes and errors without loss of the editor draft. Genuine identity failures still block access.
- Navigation writes history once outside pure state updater callbacks. One Back action restores the prior selection, including after `View source`.

See [Behavior Spec](docs/spec.md#frontend-behavior), [Frontend API Contract](frontend/API.md#editor-actions), and [User Data Contract](docs/user-data.md#editor-save-boundaries).
These editor rules introduce no backend API or identity change.

### Optional Browser Checks

Use an existing Playwright module and browser installation. No new dependency is required.
Run from `frontend`:

```sh
PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs node --test tests/sourceEditor.browser.mjs
```

Without `PLAYWRIGHT_MODULE`, the browser suite skips. A skip does not verify browser behavior.
See [Interface Checks](frontend/API.md#interface-checks) for the full check list. These commands do not certify test results or deployment.

## Storage and Deployment

The backend uses embedded DuckDB 1.5.5. The container stores its database at `/data/changelorg.duckdb` on the persistent KitSHn data mount.
Run one app container with one Uvicorn process. Do not run the CLI against the live database.

Startup imports an existing `changelorg.db` when `changelorg.duckdb` does not exist.
After success, it deletes the SQLite file and its journal files without a backup.
Stop old app processes before this migration. A SQLite-only release cannot use the migrated database.

GitHub authentication uses the optional `github-auth` Compose profile.
Production requires `COMPOSE_PROFILES=github-auth`, `CHANGELORG_AUTH_ENABLED=true`, and OAuth credentials.
The API verifies proxy cookies and resolves numeric GitHub account IDs. Verify a real OAuth login before production use.
The proxy accepts any GitHub user; inbound identity headers never establish an account.

Previews default to authentication disabled and need no OAuth credentials.
The API origin is configurable through `CHANGELORG_PUBLIC_ORIGIN`.
`CHANGELORG_CORS_ORIGINS` defaults to `http://localhost:5173,http://127.0.0.1:5173` for development.
See [KitSHn prerequisites and auth contract](kitshn.md) for production parameters, callback setup, and preview isolation.

## Plugin Directories

Local plugins are loaded from:

- `./plugins`
- `~/.config/changelorg/plugins`
- Paths in `CHANGELORG_PLUGIN_PATH`, separated by the OS path separator

See `docs/spec.md` for the full behavior and plugin contract.
