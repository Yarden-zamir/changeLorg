"""Client side of agent access for the `auth` and `api` CLI commands: docs/spec.md "CLI Behavior"."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, ValidationError

from changelorg.config import credentials_path

DEFAULT_SERVER = "https://changelorg.yarden-zamir.com"
_MUTATION = {"X-Changelorg-Request": "1"}
_POLL_FAILURES = {
    "access_denied": "The request was denied in the browser.",
    "expired_token": "The approval request expired. Run changelorg auth login again.",
    "invalid_grant": "The server no longer knows this request. Run changelorg auth login again.",
}


class RemoteError(Exception):
    """A failure that the CLI reports before it exits non-zero."""


class DeviceCode(BaseModel):
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int = Field(gt=0)
    interval: int = Field(gt=0)


class TokenGrant(BaseModel):
    access_token: str
    owner_id: str
    login: str


class SavedToken(BaseModel):
    token: str
    owner_id: str
    login: str
    name: str
    saved_at: datetime


class Credentials(BaseModel):
    servers: dict[str, SavedToken] = Field(default_factory=dict)


def server_origin(value: str | None) -> str:
    raw = (value or os.environ.get("CHANGELORG_SERVER") or DEFAULT_SERVER).strip().rstrip("/")
    parts = urlsplit(raw)
    if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1"}):
        raise RemoteError("The server must use https, or http on localhost.")
    if not parts.hostname or parts.username or parts.password or parts.path or parts.query or parts.fragment:
        raise RemoteError(f"The server must be an origin such as {DEFAULT_SERVER}.")
    return f"{parts.scheme}://{parts.netloc}"


def client(origin: str) -> httpx.Client:
    return httpx.Client(base_url=origin, timeout=30, follow_redirects=False, headers={"User-Agent": "changelorg-cli"})


def load_credentials(path: Path | None = None) -> Credentials:
    path = path or credentials_path()
    try:
        text = path.read_text()
    except FileNotFoundError:
        return Credentials()
    try:
        return Credentials.model_validate_json(text)
    except ValidationError:
        raise RemoteError(f"{path} is not a valid credentials file. Delete it, then run changelorg auth login.") from None


def save_credentials(credentials: Credentials, path: Path | None = None) -> Path:
    path = path or credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(credentials.model_dump_json(indent=2))
    # A leftover temporary file can carry a wider mode from before.
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return path


def _failure(response: httpx.Response, action: str) -> RemoteError:
    try:
        body = response.json()
    except ValueError:
        body = None
    detail = body.get("detail") if isinstance(body, dict) else None
    reason = detail if isinstance(detail, str) else f"HTTP {response.status_code}"
    return RemoteError(f"Could not {action}: {reason}")


def start_login(http: httpx.Client, name: str) -> DeviceCode:
    response = http.post("/agent/device", json={"client_name": name}, headers=_MUTATION)
    if response.status_code != 201:
        raise _failure(response, "start the approval request")
    return DeviceCode.model_validate(response.json())


def wait_for_token(
    http: httpx.Client,
    device: DeviceCode,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> TokenGrant:
    interval = device.interval
    deadline = clock() + device.expires_in
    while clock() < deadline:
        sleep(interval)
        response = http.post("/agent/token", json={"device_code": device.device_code}, headers=_MUTATION)
        if response.status_code == 200:
            return TokenGrant.model_validate(response.json())
        if response.status_code != 400:
            raise _failure(response, "check the approval")
        try:
            body = response.json()
        except ValueError:
            body = None
        error = body.get("error") if isinstance(body, dict) else None
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        raise RemoteError(_POLL_FAILURES.get(str(error), f"The server rejected the approval request: {error!r}"))
    raise RemoteError(_POLL_FAILURES["expired_token"])


def get_json(http: httpx.Client, token: str, path: str) -> Any:
    if not path.startswith("/"):
        raise RemoteError("The path must start with /, for example /changes?since=10d.")
    response = http.get(path, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    if response.status_code == 401:
        raise RemoteError("The saved token is expired or revoked. Run changelorg auth login.")
    if not response.is_success:
        raise _failure(response, f"read {path}")
    try:
        return response.json()
    except ValueError:
        raise RemoteError(f"The server did not return JSON for {path}.") from None


def revoke(http: httpx.Client, token: str) -> None:
    response = http.delete("/agent/token", headers={"Authorization": f"Bearer {token}", **_MUTATION})
    # 401 means that the server already rejects the token.
    if response.status_code not in {204, 401}:
        raise _failure(response, "revoke the token")
