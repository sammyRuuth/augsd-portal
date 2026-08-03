"""Admin API routes for user and session management"""

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin
from app.core.parsers import parse_prerequisites_excel
from app.database import get_db
from app.models.course import Course
from app.models.user import User
from app.schemas.common import Message
from app.schemas.default_package import (
    DefaultPackageCreate,
    DefaultPackageResponse,
    DefaultPackageUpdate,
)
from app.schemas.global_settings import GlobalSettingsResponse, GlobalSettingsUpdate
from app.schemas.session import SessionCreate, SessionResponse, SessionUpdate
from app.schemas.user import PasswordResponse, UserCreate, UserResponse
from app.services.session_service import (
    create_session,
    delete_session,
    update_session,
)
from app.services.user_service import create_user, list_users

router = APIRouter(prefix="/api/admin", tags=["admin"])


# User management


@router.post("/users", response_model=PasswordResponse)
async def create_staff_user(
    user_create: UserCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_admin: Annotated[User, Depends(get_current_admin)],
):
    """
    Create a new staff user (admin only).

    Returns the created user and auto-generated password.
    Password is shown once - must be saved by admin.
    """
    user, password = await create_user(db, user_create, current_admin.id)
    await db.commit()

    return PasswordResponse(
        user=UserResponse.model_validate(user),
        password=password,
    )


@router.get("/users", response_model=list[UserResponse])
async def get_users(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_admin: Annotated[User, Depends(get_current_admin)],
):
    """List all users (admin only)"""
    users = await list_users(db)
    return [UserResponse.model_validate(u) for u in users]


# Global Settings


@router.get("/settings", response_model=GlobalSettingsResponse)
async def get_global_settings(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_admin: Annotated[User, Depends(get_current_admin)],
):
    """Get global settings (admin only)"""
    from app.services.global_settings_service import get_or_create_global_settings

    settings = await get_or_create_global_settings(db)
    return GlobalSettingsResponse.model_validate(settings)


@router.put("/settings", response_model=GlobalSettingsResponse)
async def update_settings(
    settings_data: GlobalSettingsUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_admin: Annotated[User, Depends(get_current_admin)],
):
    """Update global settings (admin only)"""
    from app.services.global_settings_service import update_global_settings

    settings = await update_global_settings(db, settings_data)
    await db.commit()
    return GlobalSettingsResponse.model_validate(settings)


# Session management


@router.post("/sessions", response_model=SessionResponse)
async def create_new_session(
    session_create: SessionCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_admin: Annotated[User, Depends(get_current_admin)],
):
    """Create a new session (admin only)"""
    session = await create_session(db, session_create, current_admin.id)
    await db.commit()
    return SessionResponse.model_validate(session)


@router.put("/sessions/{session_id}", response_model=SessionResponse)
async def update_session_status(
    session_id: uuid.UUID,
    session_update: SessionUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_admin: Annotated[User, Depends(get_current_admin)],
):
    """Update session (enable/disable) - admin only"""
    session = await update_session(db, session_id, session_update)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    await db.commit()
    return SessionResponse.model_validate(session)


