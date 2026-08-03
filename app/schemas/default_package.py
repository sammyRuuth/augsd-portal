"""Default Package Pydantic schemas"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class DefaultPackageBase(BaseModel):
    """Base default package schema"""

    year: int = Field(..., ge=2000, le=2100, description="Academic year")
    branch: str = Field(
        ..., min_length=1, max_length=100, description="Branch code (e.g., A1, A2)"
    )
    course_codes: list[str] = Field(
        ..., min_length=1, description="List of course codes"
    )


class DefaultPackageCreate(DefaultPackageBase):
    """Schema for creating a new default package"""

    pass


class DefaultPackageUpdate(BaseModel):
    """Schema for updating a default package"""

    course_codes: list[str] | None = Field(
        None, min_length=1, description="Updated course codes"
    )
    year: int | None = Field(None, ge=2000, le=2100, description="Updated year")
    branch: str | None = Field(
        None, min_length=1, max_length=100, description="Updated branch"
    )


class DefaultPackageResponse(DefaultPackageBase):
    """Schema for default package response"""

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class DefaultPackageBulkUpload(BaseModel):
    """Schema for bulk uploading default packages from JSON"""

    packages: dict[str, dict[str, list[str]]] = Field(
        ..., description="Format: {'2025': {'A1, A2': ['COURSE1', 'COURSE2']}}"
    )
    overwrite: bool = Field(
        default=False,
        description="If true, deletes existing packages for affected years before inserting",
    )
