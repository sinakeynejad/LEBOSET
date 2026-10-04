from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from leboset.domain.styling import Role, Template

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(utc)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ItemCreate(Input):
    name: Name
    role: Role
    colors: list[Name] = Field(min_length=1, max_length=5)


class ItemUpdate(ItemCreate):
    expected_version: int = Field(ge=1)


class VersionInput(Input):
    expected_version: int = Field(ge=1)


class AvailabilityInput(VersionInput):
    available: bool


class ItemView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    role: Role
    colors: list[str]
    confirmed: bool
    available: bool
    version: int


class ContextInput(Input):
    occasion: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    template: Template = Template.SEPARATES
    include_layer: bool = False
    required_item_ids: list[UUID] = Field(default_factory=list, max_length=4)
    excluded_item_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def check_duplicates(self):
        for values in (self.required_item_ids, self.excluded_item_ids):
            if len(set(values)) != len(values):
                raise ValueError("Item IDs must be unique within each constraint list")
        return self


class SwapInput(VersionInput):
    base_revision_id: UUID
    old_item_id: UUID
    new_item_id: UUID
    reason: Reason


class SelectionInput(VersionInput):
    revision_id: UUID


class RevisionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    number: int
    parent_id: UUID | None
    item_ids: list[UUID]
    item_snapshot: list[dict]
    change: dict | None
    created_at: UtcDatetime


class SessionView(BaseModel):
    id: UUID
    context: dict
    status: str
    version: int
    selected_revision_id: UUID | None
    selected_at: UtcDatetime | None
    revisions: list[RevisionView]
