"""Prerequisite Pydantic schemas"""

import uuid

from pydantic import BaseModel, Field


class PrerequisiteBase(BaseModel):
    """Base prerequisite schema"""

    prereq_type: str = Field(..., pattern="^(AND|OR)$")
    prereq_order: int = Field(..., ge=1, le=4)
    is_corequisite: bool = False


class PrerequisiteCreate(PrerequisiteBase):
    """Schema for creating a new prerequisite"""

    course_id: uuid.UUID
    prereq_course_id: uuid.UUID


class PrerequisiteResponse(PrerequisiteBase):
    """Schema for prerequisite response"""

    id: uuid.UUID
    course_id: uuid.UUID
    prereq_course_id: uuid.UUID

    class Config:
        from_attributes = True


class PrerequisiteWithCourse(PrerequisiteResponse):
    """Schema for prerequisite with course details"""

    prereq_course: "CourseResponse"  # type: ignore[name-defined]
