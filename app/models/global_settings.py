"""Global settings model for application-wide defaults"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class GlobalSettings(Base):
    """
    Global Settings model - stores application-wide default values.

    Only one row should exist in this table. Used to pre-populate
    session creation forms and provide default values.
    """

    __tablename__ = "global_settings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Session defaults
    default_term_code: Mapped[str | None] = mapped_column(
        String(20), nullable=True, default=None
    )
    default_career: Mapped[str | None] = mapped_column(
        String(20), nullable=True, default=None
    )

    # Institution info
    institution_name: Mapped[str | None] = mapped_column(
        String(200), nullable=True, default="BITS Pilani"
    )

    # Session name templates
    session_name_template: Mapped[str | None] = mapped_column(
        Text, nullable=True, default="Semester {semester} {year}"
    )

    # Other settings
    auto_generate_session_names: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

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

    def __repr__(self) -> str:
        return f"<GlobalSettings term={self.default_term_code} career={self.default_career}>"
