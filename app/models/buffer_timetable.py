"""Buffer Timetable models for pre-generated timetable templates"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class BufferTimetable(Base):
    """
    Buffer Timetable model - stored in session-specific schema.

    Represents a pre-generated timetable template that can be assigned
    to students. These are uploaded from the timetable generator exports
    and contain a fixed set of course sections.

    The assigned_count tracks how many students have been assigned to this
    timetable, allowing admins to see remaining capacity.
    """

    __tablename__ = "buffer_timetables"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Plan identifier - matches student branches/programs
    # e.g., "A3,A4,A5,A7", "COMBINED:A3,A4+ALL_MM", "A5_PCB"
    plan: Mapped[str] = mapped_column(Text, nullable=False, index=True)

    # Sequential timetable ID within the plan (1, 2, 3, ...)
    timetable_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # Batch size - original number of students this was allocated for
    batch_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Capacity ceiling - maximum students this timetable could serve
    capacity_ceiling: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # How many students have been assigned to this timetable
    assigned_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Whether this is a variant (overflow/backup) timetable
    is_variant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Whether enrollment counts were deducted on upload
    # If True: tot_enrl was decreased when CSV was uploaded
    # If False: tot_enrl will be decreased when each student is assigned
    enrollment_deducted_on_upload: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # Optional description/label for this timetable
    label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Relationships
    items: Mapped[list["BufferTimetableItem"]] = relationship(
        "BufferTimetableItem",
        back_populates="buffer_timetable",
        cascade="all, delete-orphan",
    )

    @property
    def remaining_capacity(self) -> int:
        """Calculate remaining capacity (batch_size - assigned_count)."""
        return max(0, self.batch_size - self.assigned_count)

    @property
    def fill_percentage(self) -> float:
        """Calculate fill percentage."""
        if self.batch_size == 0:
            return 0.0
        return (self.assigned_count / self.batch_size) * 100

    @property
    def is_full(self) -> bool:
        """Check if timetable has reached its batch_size."""
        return self.assigned_count >= self.batch_size

    def __repr__(self) -> str:
        return (
            f"<BufferTimetable {self.plan}#{self.timetable_id} "
            f"({self.assigned_count}/{self.batch_size})>"
        )


class BufferTimetableItem(Base):
    """
    Buffer Timetable Item - links a buffer timetable to course sections.

    Each item represents one course section (identified by class_nbr) in the
    pre-generated timetable. The course_section_id links to the actual
    CourseSection record in the session schema.
    """

    __tablename__ = "buffer_timetable_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    buffer_timetable_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("buffer_timetables.id"),
        nullable=False,
        index=True,
    )

    # Reference to the actual course section
    course_section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course_sections.id"),
        nullable=False,
    )

    # Store course info for display (denormalized for convenience)
    course_code: Mapped[str] = mapped_column(String(50), nullable=False)
    component: Mapped[str] = mapped_column(String(10), nullable=False)
    section: Mapped[str] = mapped_column(String(20), nullable=False)

    # Relationships
    buffer_timetable: Mapped["BufferTimetable"] = relationship(
        "BufferTimetable", back_populates="items"
    )

    def __repr__(self) -> str:
        return f"<BufferTimetableItem {self.course_code} {self.section} ({self.component})>"
