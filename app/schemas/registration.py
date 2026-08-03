"""Registration Data Pydantic schemas"""

import uuid
from datetime import date

from pydantic import BaseModel, Field


class RegistrationDataBase(BaseModel):
    """Base registration data schema"""

    campus_id: str = Field(..., min_length=1)
    course_id: str = Field(..., min_length=1)
    subject: str = Field(..., min_length=1)
    catalog: str = Field(..., min_length=1)
    section: str = Field(..., min_length=1)
    component: str = Field(..., min_length=1)
    class_nbr: int = Field(..., gt=0)
    add_dt: date | None = None
    drop_dt: date | None = None
    unit_taken: float | None = Field(None, ge=0, le=99.99)
    grade_in: str | None = None
    instructor_name: str | None = None
    admit_sem: str | None = None
    last_reg_sem: str | None = None
    degree1: str | None = None
    degree2: str | None = None


class RegistrationDataCreate(RegistrationDataBase):
    """Schema for creating registration data"""

    pass


class RegistrationDataResponse(RegistrationDataBase):
    """Schema for registration data response"""

    id: uuid.UUID

    class Config:
        from_attributes = True
