"""Timetable Audit Trail model - tracks all timetable modifications"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.timetable import Timetable


class TimetableAuditTrail(Base):
    """
    Audit trail for timetable changes.

    Tracks every modification made to student timetables for:
    - Compliance and auditing
    - Export diff verification
    - Change history tracking

    Stored in session-specific schema.
    """

    __tablename__ = "timetable_audit_trail"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    timetable_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timetables.id"), nullable=False, index=True
    )

    # Action type: 'add_course', 'remove_course', 'swap_section', 'commit', 'uncommit', 'initialize'
    action: Mapped[str] = mapped_column(Text, nullable=False, index=True)

    # Structured details about the change (JSON)
    # Examples:
    # - add_course: {"course_id": "uuid", "section_ids": ["uuid1", "uuid2"]}
    # - remove_course: {"course_id": "uuid", "section_ids": ["uuid1", "uuid2"]}
    # - swap_section: {"course_id": "uuid", "old_section_id": "uuid", "new_section_id": "uuid"}
    # - commit: {"section_ids": ["uuid1", "uuid2", ...], "total_units": 18.0}
    # - initialize: {"source": "registration", "section_ids": ["uuid1", ...]}
    details: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Audit metadata
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    changed_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )  # References global users table

    # Optional human-readable note
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    timetable: Mapped["Timetable"] = relationship(
        "Timetable", back_populates="audit_trail"
    )

    def __repr__(self) -> str:
        return (
            f"<TimetableAuditTrail {self.id} action={self.action} at={self.changed_at}>"
        )
