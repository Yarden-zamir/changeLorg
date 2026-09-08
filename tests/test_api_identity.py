from collections.abc import Iterator
from uuid import uuid1, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from test_api_profiles import api_client as api_client  # noqa: PLC0414
from test_api_profiles import api_env as api_env  # noqa: PLC0414
from test_api_profiles import owner_headers, seed_change, source_body

from changelorg import store
from changelorg.api import create_app
from changelorg.models import ChangeUpdate


@pytest.fixture
def auth_client(api_env: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CHANGELORG_AUTH_ENABLED", "true")
    with TestClient(create_app()) as client:
        yield client


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("forged", [False, True])
def test_absent_or_forged_headers_never_authenticate_github(
    api_env: None, monkeypatch: pytest.MonkeyPatch, enabled: bool, forged: bool
) -> None:
    monkeypatch.setenv("CHANGELORG_AUTH_ENABLED", str(enabled).lower())
    requests = []

    def reject_network(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError("Frontend headers must not trigger auth")

    with (
        TestClient(create_app()) as client,
        httpx.Client(transport=httpx.MockTransport(reject_network)) as mocked,
    ):
        client.app.state.auth_client = mocked
        headers = (
            {
                "X-Auth-Request-User": "8178413",
                "X-Auth-Request-Email": "victim@example.com",
                "X-Auth-Request-Preferred-Username": "victim",
                "X-Auth-Request-Access-Token": "forged-token",
                "X-Forwarded-User": "victim",
                "Authorization": "Bearer forged-token",
            }
            if forged
            else {}
        )
        response = client.get("/me", headers=headers)
        assert response.status_code == 401
        assert (
            client.get(
                "/me", headers={**headers, "X-Changelorg-Owner": "github:8178413"}
            ).status_code
            == 401
        )
        headers["X-Anonymous-Token"] = str(uuid4())
        response = client.get("/me", headers=headers)
        assert response.status_code == 200
        identity = response.json()
        assert identity["id"].startswith("anon:")
        assert identity["authenticated"] is False
        assert identity["login"] is None
        assert identity["auth_enabled"] is enabled
        assert identity["anonymous_has_data"] is False
        assert (
            client.get(
                "/me", headers={**headers, "X-Changelorg-Owner": "github:8178413"}
            ).status_code
            == 412
        )
        assert requests == []


@pytest.mark.parametrize(
    "token",
    [
        "",
        "github:8178413",
        "not-a-uuid",
        str(uuid1()),
        uuid4().hex,
        "00000000-0000-0000-0000-000000000000",
    ],
)
def test_invalid_anonymous_identity_blocks_access(
    api_client: TestClient, token: str
) -> None:
    response = api_client.get("/me", headers={"X-Anonymous-Token": token})
    assert response.status_code == 401
    assert response.headers["cache-control"] == "private, no-store"


def test_anonymous_identity_is_stable_and_not_the_raw_token(
    api_client: TestClient,
) -> None:
    token = str(uuid4())
    first = owner_headers(api_client, token)
    assert (
        owner_headers(api_client, token.upper())["X-Changelorg-Owner"]
        == first["X-Changelorg-Owner"]
    )
    assert (
        owner_headers(api_client)["X-Changelorg-Owner"] != first["X-Changelorg-Owner"]
    )
    assert token not in first["X-Changelorg-Owner"]


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/profiles", None),
        ("GET", "/sources", None),
        ("GET", "/changes", None),
        ("POST", "/profiles", {"name": "Injected"}),
        ("PATCH", "/profiles/dev", {"name": "Injected"}),
        ("DELETE", "/profiles/dev", None),
        ("POST", "/sources", source_body()),
        ("PATCH", "/sources/{source_id}", {"enabled": False}),
        ("DELETE", "/sources/{source_id}", None),
        ("PATCH", "/changes/{change_id}", {"saved": True, "note": "Injected"}),
        ("POST", "/changes/restore", {"ids": []}),
        ("POST", "/me/import-state", {"state": {}}),
        ("POST", "/me/import", None),
    ],
)
@pytest.mark.parametrize("stale", [False, True])
def test_missing_or_stale_owner_precondition_prevents_access_and_writes(
    api_client: TestClient, method: str, path: str, body: dict | None, stale: bool
) -> None:
    headers = owner_headers(api_client)
    other = owner_headers(api_client)
    owner = headers["X-Changelorg-Owner"]
    change = seed_change(owner)
    foreign = seed_change(other["X-Changelorg-Owner"])
    sources = store.list_sources(owner_id=None)
    profiles = store.list_profiles(owner)
    if stale:
        headers["X-Changelorg-Owner"] = other["X-Changelorg-Owner"]
    else:
        del headers["X-Changelorg-Owner"]
    if path == "/changes/restore":
        body = {"ids": [change.id]}
    elif path == "/me/import-state":
        body = {"state": {f"{change.source_id}:{change.external_id}": {"saved": True}}}
    response = api_client.request(
        method,
        path.format(source_id=change.source_id, change_id=change.id),
        headers=headers,
        json=body,
    )
    assert response.status_code == 412
    assert response.headers["cache-control"] == "private, no-store"
    assert store.list_sources(owner_id=None) == sources
    assert store.list_profiles(owner) == profiles
    assert store.get_change(change.id, owner_id=owner) == change
    assert store.get_change(foreign.id, owner_id=other["X-Changelorg-Owner"]) == foreign


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST", "/profiles", {"name": "Injected"}),
        ("PATCH", "/profiles/dev", {"name": "Injected"}),
        ("DELETE", "/profiles/dev", None),
    ],
)
@pytest.mark.parametrize(
    "failure", ["missing-header", "wrong-header", "foreign-origin", "null-origin"]
)
def test_csrf_rejection_prevents_mutations(
    api_client: TestClient, method: str, path: str, body: dict | None, failure: str
) -> None:
    headers = owner_headers(api_client)
    owner = headers["X-Changelorg-Owner"]
    change = seed_change(owner)
    before = store.list_sources(owner_id=owner)
    if failure == "missing-header":
        del headers["X-Changelorg-Request"]
    elif failure == "wrong-header":
        headers["X-Changelorg-Request"] = "true"
    else:
        headers["Origin"] = (
            "null" if failure == "null-origin" else "https://evil.example"
        )
    response = api_client.request(method, path, headers=headers, json=body)
    assert response.status_code == 403
    assert "no-store" in response.headers["cache-control"]
    assert [profile.name for profile in store.list_profiles(owner)] == ["dev"]
    assert store.list_sources(owner_id=owner) == before
    assert store.get_change(change.id, owner_id=owner) == change


