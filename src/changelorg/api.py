from __future__ import annotations

import os
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Annotated

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from changelorg import store
from changelorg.auth import Identity, request_identity
from changelorg.discovery import discover_sources, normalize_discovery_url
from changelorg.enrichment import PROFILES
from changelorg.models import (
    Change,
    ChangeInput,
    ChangeUpdate,
    GenerationResult,
    Profile,
    ProfileCreate,
    Source,
    SourceCreate,
    SourceUpdate,
    TimeWindow,
    utc_now,
)
from changelorg.network import (
    SOURCE_FETCH_SLOTS,
    PublicFetchError,
    validate_source_config,
)
from changelorg.plugin import PluginConfigError, PluginManager
from changelorg.plugins.html_news import HtmlNewsPlugin
from changelorg.plugins.rss_atom import RssAtomPlugin
from changelorg.plugins.x import XPlugin, XUnavailable
from changelorg.refresh import HourlyRefreshLoop
from changelorg.request_body import BodyLimitMiddleware
from changelorg.seed import DEFAULT_SOURCES, seed_default_sources
from changelorg.service import generate_changes
from changelorg.timeutils import parse_window

CurrentIdentity = Annotated[Identity, Depends(request_identity)]


class RestoreRequest(BaseModel):
    ids: list[Annotated[int, Field(strict=True, gt=0)]] = Field(max_length=500)


class BrowserStateImport(BaseModel):
    state: dict[str, ChangeUpdate] = Field(max_length=5000)


class SourcePreview(BaseModel):
    window: TimeWindow
    changes: list[ChangeInput]


class SourceDiscoveryRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2000)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    if value.strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
        "0",
        "false",
        "no",
        "off",
    }:
        raise ValueError(f"{name} requires a boolean")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def create_app() -> FastAPI:
    auth_enabled = _env_bool("CHANGELORG_AUTH_ENABLED", False)
    proxy_url = os.environ.get(
        "CHANGELORG_OAUTH2_PROXY_URL", "http://oauth2-proxy:4180"
    ).rstrip("/")
    environment = os.environ.get("KITSHN_ENVIRONMENT", "")
    default_origins = (
        "" if environment else "http://localhost:5173,http://127.0.0.1:5173"
    )
    origins = [
        origin.strip().rstrip("/")
        for origin in os.environ.get("CHANGELORG_CORS_ORIGINS", default_origins).split(
            ","
        )
        if origin.strip()
    ]
    public_origin = os.environ.get("CHANGELORG_PUBLIC_ORIGIN", "").rstrip("/")
    if not public_origin and environment:
        if environment == "prod":
            public_origin = "https://changelorg.yarden-zamir.com"
        elif (
            environment.startswith("pr-")
            and environment.removeprefix("pr-").isascii()
            and environment.removeprefix("pr-").isdecimal()
        ):
            public_origin = f"https://{environment}.changelorg.yarden-zamir.com"
        else:
            raise ValueError(
                "Set CHANGELORG_PUBLIC_ORIGIN for this deployment environment"
            )
    if public_origin:
        origins.append(public_origin)
    if "*" in origins:
        raise ValueError("CORS requires explicit origins")
    manager = PluginManager([RssAtomPlugin(), HtmlNewsPlugin(), XPlugin()])

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.init_db()
        if _env_bool("CHANGELORG_SEED_DEFAULT_SOURCES", True):
            seed_default_sources()
        app.state.auth_enabled = auth_enabled
        app.state.oauth2_proxy_url = proxy_url
        with httpx.Client(
            timeout=10,
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": "changelorg"},
        ) as client:
            app.state.auth_client = client
            refresh_loop = HourlyRefreshLoop.from_env()
            app.state.refresh_loop = refresh_loop
            if HourlyRefreshLoop.enabled_from_env():
                refresh_loop.start()
            try:
                yield
            finally:
                await refresh_loop.stop()

    app = FastAPI(title="changelorg", version="0.2.0", lifespan=lifespan)
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=[
            "Content-Type",
            "X-Anonymous-Token",
            "X-Changelorg-Owner",
            "X-Changelorg-Request",
        ],
    )

    @app.middleware("http")
    async def private_requests(request: Request, call_next):
        if request.method in {"POST", "PATCH", "DELETE", "PUT"}:
            origin = request.headers.get("origin")
            if request.headers.get("X-Changelorg-Request") != "1" or (
                origin is not None and origin.rstrip("/") not in origins
            ):
                return JSONResponse(
                    {"detail": "Mutation origin or request header rejected"},
                    status_code=403,
                    headers={"Cache-Control": "private, no-store"},
                )
            length = request.headers.get("content-length")
            if length is not None and (
                not length.isdecimal() or int(length) > 2 * 1024 * 1024
            ):
                return JSONResponse(
                    {"detail": "Request exceeds 2 MiB"},
                    status_code=413,
                    headers={"Cache-Control": "private, no-store"},
                )
        response = await call_next(request)
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable"
            if request.url.path.startswith("/assets/") and response.status_code == 200
            else "private, no-store"
        )
        return response

    @app.exception_handler(KeyError)
    async def missing_record(request: Request, exc: KeyError):
        return JSONResponse({"detail": "Record not found"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid_value(request: Request, exc: ValueError):
        return JSONResponse(
            {"detail": "Invalid values, duplicate profile, or account limit exceeded"},
            status_code=409,
        )

    @contextmanager
    def fetch_slot():
        if not SOURCE_FETCH_SLOTS.acquire(blocking=False):
            raise HTTPException(429, "Source fetch capacity reached; retry later")
        try:
            yield
        finally:
            SOURCE_FETCH_SLOTS.release()

    def checked_config(
        source: SourceCreate, identity: Identity, *, require_profile: bool = True
    ) -> None:
        try:
            validate_source_config(source)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if require_profile and source.config.get("profile") not in {
            profile.name for profile in store.list_profiles(identity.owner_id)
        }:
            raise HTTPException(422, "Select an existing profile")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/session/reset", status_code=204)
    def reset_session(request: Request) -> Response:
        response = Response(status_code=204)
        for name in {"_changelorg_oauth", *request.cookies}:
            if name == "_changelorg_oauth" or name.startswith("_changelorg_oauth_"):
                response.delete_cookie(
                    name, path="/", secure=True, httponly=True, samesite="lax"
                )
        return response

    @app.get("/me")
    def me(identity: CurrentIdentity) -> dict[str, object]:
        return {
            "id": identity.owner_id,
            "authenticated": identity.authenticated,
            "login": identity.login,
            "auth_enabled": auth_enabled,
            "anonymous_has_data": bool(
                identity.anonymous_owner and store.has_data(identity.anonymous_owner)
            ),
        }

    @app.post("/me/import")
    def import_anonymous(identity: CurrentIdentity) -> dict[str, int]:
        if not identity.authenticated or identity.anonymous_owner is None:
            raise HTTPException(
                403, "Import requires a GitHub account and browser identity"
            )
        return store.import_anonymous(identity.anonymous_owner, identity.owner_id)

    @app.post("/me/import-state")
    def import_browser_state(
        body: BrowserStateImport, identity: CurrentIdentity
    ) -> dict[str, int]:
        return store.import_browser_state(body.state, identity.owner_id)

    @app.get("/refresh/status")
    def refresh_status(identity: CurrentIdentity) -> dict[str, object]:
        status = app.state.refresh_loop.status
        # Refresh errors contain private source names. Public status exposes only scheduler activity.
        return {
            "enabled": HourlyRefreshLoop.enabled_from_env(),
            "running": status.running,
            "last_started_at": status.last_started_at,
            "last_finished_at": status.last_finished_at,
        }

    @app.get("/plugins")
    def plugins(identity: CurrentIdentity) -> list[dict[str, object]]:
        return [plugin.model_dump() for plugin in manager.list()]

    @app.get("/enrichment-profiles")
    def enrichment_profiles(identity: CurrentIdentity) -> list[dict[str, str]]:
        return [{"name": name} for name in sorted(PROFILES)]

    @app.get("/catalog", response_model=list[SourceCreate])
    def catalog(
        identity: CurrentIdentity, q: str = Query(default="", max_length=200)
    ) -> list[SourceCreate]:
        terms = q.casefold().split()
        return [
            source
            for source in DEFAULT_SOURCES
            if all(
                term
                in f"{source.name} {source.config.get('url', '')} {source.config.get('profile', '')}".casefold()
                for term in terms
            )
        ]

    @app.get("/profiles", response_model=list[Profile])
    def profiles(identity: CurrentIdentity) -> list[Profile]:
        return store.list_profiles(identity.owner_id)

    @app.post("/profiles", response_model=Profile, status_code=201)
    def create_profile(body: ProfileCreate, identity: CurrentIdentity) -> Profile:
        return store.create_profile(body.name, identity.owner_id)

    @app.patch("/profiles/{name:path}", response_model=Profile)
    def rename_profile(
        name: str, body: ProfileCreate, identity: CurrentIdentity
    ) -> Profile:
        return store.rename_profile(name, body.name, identity.owner_id)

    @app.delete("/profiles/{name:path}", status_code=204)
    def delete_profile(name: str, identity: CurrentIdentity) -> None:
        store.delete_profile(name, identity.owner_id)

    @app.get("/sources", response_model=list[Source])
    def sources(identity: CurrentIdentity, enabled: bool | None = None) -> list[Source]:
        return store.list_sources(enabled=enabled, owner_id=identity.owner_id)

    @app.post("/sources/preview", response_model=SourcePreview)
    def preview(body: SourceCreate, identity: CurrentIdentity) -> SourcePreview:
        checked_config(body, identity, require_profile=False)
        window = parse_window(since="30d")
        now = utc_now()
        draft = Source(
            **body.model_dump(),
            id=0,
            owner_id=identity.owner_id,
            created_at=now,
            updated_at=now,
        )
        with fetch_slot():
            try:
                changes = manager.get(body.plugin).fetch(draft, window)
            except XUnavailable:
                raise HTTPException(
                    424, "X public timeline is unavailable or rate-limited"
                ) from None
            except (ValueError, PluginConfigError):
                raise HTTPException(
                    502, "Source preview failed; check the URL and configuration"
                ) from None
        return SourcePreview(window=window, changes=changes[:10])

    @app.post("/sources/discover", response_model=list[SourceCreate])
    def discover(
        body: SourceDiscoveryRequest, identity: CurrentIdentity
    ) -> list[SourceCreate]:
        try:
            url = normalize_discovery_url(body.url)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        with fetch_slot():
            try:
                return discover_sources(str(url))
            except PublicFetchError:
                raise HTTPException(
                    502, "Source discovery failed; check the link or retry later"
                ) from None

    @app.post("/sources", response_model=Source, status_code=201)
    def create_source(body: SourceCreate, identity: CurrentIdentity) -> Source:
        checked_config(body, identity)
        return store.add_source(body, owner_id=identity.owner_id)

    @app.get("/sources/{source_id}", response_model=Source)
    def source(source_id: int, identity: CurrentIdentity) -> Source:
        return store.get_source(source_id, owner_id=identity.owner_id)

    @app.patch("/sources/{source_id}", response_model=Source)
    def update_source(
        source_id: int, body: SourceUpdate, identity: CurrentIdentity
    ) -> Source:
        existing = store.get_source(source_id, owner_id=identity.owner_id)
        checked_config(
            SourceCreate.model_validate(
                {**existing.model_dump(), **body.model_dump(exclude_none=True)}
            ),
            identity,
        )
        return store.update_source(source_id, body, owner_id=identity.owner_id)

    @app.delete("/sources/{source_id}", status_code=204)
    def delete_source(source_id: int, identity: CurrentIdentity) -> None:
        store.delete_source(source_id, owner_id=identity.owner_id)

    @app.post("/sources/{source_id}/refresh", response_model=GenerationResult)
    def refresh_source(source_id: int, identity: CurrentIdentity) -> GenerationResult:
        source = store.get_source(source_id, owner_id=identity.owner_id)
        checked_config(source, identity)
        with fetch_slot():
            return generate_changes(
                parse_window(since="30d"),
                [source_id],
                200,
                plugin_manager=manager,
                owner_id=identity.owner_id,
            )

    @app.get("/changes", response_model=list[Change])
    def changes(
        identity: CurrentIdentity,
        since: str | None = None,
        until: str | None = None,
        source_id: Annotated[list[int] | None, Query()] = None,
        profile: str | None = None,
        include_dismissed: bool = False,
        saved: bool | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[Change]:
        try:
            window = (
                parse_window(since=since, until=until)
                if since is not None or until is not None
                else None
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        sources = store.list_sources(enabled=True, owner_id=identity.owner_id)
        selected = [
            source.id
            for source in sources
            if (profile is None or source.config["profile"] == profile)
            and (source_id is None or source.id in source_id)
        ]
        return store.list_changes(
            window=window,
            source_ids=selected,
            include_dismissed=include_dismissed,
            saved=saved,
            limit=limit,
            owner_id=identity.owner_id,
        )

    @app.post("/changes/restore", response_model=list[Change])
    def restore(body: RestoreRequest, identity: CurrentIdentity) -> list[Change]:
        return store.restore_changes(body.ids, identity.owner_id)

    @app.patch("/changes/{change_id}", response_model=Change)
    def update_change(
        change_id: int, body: ChangeUpdate, identity: CurrentIdentity
    ) -> Change:
        return store.update_change(change_id, body, owner_id=identity.owner_id)

    if _env_bool("CHANGELORG_MANUAL_GENERATE_API", False):

        @app.post("/changes/generate", response_model=GenerationResult)
        def generate(
            identity: CurrentIdentity,
            since: str = "7d",
            until: str | None = None,
            source_id: Annotated[list[int] | None, Query()] = None,
            limit: int = Query(default=100, ge=1, le=500),
        ) -> GenerationResult:
            try:
                window = parse_window(since=since, until=until)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
            with fetch_slot():
                return generate_changes(
                    window,
                    source_id,
                    limit,
                    plugin_manager=manager,
                    owner_id=identity.owner_id,
                )

    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if not frontend_dist.exists():
        frontend_dist = Path.cwd() / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
    return app


app = create_app()


def run() -> None:
    uvicorn.run(
        "changelorg.api:app", host="127.0.0.1", port=8000, reload=False, workers=1
    )
