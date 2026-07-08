from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

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
