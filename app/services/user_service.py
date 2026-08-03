"""User service for user management operations"""

import uuid
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import generate_password, hash_password, verify_password
from app.models.user import User
from app.schemas.user import UserCreate


async def create_user(
    db: AsyncSession, user_create: UserCreate, created_by_id: uuid.UUID
) -> tuple[User, str]:
    """
    Create a new user (admin creates staff).

    Returns the user and the auto-generated password.
    """
    # Generate password
    plain_password = generate_password()

    # Create user
    user = User(
        email=user_create.email,
        password_hash=hash_password(plain_password),
        role=user_create.role,
        created_by_id=created_by_id,
        is_active=True,
    )

    db.add(user)
    await db.flush()
    await db.refresh(user)

    return user, plain_password


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    """Get user by email"""
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    """Get user by ID"""
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User | None:
    """Authenticate user with email and password"""
    user = await get_user_by_email(db, email)
    if not user:
        return None
    if not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


async def list_users(db: AsyncSession) -> Sequence[User]:
    """List all users"""
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return result.scalars().all()


async def deactivate_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    """Deactivate a user"""
    user = await get_user_by_id(db, user_id)
    if user:
        user.is_active = False
        await db.flush()
        await db.refresh(user)
    return user
