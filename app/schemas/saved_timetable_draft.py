"""Saved Timetable Draft Pydantic schemas"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SavedDraftCreate(BaseModel):
    """Schema for creating a saved timetable draft"""

    name: str = Field(..., min_length=1, max_length=255)
    notes: str | None = None
    selected_course_ids: list[uuid.UUID] = Field(..., min_length=1)
    pinned_sections: dict[str, dict[str, int | None]] = Field(
        default_factory=dict,
        description="Course ID -> {component -> class_nbr}",
    )
    seat_preferences: dict[str, bool] = Field(
        default_factory=dict,
        description="Seat preferences like prefer_lab_seats, etc.",
    )


class SavedDraftUpdate(BaseModel):
    """Schema for updating a saved timetable draft"""

    name: str | None = Field(None, min_length=1, max_length=255)
    notes: str | None = None
    selected_course_ids: list[uuid.UUID] | None = None
    pinned_sections: dict[str, dict[str, int | None]] | None = None
    seat_preferences: dict[str, bool] | None = None


class SavedDraftResponse(BaseModel):
    """Schema for saved timetable draft response"""

    id: uuid.UUID
    student_id: uuid.UUID
    name: str
    notes: str | None = None
    selected_course_ids: list[str]  # UUIDs as strings in JSONB
    pinned_sections: dict[str, dict[str, int | None]]
    seat_preferences: dict[str, bool]
    created_at: datetime
    updated_at: datetime | None = None
    created_by_id: uuid.UUID

    class Config:
        from_attributes = True


class SavedDraftListResponse(BaseModel):
    """Schema for listing saved drafts"""

    drafts: list[SavedDraftResponse]
    total: int
