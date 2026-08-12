"""Course API routes"""

import uuid
from typing import List

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_db
from app.database import get_session_db, get_session_db_read_only
from app.models.course import Course
from app.models.prerequisite import Prerequisite
from app.models.user import User
from app.schemas.course import CourseResponse, CourseWithPrerequisites
from app.schemas.course_section import CourseSectionResponse, CourseSectionUpdate
from app.services.course_cache_service import search_courses_cached
from app.services.course_service import (
    get_course_by_id,
    get_offered_course_ids,
    get_sections_by_class_nbr,
    list_course_sections,
    update_section_capacity,
)
from app.services.session_service import get_session_by_id

router = APIRouter(prefix="/api/sessions/{session_id}/courses", tags=["courses"])


class BulkCapacityUpdate(BaseModel):
    """Schema for bulk capacity update"""

    class_nbr: int = Field(..., gt=0)
    cap_enrl: int | None = Field(None, ge=0)


@router.post("/prerequisites", response_model=list[CourseWithPrerequisites])
async def get_courses_prerequisites(
    session_id: uuid.UUID,
    course_ids: list[uuid.UUID] = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get prerequisites for a list of courses"""
    if not course_ids:
        return []

    stmt = (
        select(Course)
        .options(
            selectinload(Course.prerequisites).selectinload(Prerequisite.prereq_course)
        )
        .where(Course.id.in_(course_ids))
    )

    result = await db.execute(stmt)
    courses = result.scalars().all()

    # Pydantic will handle the serialization based on CourseWithPrerequisites schema
    return courses


@router.get("", response_model=list[CourseResponse])
async def get_courses(
    session_id: uuid.UUID,
    query: str | None = Query(
        None, description="Search query (e.g., 'CS F2', 'Data Structures')"
    ),
    include_not_offered: bool = Query(
        False,
        description="Include catalog courses that have no sections in this session",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Search courses offered in this session, with smart multi-word support.

    Supports patterns like:
    - "CS F2" → matches CS F211, CS F213, etc.
    - "data structures" → matches title containing both words
    - "CS" → matches all CS courses

    The catalog itself is global and accumulates courses from every term, so results
    are restricted to courses that actually have sections in this session. Without
    that filter a course from an earlier term shows up in the picker and then offers
    no sections to choose from. Pass include_not_offered=true for the raw catalog.
    """
    courses = await search_courses_cached(db, query)

    if include_not_offered:
        return [CourseResponse.model_validate(c) for c in courses]

    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    async for session_db in get_session_db_read_only(session.schema_name):
        offered = await get_offered_course_ids(session_db)
        return [
            CourseResponse.model_validate(c) for c in courses if c.id in offered
        ]


@router.get("/sections", response_model=list[CourseSectionResponse])
async def get_course_sections(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all course sections in the session"""
    # Get session
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get session database - use read-only for better concurrency
    async for session_db in get_session_db_read_only(session.schema_name):
        sections = await list_course_sections(session_db)
        return [CourseSectionResponse.model_validate(s) for s in sections]


@router.get("/{course_id}", response_model=CourseResponse)
async def get_course(
    session_id: uuid.UUID,
    course_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get course details"""
    course = await get_course_by_id(db, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    return CourseResponse.model_validate(course)


@router.put(
    "/sections/{class_nbr}/capacity", response_model=list[CourseSectionResponse]
)
async def update_section_capacity_endpoint(
    session_id: uuid.UUID,
    class_nbr: int,
    update_data: CourseSectionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update capacity for all meeting times of a section (by class_nbr).

    Since sections can have multiple meeting times (same class_nbr),
    this updates cap_enrl for all rows with the given class_nbr.
    """
    # Get session
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get session database
    async for session_db in get_session_db(session.schema_name):
        # Check if sections exist
        sections = await get_sections_by_class_nbr(session_db, class_nbr)
        if not sections:
            raise HTTPException(
                status_code=404, detail=f"No sections found with class_nbr {class_nbr}"
            )

        # Update capacity
        updated_sections = await update_section_capacity(
            session_db, class_nbr, update_data.cap_enrl
        )

        await session_db.commit()
        return [CourseSectionResponse.model_validate(s) for s in updated_sections]


@router.put("/sections/bulk-capacity", response_model=dict)
async def bulk_update_section_capacity(
    session_id: uuid.UUID,
    updates: List[BulkCapacityUpdate] = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Bulk update capacity for multiple sections.

    Each update specifies a class_nbr and new cap_enrl value.
    All meeting times with the same class_nbr are updated together.
    """
    # Get session
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get session database
    async for session_db in get_session_db(session.schema_name):
        updated_count = 0
        errors = []

        for update in updates:
            try:
                sections = await get_sections_by_class_nbr(session_db, update.class_nbr)
                if not sections:
                    errors.append(f"class_nbr {update.class_nbr} not found")
                    continue

                await update_section_capacity(
                    session_db, update.class_nbr, update.cap_enrl
                )
                updated_count += 1
            except Exception as e:
                errors.append(f"class_nbr {update.class_nbr}: {str(e)}")

        await session_db.commit()

        return {
            "updated_count": updated_count,
            "total_requested": len(updates),
            "errors": errors if errors else None,
        }
