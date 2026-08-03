"""Default package model for course recommendations"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DefaultPackage(Base):
    """
    Default Package model - stored in global database.

    Stores one branch per row for easier querying and updates.
    Unique constraint ensures no duplicate (year, branch) combinations.
    """

    __tablename__ = "default_packages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    branch: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    course_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("year", "branch", name="uq_default_package_year_branch"),
        Index("ix_default_package_year_branch", "year", "branch"),
    )

    def __repr__(self) -> str:
        return f"<DefaultPackage {self.year}-{self.branch}: {len(self.course_codes)} courses>"
