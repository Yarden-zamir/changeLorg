from datetime import datetime, timezone

from fastapi.testclient import TestClient

from changelorg.api import create_app
from changelorg.models import ChangeInput, SourceCreate
from changelorg.store import add_source, upsert_changes


def test_profiles_and_profile_filtered_changes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHANGELORG_DB", str(tmp_path / "changelorg.db"))
    monkeypatch.setenv("CHANGELORG_AUTO_REFRESH", "false")
    monkeypatch.setenv("CHANGELORG_SEED_DEFAULT_SOURCES", "false")
    app = create_app()

    with TestClient(app) as client:
        dev = add_source(SourceCreate(name="Dev Feed", config={"profile": "dev", "url": "https://example.com/dev.xml"}))
        games = add_source(SourceCreate(name="Games Feed", config={"profile": "games", "url": "https://example.com/games.xml"}))
        published_at = datetime(2026, 7, 4, 10, tzinfo=timezone.utc)
        upsert_changes(dev.id, [ChangeInput(external_id="dev", title="Dev item", published_at=published_at)])
        upsert_changes(games.id, [ChangeInput(external_id="games", title="Games item", published_at=published_at)])

        profiles_response = client.get("/profiles")
        games_response = client.get("/changes?profile=games&include_dismissed=true")

    assert profiles_response.status_code == 200
    assert profiles_response.json() == [
        {"name": "dev", "source_count": 1},
        {"name": "games", "source_count": 1},
    ]
    assert games_response.status_code == 200
    assert [change["title"] for change in games_response.json()] == ["Games item"]
    assert games_response.json()[0]["source_profile"] == "games"
