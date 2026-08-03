"""Student model for session-specific student data"""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, Date, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.registration_timetable import RegistrationTimetable
    from app.models.saved_timetable_draft import SavedTimetableDraft
    from app.models.timetable import Timetable


class Student(Base):
    """Student model - stored in session-specific schema"""

    __tablename__ = "students"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    student_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False, index=True
    )
    campus_id: Mapped[str] = mapped_column(
        String, unique=True, nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sex: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    birthdate: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    admission_category: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships - portal-created timetables
    timetables: Mapped[list["Timetable"]] = relationship(
        "Timetable", back_populates="student", cascade="all, delete-orphan"
    )
    # Registration-imported timetable (one per student)
    registration_timetable: Mapped[Optional["RegistrationTimetable"]] = relationship(
        "RegistrationTimetable",
        back_populates="student",
        cascade="all, delete-orphan",
        uselist=False,
    )
    # Saved timetable drafts
    saved_drafts: Mapped[list["SavedTimetableDraft"]] = relationship(
        "SavedTimetableDraft",
        back_populates="student",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Student {self.campus_id}: {self.name}>"
