import asyncio
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Event
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from test_api_profiles import api_client as api_client  # noqa: PLC0414
from test_api_profiles import api_env as api_env  # noqa: PLC0414
from test_api_profiles import owner_headers, seed_change, source_body

from changelorg import api, refresh, service, store
from changelorg.api import create_app
from changelorg.models import ChangeInput, ChangeUpdate, Source, TimeWindow
from changelorg.network import SOURCE_FETCH_SLOTS
from changelorg.plugin import PluginManager
from changelorg.plugins.rss_atom import RssAtomPlugin
from changelorg.refresh import HourlyRefreshLoop


@pytest.mark.parametrize(
    "environment,origin",
    [
        ("prod", "https://changelorg.yarden-zamir.com"),
        ("pr-123", "https://pr-123.changelorg.yarden-zamir.com"),
    ],
)
@pytest.mark.parametrize("configuration", ["unset", "blank"])
def test_deployment_origin_allows_only_matching_browser_mutations(
    api_env: None,
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
    origin: str,
    configuration: str,
) -> None:
    monkeypatch.setenv("KITSHN_ENVIRONMENT", environment)
    for name in ("CHANGELORG_PUBLIC_ORIGIN", "CHANGELORG_CORS_ORIGINS"):
        if configuration == "blank":
            monkeypatch.setenv(name, "")
        else:
            monkeypatch.delenv(name, raising=False)
    with TestClient(create_app(), base_url=origin) as client:
        headers = owner_headers(client)
        for rejected in ("https://unrelated.example", "http://localhost:5173"):
            response = client.post(
                "/profiles",
                headers={**headers, "Origin": rejected},
                json={"name": "rejected"},
            )
            assert response.status_code == 403
            assert response.headers["cache-control"] == "private, no-store"
            assert "access-control-allow-origin" not in response.headers
            assert store.list_profiles(headers["X-Changelorg-Owner"]) == []

        response = client.post(
            "/profiles", headers={**headers, "Origin": origin}, json={"name": "allowed"}
        )
        assert response.status_code == 201, response.text
        assert response.headers["access-control-allow-origin"] == origin
        assert response.headers["access-control-allow-credentials"] == "true"
        assert response.headers["cache-control"] == "private, no-store"
        assert [p.name for p in store.list_profiles(headers["X-Changelorg-Owner"])] == [
            "allowed"
        ]


@pytest.mark.parametrize("configuration", ["unset", "blank"])
def test_unknown_deployment_requires_explicit_public_origin(
    api_env: None, monkeypatch: pytest.MonkeyPatch, configuration: str
) -> None:
    monkeypatch.setenv("KITSHN_ENVIRONMENT", "custom-deployment")
    monkeypatch.setenv("CHANGELORG_CORS_ORIGINS", "")
    if configuration == "blank":
        monkeypatch.setenv("CHANGELORG_PUBLIC_ORIGIN", "")
    else:
        monkeypatch.delenv("CHANGELORG_PUBLIC_ORIGIN", raising=False)
    with pytest.raises(ValueError) as failure, TestClient(create_app()):
        pass
    assert "CHANGELORG_PUBLIC_ORIGIN" in str(failure.value)


