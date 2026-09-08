from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from changelorg import store
from changelorg.api import create_app
from changelorg.models import (
    Change,
    ChangeInput,
    ChangeUpdate,
    SourceCreate,
    TimeWindow,
    utc_now,
)
from changelorg.plugins.rss_atom import RssAtomPlugin


@pytest.fixture
def api_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHANGELORG_DB", str(tmp_path / "changelorg.duckdb"))
    monkeypatch.setenv("CHANGELORG_AUTO_REFRESH", "false")
    monkeypatch.setenv("CHANGELORG_SEED_DEFAULT_SOURCES", "false")
    monkeypatch.setenv("CHANGELORG_AUTH_ENABLED", "false")
    monkeypatch.setenv("CHANGELORG_MANUAL_GENERATE_API", "false")
    monkeypatch.setenv("CHANGELORG_CORS_ORIGINS", "http://localhost:5173")
    monkeypatch.setenv("CHANGELORG_PUBLIC_ORIGIN", "https://changes.example.com")
    monkeypatch.setenv("CHANGELORG_OAUTH2_PROXY_URL", "http://oauth2-proxy:4180")


@pytest.fixture
def api_client(api_env: None) -> Iterator[TestClient]:
    with TestClient(create_app()) as client:
        yield client


def owner_headers(client: TestClient, token: str | None = None) -> dict[str, str]:
    headers = {"X-Anonymous-Token": token or str(uuid4())}
    response = client.get("/me", headers=headers)
    assert response.status_code == 200, response.text
    return {
        **headers,
        "X-Changelorg-Owner": response.json()["id"],
        "X-Changelorg-Request": "1",
    }


def source_body(profile: str = "dev") -> dict:
    return {
        "name": "Release feed",
        "plugin": "rss-atom",
        "enabled": True,
        "config": {"profile": profile, "url": "https://example.com/releases.xml"},
    }


def seed_change(
    owner: str, profile: str = "dev", external_id: str = "release"
) -> Change:
    source = store.add_source(
        SourceCreate.model_validate(source_body(profile)), owner_id=owner
    )
    store.upsert_changes(
        source.id,
        [ChangeInput(external_id=external_id, title="Release", published_at=utc_now())],
        owner_id=owner,
    )
    return store.list_changes(source_ids=[source.id], owner_id=owner)[0]


def test_profiles_and_profile_filtered_changes(api_client: TestClient) -> None:
    headers = owner_headers(api_client)
    other = owner_headers(api_client)
    owner = headers["X-Changelorg-Owner"]
    dev = seed_change(owner)
    games = seed_change(owner, "games")
    seed_change(other["X-Changelorg-Owner"], "private")
    assert (
        api_client.post(
            "/profiles", headers=headers, json={"name": "empty"}
        ).status_code
        == 201
    )

    response = api_client.get("/profiles", headers=headers)
    assert response.status_code == 200
    assert {
        profile["name"]: profile["source_count"] for profile in response.json()
    } == {"dev": 1, "games": 1, "empty": 0}
    response = api_client.get(
        "/changes",
        headers=headers,
        params={"profile": "games", "include_dismissed": True},
    )
    assert response.status_code == 200
    assert [change["id"] for change in response.json()] == [games.id]
    assert response.json()[0]["source_profile"] == "games"
    response = api_client.get("/changes", headers=headers)
    assert {change["id"] for change in response.json()} == {dev.id, games.id}


def test_profile_rename_and_delete_only_affect_current_owner(
    api_client: TestClient,
) -> None:
    headers = owner_headers(api_client)
    other = owner_headers(api_client)
    owner = headers["X-Changelorg-Owner"]
    change = seed_change(owner, "same name")
    foreign = seed_change(other["X-Changelorg-Owner"], "same name")
    store.update_change(
        change.id, ChangeUpdate(saved=True, note="private note"), owner_id=owner
    )

    response = api_client.patch(
        "/profiles/same%20name", headers=headers, json={"name": "new name"}
    )
    assert response.status_code == 200
    assert (
        store.get_source(change.source_id, owner_id=owner).config["profile"]
        == "new name"
    )
    assert (
        store.get_source(
            foreign.source_id, owner_id=other["X-Changelorg-Owner"]
        ).config["profile"]
        == "same name"
    )
    assert (
        api_client.delete("/profiles/same%20name", headers=headers).status_code == 404
    )
    assert api_client.delete("/profiles/new%20name", headers=headers).status_code == 204
    assert store.list_sources(owner_id=owner) == []
    assert store.list_changes(owner_id=owner, include_dismissed=True) == []
    assert store.list_profiles(owner) == []
    assert store.get_change(foreign.id, owner_id=other["X-Changelorg-Owner"]) == foreign


