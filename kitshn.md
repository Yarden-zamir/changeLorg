# KitSHn Recipe

This repository is a KitSHn recipe for deploying changelorg to `changelorg.yarden-zamir.com`.

## Runtime

- FastAPI serves the API and the built Vite frontend from one container.
- Embedded DuckDB 1.5.5 uses `/data/changelorg.duckdb` inside the app container.
- `${KITSHN_DATA_DIR}/data` persists that database on the VPS. There is no database service.
- One app container runs one Uvicorn process with `--workers 1`. Do not scale either count.
- One workflow concurrency group serializes recipe deployments. Active deployments are not cancelled by a new push.
- Default sources seed once under `github:8178413`. Startup never restores deleted subscriptions.
- The backend refresh loop runs every hour with `CHANGELORG_REFRESH_WINDOW=30d`.
- Manual public generation is disabled unless `CHANGELORG_MANUAL_GENERATE_API=true` is set.
- PR previews deploy to `pr.<number>.changelorg.yarden-zamir.com`.

## SQLite Migration

Startup imports `/data/changelorg.db` when `/data/changelorg.duckdb` does not exist.
After a successful import, the store deletes the SQLite file and its journal files. It creates no backup.
The data mount stays unchanged, so an existing installation retains its data through this migration.

Stop every old app process before the first DuckDB startup. Do not run the CLI against the live database.
Rollback to a SQLite-only release cannot use the migrated database.

## GitHub Authentication

The `github-auth` Compose profile adds `oauth2-proxy` and `auth-socket-proxy`.
The proxy uses [oauth2-proxy v7.15.4](https://github.com/oauth2-proxy/oauth2-proxy/releases/tag/v7.15.4).
The latest-release check used `gh release view --repo oauth2-proxy/oauth2-proxy`.

Any GitHub user can sign in through `--email-domain=*`. There is no user, organization, or email allowlist.
The cookie name is `_changelorg_oauth`. It uses Secure, HttpOnly, SameSite=Lax, path `/`, and no shared cookie domain.
`--cookie-refresh=0` disables cookie refresh through internal auth checks.

Caddy routes `/auth/*` through `${KITSHN_SOCKET_DIR}/auth.sock`.
The auth socket proxy uses socat to forward that socket to `oauth2-proxy:4180`.
All other requests use the existing app socket. Neither service publishes a host TCP port.
Caddy does not use `forward_auth` or grant identity through request headers.
Caddy removes inbound identity headers and removes token response headers from public auth responses.

### API Prerequisite

The API implements this contract. Before production, verify the complete proxy login flow:

1. If `CHANGELORG_AUTH_ENABLED=true`, send the browser cookie to internal `GET http://oauth2-proxy:4180/auth/auth`.
2. Read `X-Auth-Request-Access-Token` only from a successful internal auth response.
3. Use that token for a server-side request to `https://api.github.com/user`.
4. Verify the numeric GitHub `id` and use it as the account identity.
5. Reject failed auth checks and invalid identities. Never trust inbound identity headers or browser-supplied tokens.
6. Enforce the configured public origin and CORS policy for authenticated requests.

`--set-xauthrequest=true` and `--pass-access-token=true` supply the internal token response header.
The API must not expose or log the token. It must not accept a username or email as the account identity.

### Production Prerequisites

1. Create a GitHub OAuth App with homepage `https://changelorg.yarden-zamir.com`.
2. Set its callback URL to `https://changelorg.yarden-zamir.com/auth/callback`.
3. Supply the variables and secrets below through the production KitSHn parameter scope.
4. Confirm that the resolved production parameters contain `COMPOSE_PROFILES=github-auth` and `CHANGELORG_AUTH_ENABLED=true`.
5. Verify the API prerequisite through a real GitHub login before production use.
6. Keep the existing data mount and stop old database owners before the first DuckDB startup.

KitSHn removes the `KITSHN_` prefix from GitHub variables and secrets before Compose reads them.

| GitHub variable or secret | Compose parameter | Production value |
| --- | --- | --- |
| Variable `KITSHN_COMPOSE_PROFILES` | `COMPOSE_PROFILES` | `github-auth`, required |
| Variable `KITSHN_CHANGELORG_AUTH_ENABLED` | `CHANGELORG_AUTH_ENABLED` | `true`, required |
| Variable `KITSHN_OAUTH2_PROXY_CLIENT_ID` | `OAUTH2_PROXY_CLIENT_ID` | GitHub OAuth App client ID, required |
| Secret `KITSHN_OAUTH2_PROXY_CLIENT_SECRET` | `OAUTH2_PROXY_CLIENT_SECRET` | GitHub OAuth App client secret, required |
| Secret `KITSHN_OAUTH2_PROXY_COOKIE_SECRET` | `OAUTH2_PROXY_COOKIE_SECRET` | 32 random bytes, base64 encoded, required |
| Variable `KITSHN_CHANGELORG_PUBLIC_ORIGIN` | `CHANGELORG_PUBLIC_ORIGIN` | Optional `https://changelorg.yarden-zamir.com`, without a trailing slash |
| Variable `KITSHN_CHANGELORG_CORS_ORIGINS` | `CHANGELORG_CORS_ORIGINS` | Optional additional trusted frontend origins, comma-separated |

Generate the cookie secret with `openssl rand -base64 32`. Store it as a secret, not in this repository.
Never print real parameters with `docker compose config`. Use `docker compose config --quiet` for real credentials.

Compose substitutes empty credentials so disabled profiles need no secrets.
When the profile starts, oauth2-proxy rejects missing credentials. The auth socket healthcheck also probes its `/ping` endpoint.
Enabling the profile does not enable API authentication; production requires both parameters above.

### Previews and Development

By default, `COMPOSE_PROFILES` is unset and `CHANGELORG_AUTH_ENABLED=false`.
Previews need no OAuth credentials. The `/auth/*` route has no active upstream in this mode.
Do not expose private data through unauthenticated previews.

The internal URL defaults to `http://oauth2-proxy:4180`; Compose fixes it to that service address.
If `CHANGELORG_PUBLIC_ORIGIN` is empty, the API derives the production or preview origin from `KITSHN_ENVIRONMENT`.
An unknown environment requires an explicit origin. The OAuth callback still defaults to the production origin.
Compose permits the derived origin and any explicit `CHANGELORG_CORS_ORIGINS`. It does not permit localhost by default.
Outside KitSHn, the API defaults to `http://localhost:5173,http://127.0.0.1:5173` for local frontend development.
The account-error screen provides an explicit session reset, even when the authentication profile is disabled.
`POST /session/reset` clears OAuth cookies without any change to anonymous data or its browser token.

For an authenticated preview, enable both auth parameters and set `CHANGELORG_PUBLIC_ORIGIN` to its HTTPS origin.
Use a separate GitHub OAuth App with that preview origin and its `/auth/callback` URL.
Set `CHANGELORG_CORS_ORIGINS` to that origin. Do not reuse production secrets in untrusted pull requests.

## Files

- `.kitshn.yaml`: event-to-environment mapping.
- `.github/workflows/kitshn.yml`: GitHub Actions deployment workflow.
- `compose.yml`: deployment services.
- `Caddyfile.j2`: public app and auth routes.
- `Dockerfile`: frontend build and single-process Python runtime from the frozen dependency lock.