@pytest.mark.parametrize("auth_enabled", [False, True])
@pytest.mark.parametrize(
    "session_names",
    [
        ("_changelorg_oauth",),
        ("_changelorg_oauth_0", "_changelorg_oauth_1"),
        ("_changelorg_oauth", "_changelorg_oauth_0", "_changelorg_oauth_1"),
    ],
    ids=["base", "chunks", "base-and-chunks"],
)
def test_session_reset_clears_secure_cookies_without_auth_and_preserves_anonymous_data(
    api_env: None,
    monkeypatch: pytest.MonkeyPatch,
    auth_enabled: bool,
    session_names: tuple[str, ...],
) -> None:
    monkeypatch.setenv("CHANGELORG_AUTH_ENABLED", str(auth_enabled).lower())
    auth = Mock(return_value=httpx.Response(401))
    with (
        TestClient(create_app(), base_url="https://changes.example.com") as client,
        httpx.Client(transport=httpx.MockTransport(auth)) as auth_client,
    ):
        client.app.state.auth_client = auth_client
        token = str(uuid4())
        headers = owner_headers(client, token)
        owner = headers["X-Changelorg-Owner"]
        change = seed_change(owner)
        before = store.update_change(
            change.id,
            ChangeUpdate(saved=True, dismissed=True, note="Retain this note"),
            owner_id=owner,
        )
        profiles = store.list_profiles(owner)
        sources = store.list_sources(owner_id=owner)
        cookies = (
            *session_names,
            "_changelorg_oauth_csrf",
            "unrelated",
            "_changelorg_oauth2",
        )
        client.cookies.extract_cookies(
            httpx.Response(
                200,
                headers=[
                    (
                        "Set-Cookie",
                        f"{name}=stale; Path=/; Secure; HttpOnly; SameSite=Lax",
                    )
                    for name in cookies
                ],
                request=httpx.Request("GET", str(client.base_url)),
            )
        )
        response = client.get("/me", headers={"X-Anonymous-Token": token})
        assert response.status_code == 401
        assert all(
            f"{name}=stale" in response.request.headers["cookie"] for name in cookies
        )
        if auth_enabled:
            auth.assert_called_once()
        else:
            auth.assert_not_called()
        auth.reset_mock()

        response = client.post(
            "/session/reset",
            headers={
                "X-Changelorg-Request": "1",
                "Origin": str(client.base_url).rstrip("/"),
            },
        )
        assert response.status_code == 204, response.text
        assert response.content == b""
        assert response.headers["cache-control"] == "private, no-store"
        assert "X-Anonymous-Token" not in response.request.headers
        assert "X-Changelorg-Owner" not in response.request.headers
        assert dict(client.cookies) == {
            "unrelated": "stale",
            "_changelorg_oauth2": "stale",
        }
        auth.assert_not_called()

        response = client.get("/me", headers={"X-Anonymous-Token": token})
        assert response.status_code == 200, response.text
        identity = response.json()
        assert identity["id"] == owner
        assert identity["authenticated"] is False
        assert identity["login"] is None
        assert identity["anonymous_has_data"] is True
        assert identity["auth_enabled"] is auth_enabled
        assert store.list_profiles(owner) == profiles
        assert store.list_sources(owner_id=owner) == sources
        assert store.get_change(change.id, owner_id=owner) == before
        response = client.get(
            "/changes",
            headers=owner_headers(client, token),
            params={"include_dismissed": True},
        )
        assert response.status_code == 200
        assert [item["id"] for item in response.json()] == [change.id]
        assert response.json()[0]["note"] == before.note
        auth.assert_not_called()


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Changelorg-Request": "0"},
        {"X-Changelorg-Request": "1", "Origin": "https://unrelated.example"},
    ],
    ids=["missing-csrf", "wrong-csrf", "foreign-origin"],
)
def test_session_reset_rejects_unsafe_requests_without_cookie_changes(
    api_env: None, headers: dict[str, str]
) -> None:
    with TestClient(create_app(), base_url="https://changes.example.com") as client:
        client.cookies.set("_changelorg_oauth", "stale")
        response = client.post("/session/reset", headers=headers)
        assert response.status_code == 403
        assert response.headers["cache-control"] == "private, no-store"
        assert "set-cookie" not in response.headers
        assert client.cookies.get("_changelorg_oauth") == "stale"


@pytest.mark.parametrize("streamed", [False, True], ids=["content-length", "chunked"])
@pytest.mark.parametrize(
    "size,status",
    [(128, 201), (2 * 1024 * 1024, 201), (2 * 1024 * 1024 + 1, 413)],
    ids=["small", "maximum", "oversized"],
)
def test_request_body_limit_precedes_writes_and_accepts_valid_chunks(
    api_client: TestClient, streamed: bool, size: int, status: int
) -> None:
    headers = {**owner_headers(api_client), "Content-Type": "application/json"}
    owner = headers["X-Changelorg-Owner"]
    change = seed_change(owner)
    profiles = store.list_profiles(owner)
    sources = store.list_sources(owner_id=owner)
    payload = b'{"name":"new profile"}'
    payload += b" " * (size - len(payload))
    consumed: list[int] = []

    async def chunks() -> AsyncIterator[bytes]:
        chunk_size = min(64 * 1024, size // 2)
        for offset in range(0, len(payload), chunk_size):
            chunk = payload[offset : offset + chunk_size]
            consumed.append(len(chunk))
            yield chunk

    async def send() -> httpx.Response:
        # ASGITransport preserves body chunks; TestClient can combine them before the app receives them.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api_client.app),
            base_url="https://changes.example.com",
        ) as client:
            return await client.post(
                "/profiles", headers=headers, content=chunks() if streamed else payload
            )

    response = asyncio.run(send())
    if streamed:
        assert "content-length" not in response.request.headers
        assert response.request.headers["transfer-encoding"] == "chunked"
        assert len(consumed) > 1
        assert sum(consumed) == size
    else:
        assert response.request.headers["content-length"] == str(size)
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "private, no-store"
    if status == 413:
        assert store.list_profiles(owner) == profiles
    else:
        assert {p.name for p in store.list_profiles(owner)} == {"dev", "new profile"}
    assert store.list_sources(owner_id=owner) == sources
    assert store.get_change(change.id, owner_id=owner) == change


