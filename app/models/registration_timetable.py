"""Registration Timetable models - imported from registration data"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.course_section import CourseSection
    from app.models.student import Student


class RegistrationTimetable(Base):
    """
    Registration Timetable model - imported from registration data.

    Stored in session-specific schema. Represents the original timetable
    imported from registration data uploads.
    """

    __tablename__ = "registration_timetables"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("students.id"),
        nullable=False,
        unique=True,
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
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Relationships
    student: Mapped["Student"] = relationship(
        "Student", back_populates="registration_timetable"
    )
    items: Mapped[list["RegistrationTimetableItem"]] = relationship(
        "RegistrationTimetableItem",
        back_populates="timetable",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<RegistrationTimetable {self.id} for student {self.student_id}>"


class RegistrationTimetableItem(Base):
    """
    Registration Timetable Item model - individual course sections.

    Represents the IMMUTABLE original state from registration data import.
    This table is never modified after import - it serves as the baseline
    for export diffing against the current state in timetables table.
    """

    __tablename__ = "registration_timetable_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    timetable_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("registration_timetables.id"),
        nullable=False,
        index=True,
    )
    course_section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("course_sections.id"), nullable=False
    )

    # Relationships
    timetable: Mapped["RegistrationTimetable"] = relationship(
        "RegistrationTimetable", back_populates="items"
    )
    course_section: Mapped["CourseSection"] = relationship(
        "CourseSection", back_populates="registration_timetable_items"
    )

    def __repr__(self) -> str:
        return f"<RegistrationTimetableItem {self.id}>"
