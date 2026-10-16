"""Agent access through the RFC 8628 device flow: docs/user-data.md "Agent Access"."""

from __future__ import annotations

import secrets
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator

from riffle import store
from riffle.auth import AGENT_TOKEN_PREFIX, Identity, agent_identity, github_browser_identity, hash_secret
from riffle.models import (
    AGENT_POLL_INTERVAL_SECONDS,
    AGENT_REQUEST_TTL,
    USER_CODE_ALPHABET,
    USER_CODE_LENGTH,
    AgentPollError,
    AgentRequest,
    AgentScope,
    AgentToken,
    normalize_user_code,
    utc_now,
)

BrowserIdentity = Annotated[Identity, Depends(github_browser_identity)]
AgentIdentity = Annotated[Identity, Depends(agent_identity)]

router = APIRouter(prefix="/agent")


class AgentDeviceStart(BaseModel):
    client_name: str = Field(min_length=1, max_length=100)
    scope: AgentScope = "read"

    @field_validator("client_name")
    @classmethod
    def printable_name(cls, value: str) -> str:
        value = value.strip()
        if not value or not value.isprintable():
            raise ValueError("client_name requires printable text")
        return value


class AgentDeviceCode(BaseModel):
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


class AgentTokenPoll(BaseModel):
    device_code: str = Field(min_length=1, max_length=200)


class AgentTokenGrant(BaseModel):
    access_token: str
    token_type: Literal["Bearer"]
    scope: AgentScope
    owner_id: str
    login: str


def _user_code(value: str) -> str:
    code = normalize_user_code(value)
    if code is None:
        raise HTTPException(404, "Unknown or expired code")
    return code


@router.post("/device", response_model=AgentDeviceCode, status_code=201)
def start_device(body: AgentDeviceStart, request: Request) -> AgentDeviceCode:
    device_code = secrets.token_urlsafe(32)
    created: AgentRequest | None = None
    # 20^8 codes make a collision rare; a few attempts cover it.
    for _ in range(5):
        user_code = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(USER_CODE_LENGTH))
        try:
            created = store.create_agent_request(hash_secret(device_code), user_code, body.client_name, body.scope, utc_now())
        except store.AgentCapacityError:
            raise HTTPException(429, "Too many agent requests wait for approval. Retry later.") from None
        if created is not None:
            break
    if created is None:
        raise HTTPException(503, "Could not allocate a user code. Retry.")
    origin = request.app.state.public_origin or str(request.base_url).rstrip("/")
    return AgentDeviceCode(
        device_code=device_code,
        user_code=created.user_code,
        verification_uri=f"{origin}/connect",
        verification_uri_complete=f"{origin}/connect?code={created.user_code}",
        expires_in=int(AGENT_REQUEST_TTL.total_seconds()),
        interval=AGENT_POLL_INTERVAL_SECONDS,
    )


@router.post("/token", response_model=AgentTokenGrant)
def poll_token(body: AgentTokenPoll) -> AgentTokenGrant | JSONResponse:
    if not body.device_code.isascii():
        return JSONResponse({"error": AgentPollError.INVALID.value}, status_code=400)
    access_token = AGENT_TOKEN_PREFIX + secrets.token_urlsafe(32)
    result = store.poll_agent_request(hash_secret(body.device_code), hash_secret(access_token), utc_now())
    if isinstance(result, AgentPollError):
        return JSONResponse({"error": result.value}, status_code=400)
    return AgentTokenGrant(
        access_token=access_token,
        token_type="Bearer",
        scope=result.scope,
        owner_id=result.owner_id,
        login=result.login,
    )


@router.delete("/token", status_code=204)
def revoke_current_token(identity: AgentIdentity) -> Response:
    assert identity.agent_token_id is not None
    store.delete_agent_token(identity.agent_token_id, identity.owner_id)
    return Response(status_code=204)


@router.get("/device/{user_code}", response_model=AgentRequest)
def show_device(user_code: str, identity: BrowserIdentity) -> AgentRequest:
    return store.get_agent_request(_user_code(user_code), identity.owner_id, utc_now())


def _decide(user_code: str, identity: Identity, approved: bool) -> AgentRequest:
    assert identity.login is not None
    try:
        return store.decide_agent_request(_user_code(user_code), identity.owner_id, identity.login, approved, utc_now())
    except ValueError:
        raise HTTPException(409, "This request already has a decision") from None


@router.post("/device/{user_code}/approve", response_model=AgentRequest)
def approve_device(user_code: str, identity: BrowserIdentity) -> AgentRequest:
    return _decide(user_code, identity, approved=True)


@router.post("/device/{user_code}/deny", response_model=AgentRequest)
def deny_device(user_code: str, identity: BrowserIdentity) -> AgentRequest:
    return _decide(user_code, identity, approved=False)


@router.get("/tokens", response_model=list[AgentToken])
def list_tokens(identity: BrowserIdentity) -> list[AgentToken]:
    return store.list_agent_tokens(identity.owner_id, utc_now())


@router.delete("/tokens/{token_id}", status_code=204)
def revoke_token(token_id: int, identity: BrowserIdentity) -> Response:
    store.delete_agent_token(token_id, identity.owner_id)
    return Response(status_code=204)
