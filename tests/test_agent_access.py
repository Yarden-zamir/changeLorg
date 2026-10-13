from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import httpx
import pytest
from fastapi.testclient import TestClient
from test_api_profiles import api_env as api_env  # noqa: PLC0414
from test_api_profiles import owner_headers, seed_change

from changelorg import agent_access, auth, store
from changelorg.api import create_app
from changelorg.models import USER_CODE_ALPHABET

MUTATION = {"X-Changelorg-Request": "1"}


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 13, 8, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    value = Clock()
    monkeypatch.setattr(agent_access, "utc_now", value)
    monkeypatch.setattr(auth, "utc_now", value)
    return value


@pytest.fixture
def browser(api_env: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CHANGELORG_AUTH_ENABLED", "true")
    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def agent(browser: TestClient) -> TestClient:
    # A separate cookie jar: agents never send the browser session cookie.
    return TestClient(browser.app)


@contextmanager
def github_session(client: TestClient, user_id: int = 12345, login: str = "octo") -> Iterator[dict[str, str]]:
    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2-proxy":
            return httpx.Response(202, headers={"X-Auth-Request-Access-Token": f"token-{user_id}"})
        assert str(request.url) == "https://api.github.com/user"
        return httpx.Response(200, json={"id": user_id, "login": login})

    client.cookies.set("_changelorg_oauth", f"session-{user_id}")
    with httpx.Client(transport=httpx.MockTransport(upstream)) as mocked:
        client.app.state.auth_client = mocked
        try:
            yield {"X-Changelorg-Owner": f"github:{user_id}", **MUTATION}
        finally:
            client.cookies.clear()


def start(agent: TestClient, name: str = "Claude on test") -> dict:
    response = agent.post("/agent/device", json={"client_name": name}, headers=MUTATION)
    assert response.status_code == 201, response.text
    return response.json()


def poll(agent: TestClient, device: dict) -> httpx.Response:
    return agent.post("/agent/token", json={"device_code": device["device_code"]}, headers=MUTATION)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def approved_token(browser: TestClient, agent: TestClient, clock: Clock, user_id: int = 12345) -> str:
    device = start(agent)
    with github_session(browser, user_id) as headers:
        assert browser.post(f"/agent/device/{device['user_code']}/approve", headers=headers).status_code == 200
    clock.advance(seconds=5)
    response = poll(agent, device)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_device_flow_grants_a_read_only_token(browser: TestClient, agent: TestClient, clock: Clock) -> None:
    device = start(agent)
    code = device["user_code"]
    assert len(code) == 9 and code[4] == "-"
    assert all(char in USER_CODE_ALPHABET for char in code.replace("-", ""))
    assert device["verification_uri"] == "https://changes.example.com/connect"
    assert device["verification_uri_complete"] == f"https://changes.example.com/connect?code={code}"
    assert (device["expires_in"], device["interval"]) == (600, 5)
    assert poll(agent, device).json() == {"error": "authorization_pending"}

    with github_session(browser) as headers:
        shown = browser.get(f"/agent/device/{code.lower().replace('-', ' ')}", headers=headers)
        assert shown.status_code == 200
        assert shown.json()["client_name"] == "Claude on test"
        assert (shown.json()["status"], shown.json()["scope"]) == ("pending", "read")
        assert browser.post(f"/agent/device/{code}/approve", headers=headers).json()["status"] == "approved"
        assert browser.post(f"/agent/device/{code}/approve", headers=headers).status_code == 409
        assert browser.post(f"/agent/device/{code}/deny", headers=headers).status_code == 409

    clock.advance(seconds=5)
    granted = poll(agent, device)
    assert granted.status_code == 200
    grant = granted.json()
    token = grant["access_token"]
    assert token.startswith("clg_") and len(token) == 47
    assert (grant["token_type"], grant["scope"], grant["owner_id"], grant["login"]) == (
        "Bearer",
        "read",
        "github:12345",
        "octo",
    )
    clock.advance(seconds=5)
    assert poll(agent, device).json() == {"error": "invalid_grant"}

    change = seed_change("github:12345", "games")
    me = agent.get("/me", headers=bearer(token))
    assert me.status_code == 200
    assert (me.json()["id"], me.json()["authenticated"], me.json()["login"]) == ("github:12345", True, "octo")
    assert [profile["name"] for profile in agent.get("/profiles", headers=bearer(token)).json()] == ["games"]
    changes = agent.get("/changes?profile=games&since=10d", headers=bearer(token))
    assert [item["id"] for item in changes.json()] == [change.id]
    assert changes.headers["cache-control"] == "private, no-store"
    write = agent.post("/profiles", json={"name": "Injected"}, headers={**bearer(token), **MUTATION})
    assert write.status_code == 403
    assert [profile.name for profile in store.list_profiles("github:12345")] == ["games"]
    mismatch = agent.get("/profiles", headers={**bearer(token), "X-Changelorg-Owner": "github:1"})
    assert mismatch.status_code == 412


def test_storage_keeps_only_hashes(browser: TestClient, agent: TestClient, clock: Clock) -> None:
    device = start(agent)
    with github_session(browser) as headers:
        browser.post(f"/agent/device/{device['user_code']}/approve", headers=headers)
    pending_dump = _dump_agent_tables()
    assert device["device_code"] not in pending_dump
    clock.advance(seconds=5)
    token = poll(agent, device).json()["access_token"]
    dump = _dump_agent_tables()
    assert token not in dump
    assert auth.hash_secret(token) in dump


def _dump_agent_tables() -> str:
    path = store.init_db()
    with duckdb.connect(str(path)) as connection:
        rows = [*connection.execute("SELECT * FROM agent_requests").fetchall()]
        rows += connection.execute("SELECT * FROM agent_tokens").fetchall()
    return repr(rows)


def test_denied_expired_and_fast_polls(browser: TestClient, agent: TestClient, clock: Clock) -> None:
    denied = start(agent)
    with github_session(browser) as headers:
        assert browser.post(f"/agent/device/{denied['user_code']}/deny", headers=headers).json()["status"] == "denied"
    assert poll(agent, denied).json() == {"error": "access_denied"}
    assert poll(agent, denied).json() == {"error": "invalid_grant"}

    fast = start(agent)
    assert poll(agent, fast).json() == {"error": "authorization_pending"}
    clock.advance(seconds=2)
    assert poll(agent, fast).json() == {"error": "slow_down"}
    clock.advance(seconds=4.5)
    assert poll(agent, fast).json() == {"error": "authorization_pending"}

    clock.advance(minutes=10)
    with github_session(browser) as headers:
        assert browser.get(f"/agent/device/{fast['user_code']}", headers=headers).status_code == 404
        assert browser.post(f"/agent/device/{fast['user_code']}/approve", headers=headers).status_code == 404
        assert browser.get("/agent/device/not-a-code", headers=headers).status_code == 404
    assert poll(agent, fast).json() == {"error": "expired_token"}
    assert agent.post("/agent/token", json={"device_code": "unknown"}, headers=MUTATION).json() == {
        "error": "invalid_grant"
    }


def test_decided_request_is_visible_only_to_the_deciding_owner(
    browser: TestClient, agent: TestClient, clock: Clock
) -> None:
    device = start(agent)
    with github_session(browser, 1, "first") as headers:
        browser.post(f"/agent/device/{device['user_code']}/approve", headers=headers)
        assert browser.get(f"/agent/device/{device['user_code']}", headers=headers).json()["status"] == "approved"
    with github_session(browser, 2, "second") as headers:
        assert browser.get(f"/agent/device/{device['user_code']}", headers=headers).status_code == 404


def test_approval_needs_a_github_browser_session(browser: TestClient, agent: TestClient, clock: Clock) -> None:
    device = start(agent)
    code = device["user_code"]
    anonymous = owner_headers(browser)
    assert browser.get(f"/agent/device/{code}", headers=anonymous).status_code == 403
    assert browser.post(f"/agent/device/{code}/approve", headers=anonymous).status_code == 403
    assert browser.get("/agent/tokens", headers=anonymous).status_code == 403
    assert browser.post(f"/agent/device/{code}/approve", headers=MUTATION).status_code == 401

    token = approved_token(browser, agent, clock)
    other = start(agent)
    for method, path in [
        ("GET", f"/agent/device/{other['user_code']}"),
        ("POST", f"/agent/device/{other['user_code']}/approve"),
        ("GET", "/agent/tokens"),
        ("DELETE", "/agent/tokens/1"),
    ]:
        assert agent.request(method, path, headers={**bearer(token), **MUTATION}).status_code == 403
    clock.advance(seconds=5)
    assert poll(agent, other).json() == {"error": "authorization_pending"}


def test_agent_tokens_never_mix_with_browser_identities(browser: TestClient, agent: TestClient, clock: Clock) -> None:
    token = approved_token(browser, agent, clock)
    anonymous = owner_headers(browser)
    mixed = agent.get("/me", headers={**bearer(token), "X-Anonymous-Token": anonymous["X-Anonymous-Token"]})
    assert mixed.status_code == 400
    agent.cookies.set("_changelorg_oauth", "session")
    assert agent.get("/me", headers=bearer(token)).status_code == 400
    agent.cookies.clear()
    for value in ["clg_unknown", "clg_ünicode", "clg_" + "A" * 43]:
        # httpx sends only ASCII header text; bytes carry the non-ASCII case.
        response = agent.get("/me", headers={"Authorization": f"Bearer {value}".encode("latin-1")})
        assert response.status_code == 401
        assert "auth login" in response.json()["detail"]
    # Other Authorization values grant nothing and leave browser identity rules unchanged.
    assert agent.get("/me", headers={"Authorization": "Bearer ghp_example"}).status_code == 401
    assert agent.get("/me", headers={"Authorization": f"Basic {token}"}).status_code == 401


def test_owner_lists_and_revokes_tokens(browser: TestClient, agent: TestClient, clock: Clock) -> None:
    token = approved_token(browser, agent, clock)
    with github_session(browser) as headers:
        listed = browser.get("/agent/tokens", headers=headers).json()
    assert len(listed) == 1
    entry = listed[0]
    assert (entry["name"], entry["scope"]) == ("Claude on test", "read")
    assert datetime.fromisoformat(entry["expires_at"]) - datetime.fromisoformat(entry["last_used_at"]) == timedelta(
        days=90
    )
    assert "token_hash" not in entry and token not in repr(listed)
    with github_session(browser, 2, "other") as headers:
        assert browser.get("/agent/tokens", headers=headers).json() == []
        assert browser.delete(f"/agent/tokens/{entry['id']}", headers=headers).status_code == 404
    assert agent.get("/me", headers=bearer(token)).status_code == 200
    with github_session(browser) as headers:
        assert browser.delete(f"/agent/tokens/{entry['id']}", headers=headers).status_code == 204
    assert agent.get("/me", headers=bearer(token)).status_code == 401


def test_token_revokes_itself(browser: TestClient, agent: TestClient, clock: Clock) -> None:
    token = approved_token(browser, agent, clock)
    assert agent.delete("/agent/token", headers=bearer(token)).status_code == 403
    assert agent.delete("/agent/token", headers={**bearer(token), **MUTATION}).status_code == 204
    assert agent.get("/me", headers=bearer(token)).status_code == 401
    assert agent.delete("/agent/token", headers={**bearer(token), **MUTATION}).status_code == 401
    assert agent.delete("/agent/token", headers=MUTATION).status_code == 401


def test_tokens_expire_ninety_days_after_last_use(browser: TestClient, agent: TestClient, clock: Clock) -> None:
    token = approved_token(browser, agent, clock)
    created = clock.now

    def last_used() -> datetime:
        with github_session(browser) as headers:
            return datetime.fromisoformat(browser.get("/agent/tokens", headers=headers).json()[0]["last_used_at"])

    clock.advance(minutes=30)
    assert agent.get("/me", headers=bearer(token)).status_code == 200
    assert last_used() == created
    clock.advance(minutes=31)
    assert agent.get("/me", headers=bearer(token)).status_code == 200
    assert last_used() == clock.now
    clock.advance(days=89, hours=23)
    assert agent.get("/me", headers=bearer(token)).status_code == 200
    clock.advance(days=90)
    assert agent.get("/me", headers=bearer(token)).status_code == 401
    with github_session(browser) as headers:
        assert browser.get("/agent/tokens", headers=headers).json() == []


def test_pending_requests_have_a_global_limit(
    agent: TestClient, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "AGENT_MAX_PENDING_REQUESTS", 2)
    start(agent)
    start(agent)
    assert agent.post("/agent/device", json={"client_name": "third"}, headers=MUTATION).status_code == 429
    clock.advance(minutes=10, seconds=1)
    start(agent)


@pytest.mark.parametrize("name", ["", "   ", "line\nbreak", "x" * 101])
def test_device_start_validates_the_client_name(agent: TestClient, name: str) -> None:
    assert agent.post("/agent/device", json={"client_name": name}, headers=MUTATION).status_code == 422


def test_device_start_requires_the_mutation_header(agent: TestClient) -> None:
    assert agent.post("/agent/device", json={"client_name": "agent"}).status_code == 403


def test_connect_path_serves_the_frontend(api_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Without a built frontend next to the package, the app falls back to ./frontend/dist.
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=root></div>")
    monkeypatch.chdir(tmp_path)
    with TestClient(create_app()) as client:
        page = client.get("/connect?code=BCDF-GHJK")
        assert page.status_code == 200
        assert page.headers["content-type"].startswith("text/html")
        assert page.text == client.get("/").text


def test_write_tokens_change_data_but_never_manage_access(browser: TestClient, agent: TestClient, clock: Clock) -> None:
    device = agent.post("/agent/device", json={"client_name": "writer", "scope": "write"}, headers=MUTATION).json()
    with github_session(browser) as headers:
        shown = browser.get(f"/agent/device/{device['user_code']}", headers=headers).json()
        assert shown["scope"] == "write"
        browser.post(f"/agent/device/{device['user_code']}/approve", headers=headers)
    clock.advance(seconds=5)
    grant = poll(agent, device).json()
    assert grant["scope"] == "write"
    write = {**bearer(grant["access_token"]), **MUTATION}

    assert agent.post("/profiles", json={"name": "agent made"}, headers=write).status_code == 201
    change = seed_change("github:12345", "games")
    saved = agent.patch(f"/changes/{change.id}", json={"saved": True, "note": "from agent"}, headers=write)
    assert (saved.status_code, saved.json()["saved"], saved.json()["note"]) == (200, True, "from agent")
    assert agent.delete("/profiles/agent%20made", headers=write).status_code in {200, 204}
    assert [profile.name for profile in store.list_profiles("github:12345")] == ["games"]
    # Mutations still need the request header, like every other client.
    assert agent.post("/profiles", json={"name": "x"}, headers=bearer(grant["access_token"])).status_code == 403
    for method, path, body in [
        ("POST", "/me/import", None),
        ("POST", "/me/import-state", {"state": {}}),
        ("GET", "/agent/tokens", None),
        ("POST", f"/agent/device/{start(agent)['user_code']}/approve", None),
    ]:
        assert agent.request(method, path, json=body, headers=write).status_code == 403

    read_token = approved_token(browser, agent, clock)
    denied = agent.patch(f"/changes/{change.id}", json={"saved": False}, headers={**bearer(read_token), **MUTATION})
    assert denied.status_code == 403
    assert "--write" in denied.json()["detail"]
    with github_session(browser) as headers:
        scopes = {token["name"]: token["scope"] for token in browser.get("/agent/tokens", headers=headers).json()}
    assert scopes == {"writer": "write", "Claude on test": "read"}


def test_device_start_rejects_unknown_scopes(agent: TestClient) -> None:
    for scope in ["admin", "", "READ"]:
        response = agent.post("/agent/device", json={"client_name": "agent", "scope": scope}, headers=MUTATION)
        assert response.status_code == 422