def test_preview_and_scheduler_share_fetch_capacity_and_release_it(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert api.SOURCE_FETCH_SLOTS is refresh.SOURCE_FETCH_SLOTS is SOURCE_FETCH_SLOTS
    headers = owner_headers(api_client)
    owner = headers["X-Changelorg-Owner"]
    change = seed_change(owner)
    acquire_started = Event()
    fetch_started = Event()
    finish_fetch = Event()
    enter = BoundedSemaphore.__enter__

    def observed_enter(semaphore: BoundedSemaphore) -> bool:
        if semaphore is SOURCE_FETCH_SLOTS:
            acquire_started.set()
        return enter(semaphore)

    def fetch(source: Source, window: TimeWindow) -> list[ChangeInput]:
        if source.id != 0:
            assert source.id == change.source_id
            assert source.owner_id == owner
            fetch_started.set()
            assert finish_fetch.wait(timeout=5), (
                "Scheduler fetch did not receive its release signal"
            )
        return [
            ChangeInput(
                external_id="release",
                title="Refreshed",
                published_at=window.start + (window.end - window.start) / 2,
            )
        ]

    fetch_mock = Mock(side_effect=fetch)
    monkeypatch.setattr(BoundedSemaphore, "__enter__", observed_enter)
    monkeypatch.setattr(RssAtomPlugin, "fetch", fetch_mock)
    monkeypatch.setattr(
        service,
        "default_plugin_manager",
        Mock(return_value=PluginManager([RssAtomPlugin()])),
    )
    loop = HourlyRefreshLoop(
        interval_seconds=3600, window="30d", initial_delay_seconds=0
    )
    held = 0
    with ThreadPoolExecutor(max_workers=1) as executor:
        try:
            for _ in range(4):
                assert SOURCE_FETCH_SLOTS.acquire(blocking=False)
                held += 1
            task = executor.submit(asyncio.run, loop.refresh_once())
            assert acquire_started.wait(timeout=5), (
                "Scheduler did not request the shared semaphore"
            )
            assert not task.done()
            fetch_mock.assert_not_called()
            response = api_client.post(
                "/sources/preview", headers=headers, json=source_body()
            )
            assert response.status_code == 429
            assert response.headers["cache-control"] == "private, no-store"
            fetch_mock.assert_not_called()

            SOURCE_FETCH_SLOTS.release()
            held -= 1
            assert fetch_started.wait(timeout=5), (
                "Scheduler did not resume after a slot became available"
            )
            response = api_client.post(
                "/sources/preview", headers=headers, json=source_body()
            )
            assert response.status_code == 429
            assert fetch_mock.call_count == 1

            finish_fetch.set()
            task.result(timeout=5)
            assert loop.status.running is False
            assert loop.status.last_errors == []
            assert loop.status.last_change_count == 1
            assert store.get_change(change.id, owner_id=owner).title == "Refreshed"

            response = api_client.post(
                "/sources/preview", headers=headers, json=source_body()
            )
            assert response.status_code == 200, response.text
            assert response.json()["changes"][0]["external_id"] == "release"
            assert fetch_mock.call_count == 2
            assert len(store.list_sources(owner_id=owner)) == 1
            assert len(store.list_changes(owner_id=owner)) == 1
            assert SOURCE_FETCH_SLOTS.acquire(blocking=False), (
                "Preview leaked its fetch slot"
            )
            held += 1
        finally:
            finish_fetch.set()
            for _ in range(held):
                SOURCE_FETCH_SLOTS.release()
