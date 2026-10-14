from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from changelorg import store
from changelorg.api import create_app
from changelorg.models import ChangeInput, ChangeUpdate, SourceCreate, SourceUpdate, utc_now


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CHANGELORG_DB", str(tmp_path / "changelorg.duckdb"))
    monkeypatch.setenv("CHANGELORG_AUTO_REFRESH", "false")
    monkeypatch.setenv("CHANGELORG_SEED_DEFAULT_SOURCES", "false")
    monkeypatch.setenv("CHANGELORG_AUTH_ENABLED", "false")
    monkeypatch.setenv("CHANGELORG_MANUAL_GENERATE_API", "false")
    monkeypatch.setenv("CHANGELORG_CORS_ORIGINS", "http://localhost:5173")
    monkeypatch.setenv("CHANGELORG_PUBLIC_ORIGIN", "https://changes.example.com")
    with TestClient(create_app()) as client:
        yield client


def owner_headers(client: TestClient) -> dict[str, str]:
    headers = {"X-Anonymous-Token": str(uuid4())}
    response = client.get("/me", headers=headers)
    assert response.status_code == 200, response.text
    return {**headers, "X-Changelorg-Owner": response.json()["id"], "X-Changelorg-Request": "1"}


def add_feed(owner: str, name: str, url: str, profile: str = "dev", **extra: object) -> int:
    source = store.add_source(
        SourceCreate(name=name, plugin="rss-atom", config={"profile": profile, "url": url, **extra}), owner_id=owner
    )
    return source.id


def share(client: TestClient, headers: dict[str, str], profile: str = "dev") -> str:
    response = client.post("/shares", headers=headers, json={"profile": profile})
    assert response.status_code == 200, response.text
    assert response.json()["profile"] == profile
    return response.json()["token"]


def test_share_link_follows_the_profile_until_it_stops(api_client: TestClient) -> None:
    headers = owner_headers(api_client)
    add_feed(headers["X-Changelorg-Owner"], "Releases", "https://example.com/releases.xml")

    token = share(api_client, headers)
    assert share(api_client, headers) == token, "a profile has one share link"
    assert api_client.get("/profiles", headers=headers).json() == [{"name": "dev", "source_count": 1, "share_token": token}]

    renamed = api_client.patch("/profiles/dev", headers=headers, json={"name": "tools"})
    assert renamed.json()["share_token"] == token
    assert api_client.get(f"/shares/{token}").json()["name"] == "tools"

    assert api_client.delete(f"/shares/{token}", headers=headers).status_code == 204
    assert api_client.get(f"/shares/{token}").status_code == 404
    assert api_client.get("/profiles", headers=headers).json()[0]["share_token"] is None


def test_profile_deletion_ends_its_share(api_client: TestClient) -> None:
    headers = owner_headers(api_client)
    add_feed(headers["X-Changelorg-Owner"], "Releases", "https://example.com/releases.xml")
    token = share(api_client, headers)
    assert api_client.delete("/profiles/dev", headers=headers).status_code == 204
    assert api_client.get(f"/shares/{token}").status_code == 404


def test_shared_profile_is_public_and_shows_only_enabled_sources_and_declared_settings(api_client: TestClient) -> None:
    headers = owner_headers(api_client)
    owner = headers["X-Changelorg-Owner"]
    add_feed(owner, "Releases", "https://example.com/releases.xml", include_any=["cli"], private_note="kept home")
    hidden = add_feed(owner, "Paused", "https://example.com/paused.xml")
    store.update_source(hidden, SourceUpdate(enabled=False), owner_id=owner)
    add_feed(owner, "Other desk", "https://example.com/games.xml", profile="games")
    token = share(api_client, headers)

    response = api_client.get(f"/shares/{token}")  # no identity at all
    assert response.status_code == 200
    assert response.json() == {
        "name": "dev",
        "shared_by": None,
        "sources": [
            {"name": "Releases", "plugin": "rss-atom", "config": {"url": "https://example.com/releases.xml", "include_any": ["cli"]}}
        ],
    }
    assert owner not in response.text
    assert api_client.get("/shares/unknown-token").status_code == 404


