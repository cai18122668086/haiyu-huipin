from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

AnnouncementKind = Literal["info", "success", "warning", "maintenance"]
AnnouncementStatus = Literal["draft", "published", "archived"]


class AnnouncementCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    summary: str | None = Field(default=None, max_length=320)
    content: str = Field(min_length=1, max_length=20_000)
    image_asset_id: str | None = Field(default=None, max_length=40)
    image_alt: str | None = Field(default=None, max_length=240)
    kind: AnnouncementKind = "info"
    link_url: HttpUrl | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    is_pinned: bool = False

    @model_validator(mode="after")
    def validate_window(self) -> "AnnouncementCreate":
        if (
            self.starts_at is not None
            and self.ends_at is not None
            and self.ends_at <= self.starts_at
        ):
            raise ValueError("The end time must be later than the start time.")
        return self


class AnnouncementUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    summary: str | None = Field(default=None, max_length=320)
    content: str | None = Field(default=None, min_length=1, max_length=20_000)
    image_asset_id: str | None = Field(default=None, max_length=40)
    image_alt: str | None = Field(default=None, max_length=240)
    kind: AnnouncementKind | None = None
    link_url: HttpUrl | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    is_pinned: bool | None = None

    @model_validator(mode="after")
    def validate_window(self) -> "AnnouncementUpdate":
        if (
            self.starts_at is not None
            and self.ends_at is not None
            and self.ends_at <= self.starts_at
        ):
            raise ValueError("The end time must be later than the start time.")
        return self


class AnnouncementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    summary: str | None
    content: str
    image_asset_id: str | None
    image_alt: str | None
    image_url: str | None
    kind: AnnouncementKind
    status: AnnouncementStatus
    link_url: str | None
    starts_at: datetime | None
    ends_at: datetime | None
    is_pinned: bool
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AnnouncementListResponse(BaseModel):
    items: list[AnnouncementResponse]


class AnnouncementAdminAccessResponse(BaseModel):
    can_manage: bool
