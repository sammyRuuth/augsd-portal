"""Timetable Audit Trail Pydantic schemas"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TimetableAuditTrailBase(BaseModel):
    """Base schema for audit trail"""

    action: str = Field(
        ...,
        description="Action type: add_course, remove_course, swap_section, commit, uncommit, initialize",
    )
    details: dict[str, Any] | None = Field(
        None, description="Structured details about the change (JSON)"
    )
    note: str | None = Field(None, description="Optional human-readable note")


class TimetableAuditTrailCreate(TimetableAuditTrailBase):
    """Schema for creating audit trail entry"""

    timetable_id: uuid.UUID
    changed_by_id: uuid.UUID


class TimetableAuditTrailResponse(TimetableAuditTrailBase):
    """Schema for audit trail response"""

    id: uuid.UUID
    timetable_id: uuid.UUID
    changed_at: datetime
    changed_by_id: uuid.UUID

    class Config:
        from_attributes = True


class TimetableAuditTrailSummary(BaseModel):
    """Summary of audit trail for a timetable"""

    total_changes: int
    last_changed_at: datetime | None = None
    last_changed_by_id: uuid.UUID | None = None
    changes: list[TimetableAuditTrailResponse] = []
