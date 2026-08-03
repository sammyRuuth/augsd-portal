"""Course Pydantic schemas"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CourseBase(BaseModel):
    """Base course schema"""

    course_id: str = Field(..., min_length=1)
    subject: str = Field(..., min_length=1)
    catalog: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    max_units: float | None = Field(None, ge=0, le=99.99)


class CourseCreate(CourseBase):
    """Schema for creating a new course"""

    pass


class CourseResponse(CourseBase):
    """Schema for course response"""

    id: uuid.UUID
    created_at: datetime

    class Config:
        from_attributes = True


class CourseWithPrerequisites(CourseResponse):
    """Schema for course with prerequisites information"""

    prerequisites: list["PrerequisiteWithCourse"] = []  # type: ignore[name-defined]