def test_import_copies_sources_and_recent_changes_without_the_sharers_state(api_client: TestClient) -> None:
    sharer = owner_headers(api_client)
    reader = owner_headers(api_client)
    source_id = add_feed(sharer["X-Changelorg-Owner"], "Releases", "https://example.com/releases.xml")
    store.upsert_changes(
        source_id,
        [
            ChangeInput(external_id="new", title="New release", published_at=utc_now() - timedelta(days=2)),
            ChangeInput(external_id="old", title="Old release", published_at=utc_now() - timedelta(days=40)),
        ],
        owner_id=sharer["X-Changelorg-Owner"],
    )
    recent = next(change for change in store.list_changes(source_ids=[source_id], owner_id=sharer["X-Changelorg-Owner"]) if change.external_id == "new")
    store.update_change(recent.id, ChangeUpdate(saved=True, dismissed=True, note="mine"), owner_id=sharer["X-Changelorg-Owner"])
    token = share(api_client, sharer)

    imported = api_client.post(f"/shares/{token}/import", headers=reader, json={"name": "from a friend"})
    assert imported.status_code == 200, imported.text
    assert imported.json() == {"profile": "from a friend", "sources": 1, "skipped": 0, "changes": 1}
    changes = api_client.get(
        "/changes", headers=reader, params={"profile": "from a friend", "include_dismissed": "true", "since": "60d"}
    ).json()
    assert [(change["title"], change["saved"], change["dismissed"], change["note"]) for change in changes] == [
        ("New release", False, False, "")
    ]
    sources = api_client.get("/sources", headers=reader).json()
    assert [(source["name"], source["config"]["profile"]) for source in sources] == [("Releases", "from a friend")]

    again = api_client.post(f"/shares/{token}/import", headers=reader, json={"name": "from a friend"})
    assert again.json() == {"profile": "from a friend", "sources": 0, "skipped": 1, "changes": 0}


def test_import_needs_an_identity_and_a_known_share(api_client: TestClient) -> None:
    sharer = owner_headers(api_client)
    reader = owner_headers(api_client)
    add_feed(sharer["X-Changelorg-Owner"], "Releases", "https://example.com/releases.xml")
    token = share(api_client, sharer)

    no_identity = api_client.post(f"/shares/{token}/import", headers={"X-Changelorg-Request": "1"}, json={"name": "dev"})
    assert no_identity.status_code == 401
    unknown = api_client.post("/shares/unknown-token/import", headers=reader, json={"name": "dev"})
    assert unknown.status_code == 404
    assert api_client.get("/profiles", headers=reader).json() == []


def test_import_is_all_or_nothing_at_the_source_limit(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    sharer = owner_headers(api_client)
    reader = owner_headers(api_client)
    add_feed(sharer["X-Changelorg-Owner"], "One", "https://example.com/one.xml")
    add_feed(sharer["X-Changelorg-Owner"], "Two", "https://example.com/two.xml")
    token = share(api_client, sharer)
    monkeypatch.setattr(store, "MAX_SOURCES", 1)

    response = api_client.post(f"/shares/{token}/import", headers=reader, json={"name": "dev"})
    assert response.status_code == 409
    assert api_client.get("/profiles", headers=reader).json() == []
    assert api_client.get("/sources", headers=reader).json() == []


def test_only_the_owner_can_stop_a_share(api_client: TestClient) -> None:
    sharer = owner_headers(api_client)
    other = owner_headers(api_client)
    add_feed(sharer["X-Changelorg-Owner"], "Releases", "https://example.com/releases.xml")
    token = share(api_client, sharer)

    assert api_client.delete(f"/shares/{token}", headers=other).status_code == 404
    assert api_client.get(f"/shares/{token}").status_code == 200
    assert api_client.post("/shares", headers=other, json={"profile": "dev"}).status_code == 404
