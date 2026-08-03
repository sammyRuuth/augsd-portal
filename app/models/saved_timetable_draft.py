"""Saved Timetable Draft model for storing draft timetable configurations"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.student import Student


class SavedTimetableDraft(Base):
    """
    Saved Timetable Draft model - stored in session-specific schema.

    Stores draft timetable configurations for later review, including:
    - Selected course IDs
    - Pinned section preferences
    - Seat preferences
    - User-provided name for the draft
    """

    __tablename__ = "saved_timetable_drafts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id"), nullable=False, index=True
    )

    # User-provided name for the draft
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Optional description/notes
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Selected course IDs (list of UUIDs as strings)
    selected_course_ids: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=list
    )

    # Pinned sections: {course_id: {component: class_nbr}}
    pinned_sections: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Seat preferences: {prefer_lab_seats, prefer_tut_seats, prefer_lec_seats}
    seat_preferences: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Created by user ID (references global users table, stored as UUID not FK)
    created_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # Relationships
    student: Mapped["Student"] = relationship("Student", back_populates="saved_drafts")

    def __repr__(self) -> str:
        return f"<SavedTimetableDraft {self.id} name={self.name}>"
