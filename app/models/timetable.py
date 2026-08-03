"""Timetable models for student schedules"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.course_section import CourseSection
    from app.models.student import Student
    from app.models.timetable_audit_trail import TimetableAuditTrail


# A timetable counts as live once it is committed. Editing a registration-sourced
# timetable moves it to 'edited', which is still live - only 'draft' is not.
# Filtering on 'committed' alone makes edited timetables vanish from the UI.
ACTIVE_TIMETABLE_STATUSES = ("committed", "edited")


class Timetable(Base):
    """
    Unified Timetable model - stored in session-specific schema.

    Represents the CURRENT state of a student's timetable, regardless of source.
    Can be:
    - Portal-generated (created via generation algorithm)
    - From registration (imported from registration data, then editable)
    """

    __tablename__ = "timetables"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id"), nullable=False, index=True
    )

    # Source: where this timetable originated from
    # - 'portal_generated': Created via portal generation algorithm
    # - 'from_registration': Initialized from registration data upload
    source: Mapped[str] = mapped_column(
        Enum("portal_generated", "from_registration", name="timetable_source"),
        nullable=False,
        default="portal_generated",
        index=True,
    )

    # Status: current state of the timetable
    # - 'draft': Not yet committed (preview state)
    # - 'committed': Finalized and saved
    # - 'edited': Originally from registration but has been modified
    status: Mapped[str] = mapped_column(
        Enum("draft", "committed", "edited", name="timetable_status"),
        nullable=False,
        default="draft",
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    # created_by references global users table (stored as UUID, not FK in session schema)
    created_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    total_units: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), nullable=True)

    # Track when timetable was last updated
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Track if this timetable was assigned from a buffer timetable
    # If set, uncommit will decrement the buffer's assigned_count
    buffer_timetable_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Relationships
    student: Mapped["Student"] = relationship("Student", back_populates="timetables")
    items: Mapped[list["TimetableItem"]] = relationship(
        "TimetableItem", back_populates="timetable", cascade="all, delete-orphan"
    )
    audit_trail: Mapped[list["TimetableAuditTrail"]] = relationship(
        "TimetableAuditTrail",
        back_populates="timetable",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<Timetable {self.id} source={self.source} status={self.status}>"


class TimetableItem(Base):
    """Timetable Item model - individual course sections in a timetable"""

    __tablename__ = "timetable_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    timetable_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timetables.id"), nullable=False, index=True
    )
    course_section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("course_sections.id"), nullable=False
    )

    # Relationships
    timetable: Mapped["Timetable"] = relationship("Timetable", back_populates="items")
    course_section: Mapped["CourseSection"] = relationship(
        "CourseSection", back_populates="timetable_items"
    )

    def __repr__(self) -> str:
        return f"<TimetableItem {self.id}>"
