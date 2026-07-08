# KitSHn Recipe

This repository is a KitSHn recipe for deploying changelorg to `changelorg.yarden-zamir.com`.

## Runtime

- FastAPI serves the API and the built Vite frontend from one container.
- SQLite data lives under `${KITSHN_DATA_DIR}/data` on the VPS.
- Sources are seeded on startup when missing.
- The backend refresh loop runs every hour with `CHANGELORG_REFRESH_WINDOW=30d`.
- Manual public generation is disabled unless `CHANGELORG_MANUAL_GENERATE_API=true` is set.

## Files

- `.kitshn.yaml`: event-to-environment mapping.
- `.github/workflows/kitshn.yml`: GitHub Actions deployment workflow.
- `compose.yml`: deployment services.
- `Caddyfile.j2`: public route.