@router.delete("/sessions/{session_id}", response_model=Message)
async def delete_session_by_id(
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_admin: Annotated[User, Depends(get_current_admin)],
):
    """Delete session and its schema (admin only)"""
    success = await delete_session(db, session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    await db.commit()
    return Message(message="Session deleted successfully")


# Global data uploads


@router.post("/upload/prerequisites", response_model=Message)
async def upload_prerequisites(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Upload prerequisites Excel file (admin only)"""
    # Save file permanently in global folder
    from app.core.utils import save_upload_file

    content = await file.read()
    file_path = await save_upload_file(content, file.filename)

    try:
        # Parse file
        prereq_data = parse_prerequisites_excel(file_path)

        # Process prerequisites
        from app.models.prerequisite import Prerequisite

        # clear existing prerequisites for courses in the file to avoid duplicates
        # optimizing by fetching all relevant courses first would be better but for now let's go row by row or small batches

        # Track counts
        processed_count = 0
        skipped_count = 0

        for prereq in prereq_data:
            # Find main course
            course_result = await db.execute(
                select(Course).where(
                    Course.subject == prereq["subject"],
                    Course.catalog == prereq["catalog"],
                )
            )
            course = course_result.scalars().first()
            if not course:
                skipped_count += 1
                continue

            # Parse prereq value to find prereq course
            # Expected format: "BIO F111"
            prereq_str = prereq.get("prereq_value", "").strip()
            if not prereq_str:
                continue

            parts = prereq_str.split()
            if len(parts) < 2:
                # Try to use just catalog if subject is same? No, safer to skip.
                skipped_count += 1
                continue

            p_subject = parts[0]
            p_catalog = parts[1]

            # Find prerequisite course
            p_course_result = await db.execute(
                select(Course).where(
                    Course.subject == p_subject,
                    Course.catalog == p_catalog,
                )
            )
            p_course = p_course_result.scalars().first()

            if not p_course:
                skipped_count += 1
                continue

            # Check if this specific prereq rule already exists
            existing_rule = await db.execute(
                select(Prerequisite).where(
                    Prerequisite.course_id == course.id,
                    Prerequisite.prereq_course_id == p_course.id,
                    Prerequisite.prereq_order == prereq["prereq_order"],
                )
            )
            if existing_rule.scalars().first():
                continue

            # Create prerequisite
            new_prereq = Prerequisite(
                course_id=course.id,
                prereq_course_id=p_course.id,
                prereq_type=prereq.get("prereq_type", "AND"),
                prereq_order=prereq.get("prereq_order", 1),
                is_corequisite=prereq.get("is_corequisite", False),
            )
            db.add(new_prereq)
            processed_count += 1

        await db.commit()
        return Message(
            message=f"Prerequisites uploaded successfully. Added {processed_count} rules, skipped {skipped_count} entries."
        )

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse file: {str(e)}")


# Default Packages Management


@router.get("/packages", response_model=list[DefaultPackageResponse])
async def get_default_packages(
    year: int | None = Query(None, description="Filter by year"),
    branch: str | None = Query(None, description="Filter by branch"),
    limit: int = Query(100, ge=1, le=500, description="Maximum results"),
    offset: int = Query(0, ge=0, description="Results to skip"),
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """List default packages with optional filters (admin only)"""
    from app.services.default_package_service import list_packages

    packages = await list_packages(
        db, year=year, branch=branch, limit=limit, offset=offset
    )
    return [DefaultPackageResponse.model_validate(p) for p in packages]


@router.get("/packages/years", response_model=list[int])
async def get_package_years(
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Get all unique years that have default packages (admin only)"""
    from app.services.default_package_service import get_unique_years

    return await get_unique_years(db)


@router.get("/packages/branches", response_model=list[str])
async def get_package_branches(
    year: int | None = Query(None, description="Filter by year"),
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Get all unique branches, optionally filtered by year (admin only)"""
    from app.services.default_package_service import get_unique_branches

    return await get_unique_branches(db, year=year)


@router.get("/packages/{package_id}", response_model=DefaultPackageResponse)
async def get_default_package(
    package_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Get a specific default package by ID (admin only)"""
    from app.services.default_package_service import get_package_by_id

    package = await get_package_by_id(db, package_id)
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")
    return DefaultPackageResponse.model_validate(package)


@router.post("/packages", response_model=DefaultPackageResponse)
async def create_default_package(
    package_data: DefaultPackageCreate,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Create a new default package (admin only)"""
    from app.services.default_package_service import (
        create_package,
        get_package_by_year_branch,
    )

    # Check if package already exists
    existing = await get_package_by_year_branch(
        db, package_data.year, package_data.branch
    )
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Package for year {package_data.year} and branch {package_data.branch} already exists",
        )

    package = await create_package(db, package_data)
    await db.commit()
    return DefaultPackageResponse.model_validate(package)


@router.put("/packages/{package_id}", response_model=DefaultPackageResponse)
async def update_default_package(
    package_id: uuid.UUID,
    package_data: DefaultPackageUpdate,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Update a default package (admin only)"""
    from app.services.default_package_service import update_package

    package = await update_package(db, package_id, package_data)
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")

    await db.commit()
    return DefaultPackageResponse.model_validate(package)


@router.delete("/packages/{package_id}", response_model=Message)
async def delete_default_package(
    package_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Delete a default package (admin only)"""
    from app.services.default_package_service import delete_package

    success = await delete_package(db, package_id)
    if not success:
        raise HTTPException(status_code=404, detail="Package not found")

    await db.commit()
    return Message(message="Package deleted successfully")


@router.delete("/packages/year/{year}", response_model=Message)
async def delete_packages_by_year(
    year: int,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Delete all packages for a specific year (admin only)"""
    from app.services.default_package_service import delete_packages_by_year

    count = await delete_packages_by_year(db, year)
    await db.commit()
    return Message(message=f"Deleted {count} package(s) for year {year}")


@router.post("/upload/packages")
async def upload_default_packages(
    file: UploadFile = File(...),
    overwrite: bool = False,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """
    Upload default packages JSON file (admin only).

    Supports bulk upload with automatic parsing of comma-separated branches.
    """
    from app.services.default_package_service import parse_and_upsert_from_json

    # Read JSON content
    content = await file.read()
    try:
        packages_data = json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON file")

    # Validate basic structure
    if not isinstance(packages_data, dict):
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON format. Expected: {'2025': {'A1, A2': ['COURSE1']}}",
        )

    # Parse and upsert packages
    try:
        total, years_count, years_list = await parse_and_upsert_from_json(
            db, packages_data, overwrite=overwrite
        )
        await db.commit()

        return {
            "message": "Default packages uploaded successfully",
            "total_packages": total,
            "affected_years": years_count,
            "years": years_list,
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=400, detail=f"Failed to process packages: {str(e)}"
        )
