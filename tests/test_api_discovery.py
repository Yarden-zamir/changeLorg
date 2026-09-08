from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from test_api_profiles import api_client as api_client  # noqa: PLC0414
from test_api_profiles import api_env as api_env  # noqa: PLC0414
from test_api_profiles import owner_headers, source_body
from test_network import Wire
from test_network import response as wire_response

from changelorg import api, store
from changelorg.models import ChangeInput, Source, SourceCreate
from changelorg.network import (
    SOURCE_FETCH_SLOTS,
    FetchBudgetExceeded,
    PublicFetchError,
    RequestBudgetExceeded,
    validate_public_url,
)
from changelorg.plugins.rss_atom import RssAtomPlugin


def assert_no_persistence(owner: str) -> None:
    assert store.list_profiles(owner) == []
    assert store.list_sources(owner_id=None) == []
    assert store.list_changes(owner_id=None, include_dismissed=True) == []


@pytest.mark.parametrize(
    "header,value,status",
    [
        ("X-Anonymous-Token", None, 401),
        ("X-Anonymous-Token", "invalid-token", 401),
        ("X-Changelorg-Owner", None, 412),
        ("X-Changelorg-Owner", "github:99999", 412),
        ("X-Changelorg-Request", None, 403),
        ("X-Changelorg-Request", "true", 403),
        ("Origin", "https://untrusted.example", 403),
        ("Origin", "null", 403),
    ],
)
def test_discovery_access_checks_precede_fetch(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    header: str,
    value: str | None,
    status: int,
) -> None:
    headers = owner_headers(api_client)
    owner = headers["X-Changelorg-Owner"]
    if value is None:
        del headers[header]
    else:
        headers[header] = value
    discover = Mock(side_effect=AssertionError("Rejected requests must not fetch"))
    monkeypatch.setattr(api, "discover_sources", discover)

    response = api_client.post(
        "/sources/discover", headers=headers, json={"url": "https://example.com"}
    )

    assert response.status_code == status, response.text
    assert "no-store" in response.headers["cache-control"]
    discover.assert_not_called()
    assert_no_persistence(owner)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"url": None},
        {"url": 123},
        {"url": ""},
        {"url": " "},
        {"url": "x" * 2001},
        {"url": "not-a-url"},
        {"url": "file:///etc/passwd"},
        {"url": "http://127.0.0.1/feed"},
        {"url": "https://SECRET:PRIVATE@example.com/feed"},
        {"url": "https://github.com/owner/repo/issues"},
    ],
)
def test_discovery_initial_validation_returns_422_without_fetch(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch, body: dict
) -> None:
    headers = owner_headers(api_client)
    discover = Mock(side_effect=AssertionError("Invalid input must not fetch"))
    monkeypatch.setattr(api, "discover_sources", discover)

    response = api_client.post("/sources/discover", headers=headers, json=body)

    assert response.status_code == 422, response.text
    assert "SECRET" not in response.text
    assert "PRIVATE" not in response.text
    discover.assert_not_called()
    assert_no_persistence(headers["X-Changelorg-Owner"])


