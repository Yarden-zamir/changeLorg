# changelorg

Personal changelog and news tracker with durable profiles, source subscriptions, and GitHub or anonymous browser identity.

changelorg provides:

- A uv Python FastAPI backend.
- A uv Python Typer CLI.
- A local Python plugin system for adding new source types.
- A React feed and source editor with catalog search, previews, and profile management.

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
