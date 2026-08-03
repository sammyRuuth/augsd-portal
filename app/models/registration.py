"""Registration data and file upload tracking models"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import Date, DateTime, Enum, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RegistrationData(Base):
    """Registration Data model - stored in session-specific schema"""

    __tablename__ = "registration_data"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    campus_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    course_id: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    catalog: Mapped[str] = mapped_column(Text, nullable=False)
    section: Mapped[str] = mapped_column(Text, nullable=False)
    component: Mapped[str] = mapped_column(Text, nullable=False)
    class_nbr: Mapped[int] = mapped_column(Integer, nullable=False)
    add_dt: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    drop_dt: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    unit_taken: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), nullable=True)
    grade_in: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    instructor_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    admit_sem: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_reg_sem: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    degree1: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    degree2: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<RegistrationData {self.campus_id}: {self.subject} {self.catalog}>"


class UploadedFile(Base):
    """Uploaded File model - tracks file uploads in session"""

    __tablename__ = "uploaded_files"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    file_type: Mapped[str] = mapped_column(
        Enum(
            "students",
            "courses",
            "timetable",
            "registration",
            "prerequisites",
            name="file_type",
        ),
        nullable=False,
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    # uploaded_by references global users table
    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        Enum("processing", "completed", "failed", name="upload_status"),
        nullable=False,
        default="processing",
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<UploadedFile {self.filename} ({self.status})>"
