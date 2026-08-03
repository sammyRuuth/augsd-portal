"""Global Settings Pydantic schemas"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class GlobalSettingsBase(BaseModel):
    """Base global settings schema"""

    default_term_code: str | None = Field(
        None, max_length=20, description="Default term code for new sessions"
    )
    default_career: str | None = Field(
        None, max_length=20, description="Default career for new sessions"
    )
    institution_name: str | None = Field(
        None, max_length=200, description="Institution name"
    )
    session_name_template: str | None = Field(
        None, description="Template for auto-generating session names"
    )
    auto_generate_session_names: bool = Field(
        True, description="Auto-generate session names with timestamp"
    )


class GlobalSettingsUpdate(GlobalSettingsBase):
    """Schema for updating global settings"""

    pass


class GlobalSettingsResponse(GlobalSettingsBase):
    """Schema for global settings response"""

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
