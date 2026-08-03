"""Student API routes"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.database import get_session_db_read_only
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.schemas.student import StudentResponse, StudentSearch
from app.services.session_service import get_session_by_id
from app.services.student_service import (
    get_branch_list,
    get_student_by_id,
    get_student_stats,
    list_students_with_timetable_status,
    search_students,
    search_students_fast,
)

router = APIRouter(prefix="/api/sessions/{session_id}/students", tags=["students"])


@router.get("/search")
async def search_students_endpoint(
    session_id: uuid.UUID,
    q: Optional[str] = Query(None, description="Search query"),
    timetable: str = Query(
        "all", description="Filter: all, with, without, portal, registration"
    ),
    branch: Optional[str] = Query(None, description="Branch filter like A3, B2"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Results per page"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Fast server-side student search with filters and pagination.

    This is the primary endpoint for the students list page.
    """
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if current_user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Session is disabled")

    # Use read-only session for better concurrency
    async for session_db in get_session_db_read_only(session.schema_name):
        result = await search_students_fast(
            session_db,
            query=q,
            timetable_filter=timetable,
            branch_filter=branch,
            page=page,
            page_size=page_size,
        )
        return result


@router.get("/stats")
async def get_students_stats(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get summary statistics for students"""
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if current_user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Session is disabled")

    # Use read-only session for better concurrency
    async for session_db in get_session_db_read_only(session.schema_name):
        return await get_student_stats(session_db)


@router.get("/branches")
async def get_branches(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get list of distinct branches from student data"""
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Use read-only session for better concurrency
    async for session_db in get_session_db_read_only(session.schema_name):
        branches = await get_branch_list(session_db)
        return {"branches": branches}


@router.get("", response_model=PaginatedResponse[StudentResponse])
async def get_students(
    session_id: uuid.UUID,
    search: StudentSearch = Depends(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List/search students in a session (legacy endpoint)"""
    # Get session
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Staff can only access enabled sessions
    if current_user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Session is disabled")

    # Get session database - use read-only for better concurrency
    async for session_db in get_session_db_read_only(session.schema_name):
        result = await search_students(session_db, search)
        return PaginatedResponse(
            items=[StudentResponse.model_validate(s) for s in result.items],
            total=result.total,
            page=result.page,
            page_size=result.page_size,
            total_pages=result.total_pages,
        )


@router.get("/all", response_model=list[StudentResponse])
async def get_all_students(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get all students in a session with timetable status (legacy - avoid for large datasets)"""
    # Get session
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Staff can only access enabled sessions
    if current_user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Session is disabled")

    # Get session database - use read-only for better concurrency
    async for session_db in get_session_db_read_only(session.schema_name):
        students_data = await list_students_with_timetable_status(session_db)
        return [StudentResponse(**s) for s in students_data]


@router.get("/{student_id}", response_model=StudentResponse)
async def get_student(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get student by ID"""
    # Get session
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Staff can only access enabled sessions
    if current_user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Session is disabled")

    # Get session database - use read-only for better concurrency
    async for session_db in get_session_db_read_only(session.schema_name):
        student = await get_student_by_id(session_db, student_id)
        if not student:
            raise HTTPException(status_code=404, detail="Student not found")
        return StudentResponse.model_validate(student)


@router.get("/{student_id}/suggested-packages")
async def get_student_suggested_packages(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get default packages that match the student's year and branch.

    Returns packages with course matching info to help with timetable generation.
    """
    from sqlalchemy import select

    from app.core.branch_extractor import extract_campus_id_info
    from app.models.course import Course
    from app.models.default_package import DefaultPackage

    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get student to extract year and branch from campus_id - use read-only
    async for session_db in get_session_db_read_only(session.schema_name):
        student = await get_student_by_id(session_db, student_id)
        if not student:
            raise HTTPException(status_code=404, detail="Student not found")

        campus_id = student.campus_id
        break

    # Extract year, branch, and program from campus_id using proper extractor
    # Single degree: 2024A3PS0309P -> year=2024, branch=A3, program=PS
    # Single degree: 2024A3RM0309P -> year=2024, branch=A3, program=RM
    # Dual degree: 2024B2A30309P -> year=2024, branch=B2A3, program=None
    campus_info = extract_campus_id_info(campus_id)
    if not campus_info:
        return {
            "packages": [],
            "student_year": None,
            "student_branch": None,
            "student_program": None,
        }

    student_year = campus_info.year
    student_branch = campus_info.branches
    student_program = campus_info.program  # PS, RM, CS, etc. or None for dual

    # Get all packages from global database
    packages_result = await db.execute(select(DefaultPackage))
    all_packages = packages_result.scalars().all()

    # Get all courses from session for matching - use read-only
    async for session_db in get_session_db_read_only(session.schema_name):
        courses_result = await session_db.execute(select(Course))
        session_courses = {
            f"{c.subject} {c.catalog}": c for c in courses_result.scalars().all()
        }
        break

    # Filter packages matching student's year, branch, and program
    matching_packages = []

    # Build list of branches/keys to match
    student_branch_upper = student_branch.upper()
    branches_to_match = {student_branch_upper}

    # For non-PS programs, also match:
    # - branch+program keys (e.g., A3_RM)
    # - ALL_{program} keys (e.g., ALL_RM for all branches with RM program)
    if student_program and student_program != "PS":
        branches_to_match.add(f"{student_branch_upper}_{student_program}")
        branches_to_match.add(f"ALL_{student_program}")

    # For A5/AJ students, also match their _PCB variants
    if student_branch_upper in ("A5", "AJ"):
        branches_to_match.add(f"{student_branch_upper}_PCB")
    # If student is A5_PCB or AJ_PCB, also match the base branch
    elif student_branch_upper in ("A5_PCB", "AJ_PCB"):
        branches_to_match.add(student_branch_upper.replace("_PCB", ""))

    for pkg in all_packages:
        # Check year match
        if pkg.year != student_year:
            continue

        # Check branch match (branch field may contain multiple like "A1, A2")
        pkg_branches = [b.strip().upper() for b in pkg.branch.split(",")]

        # Check if any of the student's branches to match are in the package branches
        if not any(b in pkg_branches for b in branches_to_match):
            continue

        # Build course info with match status
        course_info = []
        available_count = 0

        for code in pkg.course_codes:
            code_clean = code.strip().upper()
            # Try to match against session courses
            matched_course = session_courses.get(code_clean)

            if matched_course:
                available_count += 1
                course_info.append(
                    {
                        "code": code,
                        "available": True,
                        "course_id": str(matched_course.id),
                        "title": matched_course.title,
                        "units": float(matched_course.max_units)
                        if matched_course.max_units
                        else None,
                    }
                )
            else:
                course_info.append(
                    {
                        "code": code,
                        "available": False,
                        "course_id": None,
                        "title": None,
                        "units": None,
                    }
                )

        matching_packages.append(
            {
                "id": str(pkg.id),
                "year": pkg.year,
                "branch": pkg.branch,
                "total_courses": len(pkg.course_codes),
                "available_courses": available_count,
                "courses": course_info,
            }
        )

    return {
        "packages": matching_packages,
        "student_year": student_year,
        "student_branch": student_branch,
        "student_program": student_program,
    }
