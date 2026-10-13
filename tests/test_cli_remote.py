import json
import stat
from contextlib import nullcontext
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_agent_access import Clock, github_session
from test_agent_access import agent as agent  # noqa: PLC0414
from test_agent_access import browser as browser  # noqa: PLC0414
from test_agent_access import clock as clock  # noqa: PLC0414
from test_api_profiles import api_env as api_env  # noqa: PLC0414
from typer.testing import CliRunner

from changelorg import remote
from changelorg.cli import app

SERVER = "https://changes.example.com"


@pytest.mark.parametrize(
    "value,origin",
    [
        (None, remote.DEFAULT_SERVER),
        ("https://changes.example.com/", "https://changes.example.com"),
        ("http://localhost:8000", "http://localhost:8000"),
        ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
    ],
)
def test_server_origin_accepts_origins(value: str | None, origin: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHANGELORG_SERVER", raising=False)
    assert remote.server_origin(value) == origin


@pytest.mark.parametrize(
    "value",
    ["http://changes.example.com", "https://changes.example.com/path", "https://user:pass@changes.example.com", "ftp://x"],
)
def test_server_origin_rejects_other_values(value: str) -> None:
    with pytest.raises(remote.RemoteError):
        remote.server_origin(value)


def test_credentials_file_is_private(tmp_path: Path) -> None:
    path = tmp_path / "config" / "credentials.json"
    assert remote.load_credentials(path) == remote.Credentials()
    saved = remote.SavedToken(
        token="clg_x", scope="read", owner_id="github:1", login="me", name="cli", saved_at="2026-10-13T08:00:00Z"
    )
    remote.save_credentials(remote.Credentials(servers={SERVER: saved}), path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert remote.load_credentials(path).servers[SERVER] == saved
    path.write_text("not json")
    with pytest.raises(remote.RemoteError):
        remote.load_credentials(path)


@pytest.fixture
def cli(
    browser: TestClient, agent: TestClient, clock: Clock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict:
    monkeypatch.setenv("CHANGELORG_CREDENTIALS", str(tmp_path / "credentials.json"))

    def client(origin: str):
        assert origin == SERVER
        return nullcontext(agent)

    monkeypatch.setattr(remote, "client", client)
    decision = {"approve": True}
    real_wait = remote.wait_for_token

    def wait(http, device):
        polls = []

        def sleep(seconds: float) -> None:
            polls.append(seconds)
            clock.advance(seconds=seconds)
            if len(polls) == 2:
                with github_session(browser) as headers:
                    action = "approve" if decision["approve"] else "deny"
                    response = browser.post(f"/agent/device/{device.user_code}/{action}", headers=headers)
                    assert response.status_code == 200

        return real_wait(http, device, sleep=sleep, clock=lambda: len(polls))

    monkeypatch.setattr(remote, "wait_for_token", wait)
    return {"decision": decision, "path": tmp_path / "credentials.json", "agent": agent}


def test_login_status_api_and_logout(cli: dict) -> None:
    runner = CliRunner()
    login = runner.invoke(app, ["auth", "login", "--server", SERVER, "--name", "Test agent"])
    assert login.exit_code == 0, login.output
    assert f"{SERVER}/connect?code=" in login.output
    assert "Approved for octo (github:12345)" in login.output
    stored = json.loads(cli["path"].read_text())["servers"][SERVER]
    assert stored["token"].startswith("clg_") and stored["token"] not in login.output
    assert stored["name"] == "Test agent"
    assert stat.S_IMODE(cli["path"].stat().st_mode) == 0o600

    status = runner.invoke(app, ["auth", "status", "--server", SERVER])
    assert status.exit_code == 0, status.output
    assert "as octo (github:12345) with read only access" in status.output

    profiles = runner.invoke(app, ["api", "/profiles", "--server", SERVER])
    assert profiles.exit_code == 0, profiles.output
    assert json.loads(profiles.output) == []
    assert runner.invoke(app, ["api", "profiles", "--server", SERVER]).exit_code == 1

    logout = runner.invoke(app, ["auth", "logout", "--server", SERVER])
    assert logout.exit_code == 0, logout.output
    assert SERVER not in json.loads(cli["path"].read_text())["servers"]
    assert cli["agent"].get("/me", headers={"Authorization": f"Bearer {stored['token']}"}).status_code == 401
    after = runner.invoke(app, ["auth", "status", "--server", SERVER])
    assert after.exit_code == 1
    assert "Not signed in" in after.output


def test_login_reports_a_denied_request(cli: dict) -> None:
    cli["decision"]["approve"] = False
    result = CliRunner().invoke(app, ["auth", "login", "--server", SERVER])
    assert result.exit_code == 1
    assert "denied" in result.output
    assert not cli["path"].exists()


def test_status_reports_a_revoked_token(cli: dict, browser: TestClient) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["auth", "login", "--server", SERVER]).exit_code == 0
    with github_session(browser) as headers:
        token_id = browser.get("/agent/tokens", headers=headers).json()[0]["id"]
        assert browser.delete(f"/agent/tokens/{token_id}", headers=headers).status_code == 204
    result = runner.invoke(app, ["auth", "status", "--server", SERVER])
    assert result.exit_code == 1
    assert "expired or revoked" in result.output


def test_write_login_and_api_methods(cli: dict) -> None:
    runner = CliRunner()
    login = runner.invoke(app, ["auth", "login", "--server", SERVER, "--write"])
    assert login.exit_code == 0, login.output
    assert "read and write access" in login.output
    assert json.loads(cli["path"].read_text())["servers"][SERVER]["scope"] == "write"

    created = runner.invoke(app, ["api", "-X", "POST", "/profiles", "-d", '{"name": "from cli"}', "--server", SERVER])
    assert created.exit_code == 0, created.output
    assert json.loads(created.output)["name"] == "from cli"
    deleted = runner.invoke(app, ["api", "-X", "DELETE", "/profiles/from%20cli", "--server", SERVER])
    assert deleted.exit_code == 0, deleted.output
    assert runner.invoke(app, ["api", "-X", "PUT", "/profiles", "--server", SERVER]).exit_code == 1
    bad_json = runner.invoke(app, ["api", "-X", "POST", "/profiles", "-d", "{name", "--server", SERVER])
    assert bad_json.exit_code == 1
    assert "valid JSON" in bad_json.output


def test_read_token_reports_write_refusal(cli: dict) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["auth", "login", "--server", SERVER]).exit_code == 0
    refused = runner.invoke(app, ["api", "-X", "POST", "/profiles", "-d", '{"name": "x"}', "--server", SERVER])
    assert refused.exit_code == 1
    assert "--write" in refused.output
