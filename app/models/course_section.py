"""Course section model for session-specific course offerings"""

from __future__ import annotations

import uuid
from datetime import date, time
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Date, Integer, Text, Time
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.registration_timetable import RegistrationTimetableItem
    from app.models.timetable import TimetableItem


class CourseSection(Base):
    """
    Course Section model - stored in session-specific schema.

    Note: class_nbr is NOT unique because a section can have multiple meeting times
    (e.g., MW 12:00-12:50 AND TH 15:00-15:50). Each row represents one meeting slot.
    Use (class_nbr, day, mtg_start) as the logical unique key.
    """

    __tablename__ = "course_sections"
    # Note: Unique index on (class_nbr, COALESCE(day, ''), COALESCE(mtg_start::text, ''))
    # is created via raw SQL in session_service.py for proper NULL handling

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Foreign key to global courses table
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    # class_nbr identifies the section, but NOT unique (multiple meeting times)
    class_nbr: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    section: Mapped[str] = mapped_column(Text, nullable=False)
    component: Mapped[str] = mapped_column(Text, nullable=False)
    class_pattern: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    day: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # Individual day (M, T, W, TH, F, S)
    mtg_start: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    mtg_end: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    exam_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    exam_start: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    exam_end: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    instructor: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    room: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cap_enrl: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tot_enrl: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    timetable_items: Mapped[list["TimetableItem"]] = relationship(
        "TimetableItem", back_populates="course_section", cascade="all, delete-orphan"
    )
    registration_timetable_items: Mapped[list["RegistrationTimetableItem"]] = (
        relationship(
            "RegistrationTimetableItem",
            back_populates="course_section",
            cascade="all, delete-orphan",
        )
    )

    def __repr__(self) -> str:
        return f"<CourseSection {self.class_nbr}: {self.section} {self.day} ({self.component})>"