@pytest.mark.parametrize(
    "origin", ["http://localhost:5173", "https://changes.example.com"]
)
def test_explicit_origins_allow_credentials_and_custom_headers(
    api_client: TestClient, origin: str
) -> None:
    names = {
        "x-anonymous-token",
        "x-changelorg-owner",
        "x-changelorg-request",
        "content-type",
    }
    response = api_client.options(
        "/profiles",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": ", ".join(sorted(names)),
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"
    assert names <= {
        name.strip().lower()
        for name in response.headers["access-control-allow-headers"].split(",")
    }
    response = api_client.post(
        "/profiles",
        headers={**owner_headers(api_client), "Origin": origin},
        json={"name": "Allowed"},
    )
    assert response.status_code == 201
    assert response.headers["cache-control"] == "private, no-store"
    response = api_client.options(
        "/profiles",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    "path",
    [
        "/me",
        "/profiles",
        "/sources",
        "/changes",
        "/catalog",
        "/plugins",
        "/refresh/status",
        "/sources/999",
    ],
)
def test_private_responses_disable_caching(api_client: TestClient, path: str) -> None:
    response = api_client.get(path, headers=owner_headers(api_client))
    assert response.status_code == (404 if path == "/sources/999" else 200)
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("status", [403, 413])
def test_rejected_mutations_disable_private_response_caching(
    api_client: TestClient, status: int
) -> None:
    headers = owner_headers(api_client)
    body = {"name": "Rejected"}
    if status == 403:
        del headers["X-Changelorg-Request"]
    else:
        body["name"] = "x" * (2 * 1024 * 1024)
    response = api_client.post("/profiles", headers=headers, json=body)
    assert response.status_code == status
    assert store.list_profiles(headers["X-Changelorg-Owner"]) == []
    assert response.headers.get("cache-control") == "private, no-store"


@pytest.mark.parametrize("cookie", ["_changelorg_oauth", "_changelorg_oauth_0"])
def test_custom_session_cookie_requires_enabled_auth(
    api_client: TestClient, cookie: str
) -> None:
    headers = owner_headers(api_client)
    api_client.cookies.set(cookie, "session")
    assert (
        api_client.get(
            "/me", headers={"X-Anonymous-Token": headers["X-Anonymous-Token"]}
        ).status_code
        == 401
    )
    assert (
        api_client.post(
            "/profiles", headers=headers, json={"name": "No fallback"}
        ).status_code
        == 401
    )
    assert store.list_profiles(headers["X-Changelorg-Owner"]) == []


@pytest.mark.parametrize("cookie", ["_changelorg_oauth", "_changelorg_oauth_0"])
def test_proxy_token_and_github_numeric_id_define_stable_identity(
    auth_client: TestClient, cookie: str
) -> None:
    login = "original-login"
    requests = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        assert request.method == "GET"
        if str(request.url) == "http://oauth2-proxy:4180/auth/auth":
            assert request.headers["cookie"] == f"{cookie}=session"
            assert "authorization" not in request.headers
            return httpx.Response(
                202,
                headers={
                    "X-Auth-Request-Access-Token": "verified-token",
                    "X-Auth-Request-User": "wrong-user",
                },
            )
        assert str(request.url) == "https://api.github.com/user"
        assert request.headers["authorization"] == "Bearer verified-token"
        assert "cookie" not in request.headers
        return httpx.Response(200, json={"id": 12345, "login": login})

    anonymous = owner_headers(auth_client)
    auth_client.cookies.set(cookie, "session")
    with httpx.Client(transport=httpx.MockTransport(upstream)) as mocked:
        auth_client.app.state.auth_client = mocked
        headers = {
            "X-Anonymous-Token": anonymous["X-Anonymous-Token"],
            "X-Auth-Request-User": "victim",
            "Authorization": "Bearer attacker",
        }
        response = auth_client.get("/me", headers=headers)
        assert response.status_code == 200
        identity = response.json()
        assert identity["id"] == "github:12345"
        assert identity["authenticated"] is True
        assert identity["login"] == login
        assert identity["auth_enabled"] is True
        assert response.headers["cache-control"] == "private, no-store"
        login = "renamed-login"
        response = auth_client.get("/me", headers=headers)
        assert response.json()["id"] == identity["id"]
        assert response.json()["login"] == login
        assert (
            auth_client.post(
                "/profiles", headers=anonymous, json={"name": "Stale anonymous"}
            ).status_code
            == 412
        )
        current = {**anonymous, "X-Changelorg-Owner": identity["id"]}
        assert (
            auth_client.post(
                "/profiles",
                headers=current,
                json={"name": "Account profile", "owner_id": "github:99999"},
            ).status_code
            == 201
        )
        assert [profile.name for profile in store.list_profiles(identity["id"])] == [
            "Account profile"
        ]
        assert store.list_profiles("github:99999") == []
        assert store.list_profiles(anonymous["X-Changelorg-Owner"]) == []
        auth_client.cookies.clear()
        assert (
            auth_client.post(
                "/profiles", headers=current, json={"name": "Stale account"}
            ).status_code
            == 412
        )
        assert (
            owner_headers(auth_client, anonymous["X-Anonymous-Token"])[
                "X-Changelorg-Owner"
            ]
            == anonymous["X-Changelorg-Owner"]
        )
    assert requests.count("http://oauth2-proxy:4180/auth/auth") == 4
    assert requests.count("https://api.github.com/user") == 4
    assert mocked.is_closed


@pytest.mark.parametrize(
    "failure,status",
    [
        ("proxy-unauthorized", 401),
        ("proxy-error", 503),
        ("proxy-redirect", 503),
        ("proxy-no-token", 503),
        ("proxy-timeout", 503),
        ("github-unauthorized", 401),
        ("github-error", 503),
        ("github-timeout", 503),
        ("github-invalid-json", 503),
    ],
)
def test_auth_failures_fail_closed_without_anonymous_fallback(
    auth_client: TestClient, failure: str, status: int
) -> None:
    anonymous = owner_headers(auth_client)
    requests = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.host == "oauth2-proxy":
            if failure == "proxy-timeout":
                raise httpx.ReadTimeout("SECRET proxy", request=request)
            if failure.startswith("proxy-"):
                return httpx.Response(
                    {
                        "proxy-unauthorized": 401,
                        "proxy-error": 500,
                        "proxy-redirect": 302,
                        "proxy-no-token": 202,
                    }[failure]
                )
            return httpx.Response(
                202, headers={"X-Auth-Request-Access-Token": "SECRET token"}
            )
        assert str(request.url) == "https://api.github.com/user"
        if failure == "github-timeout":
            raise httpx.ConnectError("SECRET github", request=request)
        if failure == "github-invalid-json":
            return httpx.Response(200, text="SECRET invalid JSON")
        return httpx.Response(
            401 if failure == "github-unauthorized" else 500, text="SECRET error"
        )

    auth_client.cookies.set("_changelorg_oauth", "invalid-session")
    with httpx.Client(transport=httpx.MockTransport(upstream)) as mocked:
        auth_client.app.state.auth_client = mocked
        for method, path, body in [
            ("GET", "/me", None),
            ("POST", "/profiles", {"name": "No fallback"}),
        ]:
            response = auth_client.request(method, path, headers=anonymous, json=body)
            assert response.status_code == status
            assert "SECRET" not in response.text
            assert response.headers["cache-control"] == "private, no-store"
        assert store.list_profiles(anonymous["X-Changelorg-Owner"]) == []
    if failure.startswith("proxy-"):
        assert all(url == "http://oauth2-proxy:4180/auth/auth" for url in requests)


@pytest.mark.parametrize(
    "user",
    [
        {"login": "user"},
        {"id": None, "login": "user"},
        {"id": True, "login": "user"},
        {"id": "12345", "login": "user"},
        {"id": 1.5, "login": "user"},
        {"id": 0, "login": "user"},
        {"id": -1, "login": "user"},
        {"id": "../../victim", "login": "user"},
        {"id": 12345, "login": ""},
        {"id": 12345},
    ],
)
def test_github_identity_payload_requires_positive_integer_id(
    auth_client: TestClient, user: dict
) -> None:
    anonymous = owner_headers(auth_client)

    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2-proxy":
            return httpx.Response(202, headers={"X-Auth-Request-Access-Token": "token"})
        assert str(request.url) == "https://api.github.com/user"
        return httpx.Response(200, json=user)

    auth_client.cookies.set("_changelorg_oauth", "session")
    with httpx.Client(transport=httpx.MockTransport(upstream)) as mocked:
        auth_client.app.state.auth_client = mocked
        assert (
            auth_client.get(
                "/me", headers={"X-Anonymous-Token": anonymous["X-Anonymous-Token"]}
            ).status_code
            == 503
        )
        assert (
            auth_client.post(
                "/profiles", headers=anonymous, json={"name": "No fallback"}
            ).status_code
            == 503
        )
        assert store.list_profiles(anonymous["X-Changelorg-Owner"]) == []


def test_anonymous_import_requires_github_and_preserves_both_owners(
    auth_client: TestClient,
) -> None:
    anonymous = owner_headers(auth_client)
    other = owner_headers(auth_client)
    owner = anonymous["X-Changelorg-Owner"]
    change = seed_change(owner)
    before = store.update_change(
        change.id,
        ChangeUpdate(dismissed=True, saved=True, note="Anonymous note"),
        owner_id=owner,
    )
    foreign = seed_change(other["X-Changelorg-Owner"], "foreign")
    assert (
        auth_client.post(
            "/me/import", headers=anonymous, json={"owner_id": "github:12345"}
        ).status_code
        == 403
    )
    assert store.list_profiles("github:12345") == []

    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2-proxy":
            return httpx.Response(202, headers={"X-Auth-Request-Access-Token": "token"})
        assert str(request.url) == "https://api.github.com/user"
        return httpx.Response(200, json={"id": 12345, "login": "account"})

    auth_client.cookies.set("_changelorg_oauth", "session")
    with httpx.Client(transport=httpx.MockTransport(upstream)) as mocked:
        auth_client.app.state.auth_client = mocked
        current = owner_headers(auth_client, anonymous["X-Anonymous-Token"])
        response = auth_client.get("/me", headers=current)
        assert response.json()["anonymous_has_data"] is True
        assert store.list_sources(owner_id=current["X-Changelorg-Owner"]) == []
        assert (
            auth_client.post(
                "/me/import",
                headers={
                    "X-Changelorg-Owner": current["X-Changelorg-Owner"],
                    "X-Changelorg-Request": "1",
                },
            ).status_code
            == 403
        )
        response = auth_client.post(
            "/me/import",
            headers=current,
            json={
                "anonymous_owner": other["X-Changelorg-Owner"],
                "owner_id": "github:99999",
            },
        )
        assert response.status_code == 200
        assert response.json() == {"profiles": 1, "sources": 1, "changes": 1}
        imported = store.list_changes(
            owner_id=current["X-Changelorg-Owner"], include_dismissed=True
        )
        assert len(imported) == 1
        assert imported[0].source_id != change.source_id
        assert imported[0].external_id == change.external_id
        assert imported[0].dismissed is True
        assert imported[0].saved is True
        assert imported[0].note == "Anonymous note"
        account_state = store.update_change(
            imported[0].id,
            ChangeUpdate(note="Account note"),
            owner_id=current["X-Changelorg-Owner"],
        )
        response = auth_client.post("/me/import", headers=current)
        assert response.status_code == 200
        assert response.json() == {"profiles": 0, "sources": 0, "changes": 0}
        assert (
            store.get_change(imported[0].id, owner_id=current["X-Changelorg-Owner"])
            == account_state
        )
        assert store.get_change(change.id, owner_id=owner) == before
        assert (
            store.get_change(foreign.id, owner_id=other["X-Changelorg-Owner"])
            == foreign
        )
        assert store.list_profiles("github:99999") == []
        auth_client.cookies.clear()
        assert (
            owner_headers(auth_client, anonymous["X-Anonymous-Token"])[
                "X-Changelorg-Owner"
            ]
            == owner
        )
        response = auth_client.get(
            "/changes", headers=anonymous, params={"include_dismissed": True}
        )
        assert response.json()[0]["note"] == "Anonymous note"
