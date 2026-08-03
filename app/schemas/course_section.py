"""Course Section Pydantic schemas"""

import uuid
from datetime import date, time

from pydantic import BaseModel, Field


class CourseSectionBase(BaseModel):
    """Base course section schema"""

    class_nbr: int = Field(..., gt=0)
    section: str = Field(..., min_length=1)
    component: str = Field(..., min_length=1)
    day: str | None = Field(
        default=None
    )  # Can be empty for sections without class pattern
    class_pattern: str | None = None
    mtg_start: time | None = None
    mtg_end: time | None = None
    exam_date: date | None = None
    exam_start: time | None = None
    exam_end: time | None = None
    instructor: str | None = None
    room: str | None = None
    cap_enrl: int | None = Field(None, ge=0)


class CourseSectionCreate(CourseSectionBase):
    """Schema for creating a new course section"""

    course_id: uuid.UUID
    tot_enrl: int = Field(default=0, ge=0)  # Initial enrollment from timetable Excel


class CourseSectionUpdate(BaseModel):
    """Schema for updating a course section"""

    cap_enrl: int | None = Field(None, ge=0)
    tot_enrl: int | None = Field(None, ge=0)


class CourseSectionResponse(CourseSectionBase):
    """Schema for course section response"""

    id: uuid.UUID
    course_id: uuid.UUID
    tot_enrl: int

    class Config:
        from_attributes = True


class CourseSectionWithDetails(CourseSectionResponse):
    """Schema for course section with full course details"""

    course: "CourseResponse"  # type: ignore[name-defined]
