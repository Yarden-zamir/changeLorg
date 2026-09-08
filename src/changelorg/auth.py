from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

import httpx
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, ValidationError


class GitHubUser(BaseModel):
    id: int = Field(strict=True, gt=0)
    login: str = Field(min_length=1, max_length=100)


@dataclass(frozen=True)
class Identity:
    owner_id: str
    login: str | None
    anonymous_owner: str | None

    @property
    def authenticated(self) -> bool:
        return self.login is not None


def anonymous_owner(token: str | None) -> str | None:
    if token is None:
        return None
    try:
        parsed = UUID(token)
        if parsed.version != 4 or str(parsed) != token.lower():
            raise ValueError
    except ValueError:
        raise HTTPException(401, "Invalid browser identity") from None
    return "anon:" + hashlib.sha256(str(parsed).encode("ascii")).hexdigest()


def request_identity(request: Request) -> Identity:
    anonymous = anonymous_owner(request.headers.get("X-Anonymous-Token"))
    login = None
    owner = anonymous
    # Proxy cookies can span numbered chunks. A rejected login never falls back to anonymous writes.
    has_session = any(
        name == "_changelorg_oauth"
        or name.startswith("_changelorg_oauth_")
        and name.removeprefix("_changelorg_oauth_").isdecimal()
        for name in request.cookies
    )
    if has_session:
        if not request.app.state.auth_enabled:
            raise HTTPException(401, "GitHub authentication is disabled")
        client: httpx.Client = request.app.state.auth_client
        try:
            response = client.get(
                f"{request.app.state.oauth2_proxy_url}/auth/auth",
                headers={"Cookie": request.headers.get("cookie", "")},
            )
            if response.status_code == 401:
                raise HTTPException(401, "GitHub session expired; sign in again")
            if response.status_code != 202:
                raise HTTPException(503, "Authentication service unavailable")
            token = response.headers.get("X-Auth-Request-Access-Token")
            if not token:
                raise HTTPException(
                    503, "Authentication service did not provide an identity token"
                )
            # Use the authenticated endpoint, not a username lookup. GitHub usernames can change owners.
            github = client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            if github.status_code == 401:
                raise HTTPException(401, "GitHub session expired; sign in again")
            if github.status_code != 200:
                raise HTTPException(503, "GitHub identity service unavailable")
            user = GitHubUser.model_validate(github.json())
        except (httpx.HTTPError, ValidationError, ValueError):
            raise HTTPException(503, "Could not verify GitHub identity") from None
        owner, login = f"github:{user.id}", user.login
    if owner is None:
        raise HTTPException(401, "A browser identity or GitHub session is required")
    expected = request.headers.get("X-Changelorg-Owner")
    if expected is not None and expected != owner:
        raise HTTPException(412, "The active account changed")
    if request.url.path != "/me" and expected is None:
        raise HTTPException(412, "Resolve the account through /me before access")
    return Identity(owner, login, anonymous)