@pytest.mark.parametrize("count", [0, 1, 10])
def test_discovery_returns_drafts_without_profiles_or_persistence(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    headers = owner_headers(api_client)
    drafts = [
        SourceCreate(
            name=f"Release feed {index}", config={"url": f"https://example.com/{index}"}
        )
        for index in range(count)
    ]
    discover = Mock(return_value=drafts)
    monkeypatch.setattr(api, "discover_sources", discover)
    assert_no_persistence(headers["X-Changelorg-Owner"])

    response = api_client.post(
        "/sources/discover", headers=headers, json={"url": "https://example.com"}
    )

    assert response.status_code == 200, response.text
    assert "no-store" in response.headers["cache-control"]
    results = response.json()
    assert isinstance(results, list)
    assert len(results) == count
    for result, draft in zip(results, drafts, strict=True):
        parsed = SourceCreate.model_validate(result)
        assert parsed.name == draft.name
        assert parsed.config["url"] == draft.config["url"]
        assert parsed.plugin == "rss-atom"
        assert parsed.enabled is True
        assert "profile" not in parsed.config
        assert "id" not in result
        assert "owner_id" not in result
    discover.assert_called_once()
    assert_no_persistence(headers["X-Changelorg-Owner"])


@pytest.mark.parametrize(
    "value", ["Any-owner/Any.repo", "  HTTPS://Example.COM/feed#top  "]
)
def test_discovery_forwards_normalized_public_url(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    headers = owner_headers(api_client)
    discover = Mock(return_value=[])
    monkeypatch.setattr(api, "discover_sources", discover)

    response = api_client.post(
        "/sources/discover", headers=headers, json={"url": value}
    )

    assert response.status_code == 200, response.text
    discover.assert_called_once()
    forwarded = discover.call_args.args[0]
    assert isinstance(forwarded, str)
    url = validate_public_url(forwarded)
    assert url.scheme == "https"
    assert not url.fragment
    assert not url.userinfo
    if value == "Any-owner/Any.repo":
        assert url.host == "github.com"
        assert url.path == "/Any-owner/Any.repo/releases.atom"
    else:
        assert url.host == "example.com"
        assert url.path == "/feed"
    assert_no_persistence(headers["X-Changelorg-Owner"])


@pytest.mark.parametrize(
    "error_type", [PublicFetchError, FetchBudgetExceeded, RequestBudgetExceeded]
)
def test_discovery_fetch_errors_return_generic_502_without_persistence(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[PublicFetchError],
) -> None:
    headers = owner_headers(api_client)
    discover = Mock(
        side_effect=error_type("SECRET upstream at http://10.0.0.1/PRIVATE")
    )
    monkeypatch.setattr(api, "discover_sources", discover)

    response = api_client.post(
        "/sources/discover", headers=headers, json={"url": "https://example.com"}
    )

    assert response.status_code == 502, response.text
    assert isinstance(response.json()["detail"], str)
    assert response.json()["detail"]
    for private_detail in ("SECRET", "10.0.0.1", "PRIVATE", error_type.__name__):
        assert private_detail not in response.text
    discover.assert_called_once()
    assert_no_persistence(headers["X-Changelorg-Owner"])


def test_discovery_shares_four_fetch_slots_and_releases_after_success_and_error(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = owner_headers(api_client)
    preview = Mock(return_value=[])
    monkeypatch.setattr(RssAtomPlugin, "fetch", preview)

    def discover(url: str) -> list[SourceCreate]:
        acquired = SOURCE_FETCH_SLOTS.acquire(blocking=False)
        if acquired:
            SOURCE_FETCH_SLOTS.release()
        assert not acquired, "Discovery must hold the fourth shared fetch slot"
        return []

    discovery = Mock(side_effect=discover)
    monkeypatch.setattr(api, "discover_sources", discovery)
    held = 0
    try:
        for _ in range(4):
            assert SOURCE_FETCH_SLOTS.acquire(blocking=False)
            held += 1
        response = api_client.post(
            "/sources/discover", headers=headers, json={"url": "https://example.com"}
        )
        assert response.status_code == 429, response.text
        assert "no-store" in response.headers["cache-control"]
        assert (
            api_client.post(
                "/sources/preview", headers=headers, json=source_body()
            ).status_code
            == 429
        )
        assert (
            api_client.post(
                "/sources/discover", headers=headers, json={"url": "file:///etc/passwd"}
            ).status_code
            == 422
        )
        discovery.assert_not_called()
        preview.assert_not_called()
        assert_no_persistence(headers["X-Changelorg-Owner"])

        SOURCE_FETCH_SLOTS.release()
        held -= 1
        for outcome, status in [
            (discover, 200),
            (PublicFetchError("SECRET upstream failure"), 502),
            (discover, 200),
        ]:
            discovery.side_effect = outcome
            response = api_client.post(
                "/sources/discover",
                headers=headers,
                json={"url": "https://example.com"},
            )
            assert response.status_code == status, response.text
            assert_no_persistence(headers["X-Changelorg-Owner"])
        assert discovery.call_count == 3
        assert (
            api_client.post(
                "/sources/preview", headers=headers, json=source_body()
            ).status_code
            == 200
        )
        preview.assert_called_once()
        assert SOURCE_FETCH_SLOTS.acquire(blocking=False), "Fetch slot leaked"
        held += 1
    finally:
        for _ in range(held):
            SOURCE_FETCH_SLOTS.release()


@pytest.mark.parametrize("html", [False, True], ids=["direct-rss", "html-alternate"])
def test_real_discovery_draft_previews_and_saves_only_with_explicit_profile(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch, html: bool
) -> None:
    headers = owner_headers(api_client)
    owner = headers["X-Changelorg-Owner"]
    feed = b"""<rss version='2.0'><channel><title>Example Releases</title>
    <item><guid>release-1</guid><title>Release one</title>
    <description>A new release</description></item></channel></rss>"""
    page = b"""<html><head><title>Example News</title>
    <link rel='alternate' type='application/rss+xml' href='/feed' title='Releases'>
    </head></html>"""
    with monkeypatch.context() as patch:
        wire = Wire(patch)
        wire.responses.append(wire_response(page if html else feed))
        response = api_client.post(
            "/sources/discover",
            headers=headers,
            json={
                "url": "http://example.com/news" if html else "http://example.com/feed"
            },
        )
        assert response.status_code == 200, response.text
        results = response.json()
        assert len(results) == 1
        draft = SourceCreate.model_validate(results[0])
        assert draft.name.strip()
        assert draft.plugin == "rss-atom"
        assert draft.enabled is True
        assert "profile" not in draft.config
        assert validate_public_url(draft.config["url"]) == httpx.URL(
            "http://example.com/feed"
        )
        assert len(wire.sockets) == 1
        assert_no_persistence(owner)

        wire.responses.append(wire_response(feed))
        response = api_client.post("/sources/preview", headers=headers, json=results[0])
        assert response.status_code == 200, response.text
        changes = [
            ChangeInput.model_validate(item) for item in response.json()["changes"]
        ]
        assert len(changes) == 1
        assert changes[0].external_id == "release-1"
        assert len(wire.sockets) == 2
        assert_no_persistence(owner)

        assert (
            api_client.post("/sources", headers=headers, json=results[0]).status_code
            == 422
        )
        draft.config["profile"] = "selected profile"
        assert (
            api_client.post(
                "/sources", headers=headers, json=draft.model_dump()
            ).status_code
            == 422
        )
        assert_no_persistence(owner)
        assert (
            api_client.post(
                "/profiles", headers=headers, json={"name": draft.config["profile"]}
            ).status_code
            == 201
        )
        response = api_client.post("/sources", headers=headers, json=draft.model_dump())
        assert response.status_code == 201, response.text
        saved = Source.model_validate(response.json())
        assert saved.owner_id == owner
        assert saved.config == draft.config
        assert saved.name == draft.name
        assert store.list_sources(owner_id=None) == [saved]
        profiles = store.list_profiles(owner)
        assert len(profiles) == 1
        assert profiles[0].name == draft.config["profile"]
        assert profiles[0].source_count == 1
        assert store.list_changes(owner_id=None, include_dismissed=True) == []
        assert len(wire.sockets) == 2
        assert not wire.responses
        assert all(sock.closed for sock in wire.sockets)


@pytest.mark.parametrize(
    "failure", ["private-dns", "private-redirect", "upstream-error"]
)
def test_real_discovery_fetch_rejections_are_safe_and_never_persist(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    headers = owner_headers(api_client)
    with monkeypatch.context() as patch:
        wire = Wire(patch)
        if failure == "private-dns":
            wire.answers["example.com"] = ["127.0.0.1"]
        elif failure == "private-redirect":
            wire.responses.append(
                wire_response(
                    status=302, headers=b"Location: http://127.0.0.1/SECRET\r\n"
                )
            )
        else:
            wire.responses.append(
                wire_response(b"SECRET upstream response", status=403)
            )

        response = api_client.post(
            "/sources/discover",
            headers=headers,
            json={"url": "http://example.com/feed"},
        )

        assert response.status_code == 502, response.text
        assert "SECRET" not in response.text
        assert "127.0.0.1" not in response.text
        assert len(wire.sockets) == (0 if failure == "private-dns" else 1)
        assert all(sock.closed for sock in wire.sockets)
        assert_no_persistence(headers["X-Changelorg-Owner"])
