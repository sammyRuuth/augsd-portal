"""Registration Timetable Pydantic schemas"""

import uuid
from datetime import datetime, time
from typing import Optional

from pydantic import BaseModel, Field


class RegistrationTimetableItemBase(BaseModel):
    """Base schema for registration timetable items"""

    course_section_id: uuid.UUID


class RegistrationTimetableItemCreate(RegistrationTimetableItemBase):
    """Schema for creating a registration timetable item"""

    pass


class RegistrationTimetableItemResponse(RegistrationTimetableItemBase):
    """Response schema for registration timetable items"""

    id: uuid.UUID
    timetable_id: uuid.UUID
    changed: bool = False
    deleted: bool = False
    added: bool = False
    changed_at: Optional[datetime] = None
    changed_by_id: Optional[uuid.UUID] = None
    change_note: Optional[str] = None

    # Include section details for convenience
    class_nbr: Optional[int] = None
    section: Optional[str] = None
    component: Optional[str] = None
    day: Optional[str] = None
    mtg_start: Optional[time] = None
    mtg_end: Optional[time] = None
    instructor: Optional[str] = None
    room: Optional[str] = None

    class Config:
        from_attributes = True


class RegistrationTimetableBase(BaseModel):
    """Base schema for registration timetables"""

    student_id: uuid.UUID


class RegistrationTimetableCreate(RegistrationTimetableBase):
    """Schema for creating a registration timetable"""

    pass


class RegistrationTimetableResponse(RegistrationTimetableBase):
    """Response schema for registration timetables"""

    id: uuid.UUID
    created_at: datetime
    created_by_id: uuid.UUID
    total_units: Optional[float] = None
    updated_at: Optional[datetime] = None
    updated_by_id: Optional[uuid.UUID] = None
    items: list[RegistrationTimetableItemResponse] = []

    class Config:
        from_attributes = True


class RegistrationTimetableUpdateRequest(BaseModel):
    """
    Request schema for updating a registration timetable via visual editor.

    Frontend sends the complete list of section_ids that should be in the timetable.
    Backend will compare with current state and update accordingly.
    """

    section_ids: list[uuid.UUID] = Field(
        ...,
        description="Complete list of course section IDs that should be in the timetable",
    )


class RegistrationTimetableUpdateResponse(BaseModel):
    """Response schema for registration timetable update"""

    success: bool
    message: str
    timetable: Optional[RegistrationTimetableResponse] = None
    changes: list[dict] = Field(
        default_factory=list,
        description="List of changes made (added/removed sections)",
    )
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class RegistrationTimetableWithCourseInfo(BaseModel):
    """
    Extended response with full course information for visual editor.

    Includes all timetable items with their course section details.
    """

    id: uuid.UUID
    student_id: uuid.UUID
    student_campus_id: str
    student_name: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    total_units: Optional[float] = None
    items: list[dict] = Field(
        default_factory=list,
        description="Timetable items with full course section and course details",
    )

    class Config:
        from_attributes = True
