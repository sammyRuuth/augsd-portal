"""Session Pydantic schemas"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SessionBase(BaseModel):
    """Base session schema"""

    name: str = Field(..., min_length=1, max_length=255)
    term_code: str = Field(..., min_length=1, max_length=50)
    career: str = Field(..., min_length=1, max_length=50)


class SessionCreate(SessionBase):
    """Schema for creating a new session"""

    pass


class SessionUpdate(BaseModel):
    """Schema for updating a session"""

    is_enabled: bool


class SessionResponse(SessionBase):
    """Schema for session response"""

    id: uuid.UUID
    is_enabled: bool
    schema_name: str
    created_at: datetime
    created_by_id: uuid.UUID

    class Config:
        from_attributes = True


class SessionStatistics(BaseModel):
    """Schema for session statistics"""

    total_students: int
    total_courses: int
    total_sections: int
    total_timetables: int
    total_committed_timetables: int
