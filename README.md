# changelorg

Local-first changelog and news tracker for RSS/Atom feeds and future source plugins.

changelorg provides:

- A uv Python FastAPI backend.
- A uv Python Typer CLI.
- A local Python plugin system for adding new source types.
- A basic React/shadcn-style frontend feed.

## Quick Start

```sh
uv sync
uv run changelorg init
uv run changelorg sources add-rss "GitHub Blog Changelog" "https://github.blog/changelog/feed/"
uv run changelorg changes generate --since 7d
uv run changelorg serve --reload
```

The API runs at `http://127.0.0.1:8000` by default.

## Frontend

```sh
npm install --prefix frontend
npm run dev --prefix frontend
```

The frontend expects the API at `http://127.0.0.1:8000`. Override it with `VITE_API_URL`.

## Plugin Directories

Local plugins are loaded from:

- `./plugins`
- `~/.config/changelorg/plugins`
- Paths in `CHANGELORG_PLUGIN_PATH`, separated by the OS path separator

See `docs/spec.md` for the full behavior and plugin contract.
