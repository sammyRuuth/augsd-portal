"""Prerequisite model for course dependencies"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.course import Course


class Prerequisite(Base):
    """Prerequisite model - stored in global database"""

    __tablename__ = "prerequisites"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id"), nullable=False
    )
    prereq_course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id"), nullable=False
    )
    prereq_type: Mapped[str] = mapped_column(
        Enum("AND", "OR", name="prereq_type"), nullable=False
    )
    prereq_order: Mapped[int] = mapped_column(Integer, nullable=False)
    is_corequisite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    course: Mapped["Course"] = relationship(
        "Course",
        foreign_keys=[course_id],
        back_populates="prerequisites",
    )
    prereq_course: Mapped["Course"] = relationship(
        "Course",
        foreign_keys=[prereq_course_id],
        back_populates="prerequisite_for",
    )

    def __repr__(self) -> str:
        return f"<Prerequisite {self.prereq_order}: {self.prereq_type}>"
