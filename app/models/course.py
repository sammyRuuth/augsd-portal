"""Course model for global course catalog"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.prerequisite import Prerequisite


class Course(Base):
    """Course model - stored in global database"""

    __tablename__ = "courses"
    __table_args__ = (
        UniqueConstraint("course_id", "subject", "catalog", name="uq_course_identity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    course_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String, nullable=False, index=True)
    catalog: Mapped[str] = mapped_column(String, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    max_units: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    prerequisites: Mapped[list["Prerequisite"]] = relationship(
        "Prerequisite",
        foreign_keys="[Prerequisite.course_id]",
        back_populates="course",
    )
    prerequisite_for: Mapped[list["Prerequisite"]] = relationship(
        "Prerequisite",
        foreign_keys="[Prerequisite.prereq_course_id]",
        back_populates="prereq_course",
    )

    def __repr__(self) -> str:
        return f"<Course {self.subject} {self.catalog}: {self.title}>"
