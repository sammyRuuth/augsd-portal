"""User Pydantic schemas"""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserBase(BaseModel):
    """Base user schema"""

    email: EmailStr


class UserCreate(UserBase):
    """Schema for creating a new user (admin creates staff)"""

    role: str = Field(default="staff", pattern="^(admin|staff)$")


class UserLogin(BaseModel):
    """Schema for user login"""

    email: EmailStr
    password: str


class UserResponse(UserBase):
    """Schema for user response"""

    id: uuid.UUID
    role: str
    is_active: bool
    created_at: datetime
    created_by_id: uuid.UUID | None = None

    class Config:
        from_attributes = True


class Token(BaseModel):
    """Schema for JWT token response"""

    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class PasswordResponse(BaseModel):
    """Schema for auto-generated password response"""

    user: UserResponse
    password: str
    message: str = (
        "User created successfully. Save this password - it won't be shown again."
    )
