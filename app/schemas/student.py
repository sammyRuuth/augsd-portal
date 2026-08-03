"""Student Pydantic schemas"""

import uuid
from datetime import date

from pydantic import BaseModel, EmailStr, Field


class StudentBase(BaseModel):
    """Base student schema"""

    campus_id: str = Field(
        ...,
        pattern=r"^\d{4}[A-Z0-9]{2}[A-Z0-9]{2}\d{4}[PGHD]?$",
        description="Campus ID format: YYYY + Branch(2) + Program/Branch2(2) + StudentNum(4) + optional Campus",
    )
    name: str = Field(..., min_length=1)
    email: EmailStr | None = None
    sex: str | None = None
    birthdate: date | None = None
    admission_category: str | None = None


class StudentCreate(StudentBase):
    """Schema for creating a new student"""

    student_id: int = Field(..., gt=0)


class StudentResponse(StudentBase):
    """Schema for student response"""

    id: uuid.UUID
    student_id: int
    has_timetable: bool = False

    class Config:
        from_attributes = True


class StudentSearch(BaseModel):
    """Schema for student search parameters"""

    query: str | None = None
    campus_id: str | None = None
    name: str | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)
