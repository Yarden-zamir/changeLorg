import httpx

from test_api_identity import auth_client  # noqa: F401
from test_api_profiles import api_env  # noqa: F401


def test_identity_cache_still_checks_proxy_and_refreshes_expired_entries(auth_client):
    calls = []
    accepted = True

    def upstream(request):
        calls.append(request.url.host)
        if request.url.host == "oauth2-proxy":
            return httpx.Response(
                202 if accepted else 401,
                headers={"X-Auth-Request-Access-Token": "verified"},
            )
        return httpx.Response(200, json={"id": 123, "login": "user"})

    auth_client.cookies.set("_changelorg_oauth", "session")
    with httpx.Client(transport=httpx.MockTransport(upstream)) as client:
        auth_client.app.state.auth_client = client
        assert auth_client.get("/me").status_code == 200
        headers = {"X-Changelorg-Owner": "github:123"}
        for _ in range(3):
            assert auth_client.get("/profiles", headers=headers).status_code == 200
        assert calls.count("api.github.com") == 1
        assert calls.count("oauth2-proxy") == 4
        cache = auth_client.app.state.github_identity_cache
        for key, (_, user) in list(cache.items()):
            cache[key] = (0, user)
        assert auth_client.get("/profiles", headers=headers).status_code == 200
        assert calls.count("api.github.com") == 2
        accepted = False
        assert auth_client.get("/profiles", headers=headers).status_code == 401