def test_source_crud_ignores_frontend_owner_identity(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = owner_headers(api_client)
    other = owner_headers(api_client)
    owner = headers["X-Changelorg-Owner"]
    fetch = Mock(side_effect=AssertionError("Source CRUD must not fetch"))
    monkeypatch.setattr(RssAtomPlugin, "fetch", fetch)
    assert (
        api_client.post("/sources", headers=headers, json=source_body()).status_code
        == 422
    )
    assert (
        api_client.post(
            "/profiles",
            headers=headers,
            json={"name": "dev", "owner_id": other["X-Changelorg-Owner"]},
        ).status_code
        == 201
    )
    response = api_client.post(
        "/sources",
        headers=headers,
        json={**source_body(), "owner_id": other["X-Changelorg-Owner"]},
    )
    assert response.status_code == 201
    source = response.json()
    assert source["owner_id"] == owner
    assert (
        api_client.get(f"/sources/{source['id']}", headers=headers).json()["id"]
        == source["id"]
    )
    assert api_client.get("/sources", headers=other).json() == []
    assert api_client.get("/profiles", headers=other).json() == []

    response = api_client.patch(
        f"/sources/{source['id']}",
        headers=headers,
        json={"name": "Renamed", "owner_id": other["X-Changelorg-Owner"]},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"
    assert response.json()["owner_id"] == owner
    store.upsert_changes(
        source["id"],
        [ChangeInput(title="Cached", published_at=utc_now())],
        owner_id=owner,
    )
    assert (
        api_client.delete(f"/sources/{source['id']}", headers=headers).status_code
        == 204
    )
    assert (
        api_client.get(f"/sources/{source['id']}", headers=headers).status_code == 404
    )
    assert store.list_changes(owner_id=owner, include_dismissed=True) == []
    fetch.assert_not_called()


@pytest.mark.parametrize(
    "method,suffix,body",
    [
        ("GET", "", None),
        ("PATCH", "", {"name": "Stolen"}),
        ("DELETE", "", None),
        ("POST", "/refresh", None),
    ],
)
def test_foreign_source_access_is_not_found(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    suffix: str,
    body: dict | None,
) -> None:
    headers = owner_headers(api_client)
    other = owner_headers(api_client)
    foreign = seed_change(other["X-Changelorg-Owner"])
    before = store.get_source(foreign.source_id, owner_id=other["X-Changelorg-Owner"])
    fetch = Mock(side_effect=AssertionError("Foreign sources must not fetch"))
    monkeypatch.setattr(RssAtomPlugin, "fetch", fetch)
    response = api_client.request(
        method, f"/sources/{foreign.source_id}{suffix}", headers=headers, json=body
    )
    assert response.status_code == 404
    assert (
        store.get_source(foreign.source_id, owner_id=other["X-Changelorg-Owner"])
        == before
    )
    assert store.get_change(foreign.id, owner_id=other["X-Changelorg-Owner"]) == foreign
    assert (
        api_client.get(
            "/changes", headers=headers, params={"source_id": foreign.source_id}
        ).json()
        == []
    )
    fetch.assert_not_called()


def test_change_state_is_private_and_partial_updates_preserve_other_fields(
    api_client: TestClient,
) -> None:
    headers = owner_headers(api_client)
    other = owner_headers(api_client)
    owner = headers["X-Changelorg-Owner"]
    change = seed_change(owner)
    response = api_client.patch(
        f"/changes/{change.id}",
        headers=headers,
        json={"saved": True, "note": "Keep this"},
    )
    assert response.status_code == 200
    assert response.json()["state_updated_at"] is not None
    response = api_client.patch(
        f"/changes/{change.id}", headers=headers, json={"dismissed": True}
    )
    assert response.status_code == 200
    assert response.json()["saved"] is True
    assert response.json()["note"] == "Keep this"
    before = store.get_change(change.id, owner_id=owner)
    assert (
        api_client.patch(
            f"/changes/{change.id}",
            headers=other,
            json={"note": "Stolen", "owner_id": owner},
        ).status_code
        == 404
    )
    assert store.get_change(change.id, owner_id=owner) == before
    assert api_client.get("/changes", headers=headers).json() == []
    response = api_client.get(
        "/changes", headers=headers, params={"include_dismissed": True, "saved": True}
    )
    assert [item["id"] for item in response.json()] == [change.id]
    assert (
        api_client.get(
            "/changes", headers=other, params={"include_dismissed": True, "saved": True}
        ).json()
        == []
    )
    assert (
        api_client.patch(
            f"/changes/{change.id}", headers=headers, json={"note": "x" * 10001}
        ).status_code
        == 422
    )
    assert store.get_change(change.id, owner_id=owner) == before


@pytest.mark.parametrize("foreign_first", [False, True])
@pytest.mark.parametrize("missing", [False, True])
def test_restore_validates_every_owner_before_any_write(
    api_client: TestClient, foreign_first: bool, missing: bool
) -> None:
    headers = owner_headers(api_client)
    other = owner_headers(api_client)
    owner = headers["X-Changelorg-Owner"]
    change = seed_change(owner)
    foreign = seed_change(other["X-Changelorg-Owner"])
    before = store.update_change(
        change.id,
        ChangeUpdate(dismissed=True, saved=True, note="Retain"),
        owner_id=owner,
    )
    ids = [change.id, foreign.id + 1000 if missing else foreign.id]
    response = api_client.post(
        "/changes/restore",
        headers=headers,
        json={"ids": ids[::-1] if foreign_first else ids},
    )
    assert response.status_code == 404
    assert store.get_change(change.id, owner_id=owner) == before
    assert store.get_change(foreign.id, owner_id=other["X-Changelorg-Owner"]) == foreign
    response = api_client.post(
        "/changes/restore", headers=headers, json={"ids": [change.id]}
    )
    assert response.status_code == 200
    restored = response.json()
    assert [item["id"] for item in restored] == [change.id]
    assert restored[0]["dismissed"] is False
    assert restored[0]["saved"] is True
    assert restored[0]["note"] == "Retain"


def test_disabled_sources_exclude_feed_and_fetch_but_preserve_state(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = owner_headers(api_client)
    owner = headers["X-Changelorg-Owner"]
    change = seed_change(owner)
    before = store.update_change(
        change.id, ChangeUpdate(saved=True, note="Retain"), owner_id=owner
    )
    fetch = Mock(return_value=[])
    monkeypatch.setattr(RssAtomPlugin, "fetch", fetch)
    assert (
        api_client.patch(
            f"/sources/{change.source_id}", headers=headers, json={"enabled": False}
        ).status_code
        == 200
    )
    assert api_client.get("/sources", headers=headers).json()[0]["enabled"] is False
    assert (
        api_client.get("/sources", headers=headers, params={"enabled": True}).json()
        == []
    )
    assert api_client.get("/profiles", headers=headers).json()[0]["source_count"] == 0
    assert (
        api_client.get(
            "/changes",
            headers=headers,
            params={"include_dismissed": True, "source_id": change.source_id},
        ).json()
        == []
    )
    response = api_client.post(f"/sources/{change.source_id}/refresh", headers=headers)
    assert response.status_code == 200
    fetch.assert_not_called()
    assert store.get_change(change.id, owner_id=owner) == before
    assert response.json()["changes"] == []
    assert (
        api_client.patch(
            f"/sources/{change.source_id}", headers=headers, json={"enabled": True}
        ).status_code
        == 200
    )
    assert api_client.get("/changes", headers=headers).json()[0]["note"] == "Retain"


@pytest.mark.parametrize("count", [0, 12])
def test_preview_is_bounded_and_never_writes(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    headers = owner_headers(api_client)
    owner = headers["X-Changelorg-Owner"]
    items = [
        ChangeInput(
            external_id=str(index), title=f"Release {index}", published_at=utc_now()
        )
        for index in range(count)
    ]
    fetch = Mock(return_value=items)
    monkeypatch.setattr(RssAtomPlugin, "fetch", fetch)
    response = api_client.post(
        "/sources/preview", headers=headers, json=source_body("unsaved profile")
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["changes"]) == min(count, 10)
    if count:
        assert body["changes"][0]["external_id"] == items[0].external_id
    window = TimeWindow.model_validate(body["window"])
    assert window.end - window.start == timedelta(days=30)
    fetch.assert_called_once()
    draft, fetch_window = fetch.call_args.args
    assert draft.owner_id == owner
    assert draft.config == source_body("unsaved profile")["config"]
    assert fetch_window == window
    assert store.list_profiles(owner) == []
    assert store.list_sources(owner_id=None) == []
    assert store.list_changes(owner_id=None, include_dismissed=True) == []


@pytest.mark.parametrize(
    "url",
    [
        "not-a-url",
        "file:///etc/passwd",
        "http://127.0.0.1/feed",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/feed",
        "http://localhost/feed",
        "https://user:secret@example.com/feed",
    ],
)
@pytest.mark.parametrize("path", ["/sources/preview", "/sources"])
def test_invalid_and_ssrf_source_urls_fail_initial_validation(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch, url: str, path: str
) -> None:
    headers = owner_headers(api_client)
    owner = headers["X-Changelorg-Owner"]
    store.create_profile("dev", owner)
    body = source_body()
    body["config"]["url"] = url
    fetch = Mock(side_effect=AssertionError("Invalid URLs must not fetch"))
    monkeypatch.setattr(RssAtomPlugin, "fetch", fetch)
    assert api_client.post(path, headers=headers, json=body).status_code == 422
    fetch.assert_not_called()
    assert store.list_sources(owner_id=None) == []
    assert store.list_changes(owner_id=None, include_dismissed=True) == []
    assert [profile.name for profile in store.list_profiles(owner)] == ["dev"]


def test_preview_fetch_failure_does_not_write_or_expose_upstream_details(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = owner_headers(api_client)
    monkeypatch.setattr(
        RssAtomPlugin, "fetch", Mock(side_effect=ValueError("SECRET upstream response"))
    )
    response = api_client.post("/sources/preview", headers=headers, json=source_body())
    assert response.status_code == 502
    assert "SECRET" not in response.text
    assert store.list_profiles(headers["X-Changelorg-Owner"]) == []
    assert store.list_sources(owner_id=None) == []
    assert store.list_changes(owner_id=None, include_dismissed=True) == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"plugin": "untrusted-plugin"},
        {"config": {"url": "https://example.com/feed", "include_any": [123]}},
        {"plugin": "html-news", "config": {"url": "https://example.com/news"}},
        {"config": {"url": "http://127.0.0.1/feed", "profile": "dev"}},
    ],
)
def test_invalid_source_configs_fail_preview_create_and_update_without_writes(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch, overrides: dict
) -> None:
    headers = owner_headers(api_client)
    owner = headers["X-Changelorg-Owner"]
    change = seed_change(owner)
    before = store.get_source(change.source_id, owner_id=owner)
    fetch = Mock(side_effect=AssertionError("Invalid configs must not fetch"))
    monkeypatch.setattr(RssAtomPlugin, "fetch", fetch)
    for method, path in [
        ("POST", "/sources/preview"),
        ("POST", "/sources"),
        ("PATCH", f"/sources/{change.source_id}"),
    ]:
        response = api_client.request(
            method, path, headers=headers, json={**source_body(), **overrides}
        )
        assert response.status_code == 422
        assert store.list_sources(owner_id=None) == [before]
        assert store.get_change(change.id, owner_id=owner) == change
    fetch.assert_not_called()


def test_catalog_search_returns_drafts_without_account_writes(
    api_client: TestClient,
) -> None:
    headers = owner_headers(api_client)
    response = api_client.get("/catalog", headers=headers)
    assert response.status_code == 200
    catalog = response.json()
    assert catalog
    draft = SourceCreate.model_validate(catalog[0])
    response = api_client.get(
        "/catalog",
        headers=headers,
        params={"q": f"  {draft.name.upper()}   {draft.config['profile']}  "},
    )
    assert response.status_code == 200
    results = response.json()
    assert any(item["name"] == draft.name for item in results)
    assert all(
        draft.name.casefold()
        in f"{item['name']} {item['config']['url']} {item['config']['profile']}".casefold()
        for item in results
    )
    assert (
        api_client.get(
            "/catalog", headers=headers, params={"q": "no-match-" + str(uuid4())}
        ).json()
        == []
    )
    assert (
        api_client.get("/catalog", headers=headers, params={"q": "x" * 201}).status_code
        == 422
    )
    assert store.list_profiles(headers["X-Changelorg-Owner"]) == []
    assert store.list_sources(owner_id=None) == []


def test_browser_state_import_is_owner_scoped_and_preserves_server_state(
    api_client: TestClient,
) -> None:
    headers = owner_headers(api_client)
    other = owner_headers(api_client)
    owner = headers["X-Changelorg-Owner"]
    change = seed_change(owner)
    foreign = seed_change(other["X-Changelorg-Owner"])
    body = {
        "state": {
            f"{item.source_id}:{item.external_id}": {
                "saved": True,
                "note": "Browser note",
            }
            for item in [change, foreign]
        }
    }
    response = api_client.post("/me/import-state", headers=headers, json=body)
    assert response.status_code == 200
    assert response.json()["created"] == 1
    assert store.get_change(foreign.id, owner_id=other["X-Changelorg-Owner"]) == foreign
    assert store.get_change(change.id, owner_id=owner).note == "Browser note"
    before = store.update_change(
        change.id, ChangeUpdate(saved=False, note="Server note"), owner_id=owner
    )
    response = api_client.post("/me/import-state", headers=headers, json=body)
    assert response.status_code == 200
    assert response.json()["created"] == 0
    assert store.get_change(change.id, owner_id=owner) == before
