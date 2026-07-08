from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from changelorg.models import Change, ChangeUpdate, GenerationResult, Source, SourceCreate, SourceUpdate
from changelorg.plugin import default_plugin_manager
from changelorg.refresh import HourlyRefreshLoop
from changelorg.seed import seed_default_sources
from changelorg.service import generate_changes
from changelorg.store import add_source, delete_source, get_source, init_db, list_changes, list_sources, update_change, update_source
from changelorg.timeutils import parse_window


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def create_app() -> FastAPI:
    app = FastAPI(title="changelorg", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    async def startup() -> None:
        init_db()
        if _env_bool("CHANGELORG_SEED_DEFAULT_SOURCES", True):
            seed_default_sources()
        refresh_loop = HourlyRefreshLoop.from_env()
        app.state.refresh_loop = refresh_loop
        if HourlyRefreshLoop.enabled_from_env():
            refresh_loop.start()

    @app.on_event("shutdown")
    async def shutdown() -> None:
        refresh_loop = getattr(app.state, "refresh_loop", None)
        if refresh_loop is not None:
            await refresh_loop.stop()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/refresh/status")
    def refresh_status() -> dict[str, object]:
        refresh_loop = getattr(app.state, "refresh_loop", None)
        if refresh_loop is None:
            return {"enabled": False, "last_message": "refresh loop not initialized"}
        return {"enabled": HourlyRefreshLoop.enabled_from_env(), **refresh_loop.status.as_dict()}

    @app.get("/plugins")
    def plugins() -> list[dict[str, object]]:
        return [plugin.model_dump() for plugin in default_plugin_manager().list()]

    @app.get("/sources", response_model=list[Source])
    def get_sources(enabled: bool | None = None) -> list[Source]:
        return list_sources(enabled=enabled)

    @app.post("/sources", response_model=Source, status_code=201)
    def post_source(source: SourceCreate) -> Source:
        return add_source(source)

    @app.get("/sources/{source_id}", response_model=Source)
    def get_source_by_id(source_id: int) -> Source:
        try:
            return get_source(source_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.patch("/sources/{source_id}", response_model=Source)
    def patch_source(source_id: int, source: SourceUpdate) -> Source:
        try:
            return update_source(source_id, source)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/sources/{source_id}", status_code=204)
    def remove_source(source_id: int) -> None:
        try:
            delete_source(source_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    if _env_bool("CHANGELORG_MANUAL_GENERATE_API", False):
        @app.post("/changes/generate", response_model=GenerationResult)
        def post_generate_changes(
            since: Annotated[str | None, Query(description="ISO datetime/date or duration like 7d")] = "7d",
            until: Annotated[str | None, Query(description="ISO datetime/date; defaults to now")] = None,
            source_id: Annotated[list[int] | None, Query()] = None,
            limit: Annotated[int, Query(ge=1, le=500)] = 100,
        ) -> GenerationResult:
            try:
                window = parse_window(since=since, until=until)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return generate_changes(window=window, source_ids=source_id, limit=limit)

    @app.get("/changes")
    def get_changes(
        since: Annotated[str | None, Query(description="ISO datetime/date or duration like 7d")] = None,
        until: Annotated[str | None, Query(description="ISO datetime/date; defaults to now")] = None,
        source_id: Annotated[list[int] | None, Query()] = None,
        include_dismissed: bool = False,
        saved: bool | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[dict[str, object]]:
        window = None
        if since is not None or until is not None:
            try:
                window = parse_window(since=since, until=until)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return [
            change.model_dump(mode="json")
            for change in list_changes(
                window=window,
                source_ids=source_id,
                include_dismissed=include_dismissed,
                saved=saved,
                limit=limit,
            )
        ]

    @app.patch("/changes/{change_id}", response_model=Change)
    def patch_change(change_id: int, update: ChangeUpdate) -> Change:
        try:
            return update_change(change_id, update)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if not frontend_dist.exists():
        frontend_dist = Path.cwd() / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    return app


app = create_app()


def run() -> None:
    uvicorn.run("changelorg.api:app", host="127.0.0.1", port=8000, reload=False)
