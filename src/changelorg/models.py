from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

DEFAULT_OWNER_ID = "github:8178413"

# Agent access values: docs/user-data.md "Agent Access".
type AgentScope = Literal["read"]
AGENT_REQUEST_TTL = timedelta(minutes=10)
AGENT_POLL_INTERVAL_SECONDS = 5
AGENT_MAX_PENDING_REQUESTS = 100
AGENT_TOKEN_IDLE_TTL = timedelta(days=90)
AGENT_TOKEN_USE_PRECISION = timedelta(hours=1)
USER_CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"
USER_CODE_LENGTH = 8


def validate_owner_id(value: str) -> str:
    namespace, separator, identifier = value.partition(":")
    if separator and namespace == "github" and identifier.isascii() and identifier.isdecimal():
        return value
    if namespace == "anon" and len(identifier) == 64 and all(char in "0123456789abcdef" for char in identifier):
        return value
    raise ValueError("owner_id must be github:<numeric> or anon:<sha256>")


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class Profile(BaseModel):
    name: str
    source_count: int


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    plugin: str = Field(default="rss-atom", min_length=1, max_length=100)
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class SourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    plugin: str | None = Field(default=None, min_length=1, max_length=100)
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class Source(SourceCreate):
    owner_id: str = DEFAULT_OWNER_ID
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    _validate_owner = field_validator("owner_id")(validate_owner_id)

    @field_validator("created_at", "updated_at")
    @classmethod
    def source_dates_are_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class TimeWindow(BaseModel):
    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def window_dates_are_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def end_must_be_after_start(self) -> TimeWindow:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class ChangeInput(BaseModel):
    external_id: str | None = Field(default=None, max_length=500)
    title: str = Field(min_length=1, max_length=500)
    url: str | None = Field(default=None, max_length=2000)
    summary: str = ""
    content: str = ""
    published_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("published_at")
    @classmethod
    def published_at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class Change(ChangeInput):
    id: int
    source_id: int
    source_name: str
    source_profile: str = "dev"
    plugin: str
    fetched_at: datetime
    dismissed: bool = False
    saved: bool = False
    note: str = ""
    state_updated_at: datetime | None = None

    @field_validator("fetched_at", "state_updated_at")
    @classmethod
    def change_dates_are_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return ensure_utc(value)


class ChangeUpdate(BaseModel):
    dismissed: bool | None = None
    saved: bool | None = None
    note: str | None = Field(default=None, max_length=10000)


class PluginInfo(BaseModel):
    key: str
    name: str
    description: str = ""
    config_schema: dict[str, Any] = Field(default_factory=dict)


class GenerationError(BaseModel):
    source_id: int
    source_name: str
    plugin: str
    message: str


class GenerationResult(BaseModel):
    window: TimeWindow
    changes: list[Change]
    errors: list[GenerationError] = Field(default_factory=list)


def normalize_user_code(value: str) -> str | None:
    """Return the stored form of a user code, or None when the input cannot be a user code."""
    code = value.replace("-", "").replace(" ", "").upper()
    if len(code) != USER_CODE_LENGTH or any(char not in USER_CODE_ALPHABET for char in code):
        return None
    return code


def display_user_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


class AgentRequest(BaseModel):
    user_code: str
    client_name: str
    scope: AgentScope
    status: Literal["pending", "approved", "denied"]
    created_at: datetime
    expires_at: datetime


class AgentToken(BaseModel):
    id: int
    owner_id: str
    login: str
    name: str
    scope: AgentScope
    created_at: datetime
    last_used_at: datetime

    _validate_owner = field_validator("owner_id")(validate_owner_id)

    @field_validator("created_at", "last_used_at")
    @classmethod
    def token_dates_are_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @computed_field
    @property
    def expires_at(self) -> datetime:
        return self.last_used_at + AGENT_TOKEN_IDLE_TTL


class AgentPollError(StrEnum):
    """RFC 8628 token endpoint errors."""

    PENDING = "authorization_pending"
    SLOW_DOWN = "slow_down"
    DENIED = "access_denied"
    EXPIRED = "expired_token"
    INVALID = "invalid_grant"
