"""Timetable API routes"""

import asyncio
import time as time_module
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.database import get_session_db, get_session_db_read_only
from app.models.user import User
from app.schemas.common import Message
from app.schemas.timetable import (
    AddCourseRequest,
    FindCompatibleSectionsRequest,
    FindCompatibleSectionsResponse,
    FindCompatibleSectionsWithConflictsResponse,
    MultiSwapRequest,
    MultiSwapResponse,
    RemoveCourseRequest,
    RevertToRegistrationResponse,
    SwapSectionRequest,
    TimetableCommitRequest,
    TimetableCommitResponse,
    TimetableEditResponse,
    TimetableGenerationRequest,
    TimetableGenerationResponse,
    TimetableResponse,
    TimetableTransferRequest,
    TimetableTransferResponse,
)
from app.services.export_service import export_transfer_timetables
from app.services.request_logger_service import OperationType, log_timetable_request
from app.services.session_service import get_session_by_id
from app.services.timetable_service import (
    add_course_to_timetable,
    commit_timetable,
    find_compatible_sections,
    find_compatible_sections_with_conflicts,
    get_timetable_by_student,
    get_timetable_courses,
    remove_course_from_timetable,
    revert_to_registration,
    swap_multiple_sections,
    swap_section_in_timetable,
    transfer_timetable,
    uncommit_timetable,
)

router = APIRouter(
    prefix="/api/sessions/{session_id}/students/{student_id}/timetable",
    tags=["timetables"],
)


@router.get("", response_model=TimetableResponse)
async def get_student_timetable(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get student's committed timetable"""
    from sqlalchemy import select as sa_select

    from app.models.course import Course

    # Get session
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Staff can access enabled sessions only (for modification)
    if current_user.role == "staff" and not session.is_enabled:
        # Allow read-only access for disabled sessions
        pass

    # Get session database - use read-only for better concurrency
    async for session_db in get_session_db_read_only(session.schema_name):
        from app.models.course_section import CourseSection

        timetable = await get_timetable_by_student(session_db, student_id)
        if not timetable:
            raise HTTPException(status_code=404, detail="No timetable found")

        # Collect course IDs and class_nbrs from sections
        course_ids = set()
        class_nbrs = set()
        for item in timetable.items:
            if item.course_section:
                course_ids.add(item.course_section.course_id)
                class_nbrs.add(item.course_section.class_nbr)

        # Fetch ALL meeting times for these class_nbrs
        sections_by_class_nbr: dict[int, list] = {}
        if class_nbrs:
            all_sections_result = await session_db.execute(
                sa_select(CourseSection).where(CourseSection.class_nbr.in_(class_nbrs))
            )
            for s in all_sections_result.scalars().all():
                if s.class_nbr not in sections_by_class_nbr:
                    sections_by_class_nbr[s.class_nbr] = []
                sections_by_class_nbr[s.class_nbr].append(s)

        # Fetch course information from global database
        courses_map = {}
        if course_ids:
            courses_result = await db.execute(
                sa_select(Course).where(Course.id.in_(course_ids))
            )
            for course in courses_result.scalars().all():
                courses_map[course.id] = course

        # Build response with course information
        response_data = {
            "id": timetable.id,
            "student_id": timetable.student_id,
            "source": timetable.source,
            "status": timetable.status,
            "created_at": timetable.created_at,
            "created_by_id": timetable.created_by_id,
            "total_units": timetable.total_units,
            "updated_at": timetable.updated_at,
            "updated_by_id": timetable.updated_by_id,
            "items": [],
        }

        for item in timetable.items:
            section = item.course_section
            course = courses_map.get(section.course_id) if section else None

            # Get all meeting times for this class_nbr
            all_meetings = sections_by_class_nbr.get(section.class_nbr, [section]) if section else []
            day_order = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6}
            all_meetings.sort(key=lambda s: (day_order.get(s.day, 99), str(s.mtg_start or "")))

            # Build meetings array
            meetings = []
            for mtg in all_meetings:
                meetings.append({
                    "day": mtg.day,
                    "mtg_start": mtg.mtg_start,
                    "mtg_end": mtg.mtg_end,
                    "room": mtg.room,
                    "instructor": mtg.instructor,
                })

            item_data = {
                "id": item.id,
                "course_section_id": item.course_section_id,
                "course_section": {
                    "id": section.id,
                    "course_id": section.course_id,
                    "class_nbr": section.class_nbr,
                    "section": section.section,
                    "component": section.component,
                    "day": section.day,
                    "class_pattern": section.class_pattern,
                    "mtg_start": section.mtg_start,
                    "mtg_end": section.mtg_end,
                    "exam_date": section.exam_date,
                    "exam_start": section.exam_start,
                    "exam_end": section.exam_end,
                    "instructor": section.instructor,
                    "room": section.room,
                    "cap_enrl": section.cap_enrl,
                    "tot_enrl": section.tot_enrl,
                    "meetings": meetings,  # All meeting times for this section
                    "course": {
                        "id": course.id,
                        "course_id": course.course_id,
                        "subject": course.subject,
                        "catalog": course.catalog,
                        "title": course.title,
                        "max_units": float(course.max_units)
                        if course.max_units
                        else None,
                    }
                    if course
                    else None,
                }
                if section
                else None,
            }
            response_data["items"].append(item_data)

        return response_data


@router.post("/generate", response_model=TimetableGenerationResponse)
async def generate_student_timetable(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    request: TimetableGenerationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Generate timetable preview for a student (does NOT commit).

    Returns a preview of the generated timetable with all meeting details.
    Use the /commit endpoint to actually save the timetable.

    Supports algorithm selection and generating multiple alternatives.
    """
    start_time = time_module.perf_counter()

    # Get session
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Only enabled sessions for staff
    if current_user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Cannot modify disabled session")

    # Extract student branch info for section filtering
    from app.core.branch_extractor import extract_branch_info
    from app.models.student import Student

    student_result = await db.execute(select(Student).where(Student.id == student_id))
    student = student_result.scalar_one_or_none()
    student_branches = None
    if student:
        branch_info = extract_branch_info(student.campus_id)
        student_branches = branch_info.get("branches")

    # Fetch all required data first, then close DB connection before CPU-intensive work
    # Use read-only session since generate is just reading data (no writes)
    sections_by_course = None
    async for session_db in get_session_db_read_only(session.schema_name):
        # Check if student already has a committed timetable
        from app.services.timetable_service import get_timetable_by_student

        existing_timetable = await get_timetable_by_student(session_db, student_id)
        if existing_timetable:
            return TimetableGenerationResponse(
                success=False,
                partial=False,
                meetings=[],
                conflicts=[
                    {
                        "type": "existing_timetable",
                        "message": "Student already has a committed timetable. Uncommit it first.",
                    }
                ],
                message="Cannot generate timetable - student already has one committed",
            )

        # Fetch all data we need before closing the session
        from app.services.timetable_service import fetch_sections_for_generation

        (
            sections_by_course,
            error_msg,
            courses_with_issues,
        ) = await fetch_sections_for_generation(
            db, session_db, request.course_ids, student_branches
        )

        # If there was an error fetching data, return detailed error
        if error_msg:
            return TimetableGenerationResponse(
                success=False,
                partial=False,
                meetings=[],
                conflicts=[
                    {
                        "type": "missing_sections",
                        "message": error_msg,
                        "courses": courses_with_issues,
                        "details": {
                            "total_courses_requested": len(request.course_ids),
                            "courses_with_issues": len(courses_with_issues),
                        },
                    }
                ],
                message=f"Cannot generate timetable: {error_msg}",
                validation_errors=[
                    f"Missing sections: {', '.join(courses_with_issues[:3])}"
                    + (" ..." if len(courses_with_issues) > 3 else "")
                ],
            )

    # Now DB session is closed, run CPU-intensive generation in thread pool
    # This prevents blocking the event loop and allows other requests to process
    from app.services.timetable_service import run_timetable_generation_v2

    # Build fixed sections and seat preferences from request
    fixed_sections = None
    if request.fixed_sections:
        fixed_sections = [
            {
                "course_id": fs.course_id,
                "component": fs.component,
                "class_nbr": fs.class_nbr,
            }
            for fs in request.fixed_sections
        ]

    seat_preferences = None
    if request.seat_preferences:
        seat_preferences = {
            "prefer_lab_seats": request.seat_preferences.prefer_lab_seats,
            "prefer_tut_seats": request.seat_preferences.prefer_tut_seats,
            "prefer_lec_seats": request.seat_preferences.prefer_lec_seats,
        }

    result = await asyncio.to_thread(
        run_timetable_generation_v2,
        sections_by_course,
        request.algorithm,
        request.generate_multiple,
        request.num_alternatives,
        request.max_units,
        fixed_sections,
        seat_preferences,
    )

    # Log the request
    duration_ms = (time_module.perf_counter() - start_time) * 1000
    log_timetable_request(
        session_id=session_id,
        student_id=student_id,
        operation=OperationType.GENERATE_TIMETABLE,
        endpoint="/generate",
        request_data={
            "course_ids": [str(cid) for cid in request.course_ids],
            "algorithm": request.algorithm,
            "generate_multiple": request.generate_multiple,
            "num_alternatives": request.num_alternatives,
            "max_units": request.max_units,
            "fixed_sections": [fs.model_dump() for fs in request.fixed_sections]
            if request.fixed_sections
            else None,
            "seat_preferences": request.seat_preferences.model_dump()
            if request.seat_preferences
            else None,
        },
        user_id=current_user.id,
        user_email=current_user.email,
        success=result.success,
        response_summary={
            "success": result.success,
            "partial": result.partial,
            "course_count": result.course_count,
            "total_units": result.total_units,
            "meeting_count": len(result.meetings) if result.meetings else 0,
            "conflict_count": len(result.conflicts) if result.conflicts else 0,
        },
        duration_ms=duration_ms,
    )

    return result


@router.post("/commit", response_model=TimetableCommitResponse)
async def commit_student_timetable(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    request: TimetableCommitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Commit a generated timetable for a student.

    Takes the section IDs from a generated timetable preview and commits them.
    This updates enrollment counts and creates the timetable record.
    """
    start_time = time_module.perf_counter()

    # Get session
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Only enabled sessions for staff
    if current_user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Cannot modify disabled session")

    # Get session database
    async for session_db in get_session_db(session.schema_name):
        try:
            timetable = await commit_timetable(
                session_db, student_id, request.section_ids, current_user.id
            )
            await session_db.commit()

            # Fetch fresh timetable with relationships
            timetable = await get_timetable_by_student(session_db, student_id)

            response = TimetableCommitResponse(
                success=True,
                timetable=TimetableResponse.model_validate(timetable)
                if timetable
                else None,
                message="Timetable committed successfully",
            )

            # Log the request
            duration_ms = (time_module.perf_counter() - start_time) * 1000
            log_timetable_request(
                session_id=session_id,
                student_id=student_id,
                operation=OperationType.COMMIT_TIMETABLE,
                endpoint="/commit",
                request_data={"section_ids": [str(sid) for sid in request.section_ids]},
                user_id=current_user.id,
                user_email=current_user.email,
                success=True,
                response_summary={"message": response.message},
                duration_ms=duration_ms,
            )

            return response
        except ValueError as e:
            await session_db.rollback()
            response = TimetableCommitResponse(
                success=False,
                timetable=None,
                message=str(e),
            )

            # Log the failed request
            duration_ms = (time_module.perf_counter() - start_time) * 1000
            log_timetable_request(
                session_id=session_id,
                student_id=student_id,
                operation=OperationType.COMMIT_TIMETABLE,
                endpoint="/commit",
                request_data={"section_ids": [str(sid) for sid in request.section_ids]},
                user_id=current_user.id,
                user_email=current_user.email,
                success=False,
                error=str(e),
                duration_ms=duration_ms,
            )

            return response
        except Exception as e:
            await session_db.rollback()
            response = TimetableCommitResponse(
                success=False,
                timetable=None,
                message=f"Failed to commit timetable: {str(e)}",
            )

            # Log the failed request
            duration_ms = (time_module.perf_counter() - start_time) * 1000
            log_timetable_request(
                session_id=session_id,
                student_id=student_id,
                operation=OperationType.COMMIT_TIMETABLE,
                endpoint="/commit",
                request_data={"section_ids": [str(sid) for sid in request.section_ids]},
                user_id=current_user.id,
                user_email=current_user.email,
                success=False,
                error=str(e),
                duration_ms=duration_ms,
            )

            return response


@router.post("/uncommit", response_model=Message)
async def uncommit_student_timetable(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Uncommit a student's timetable"""
    start_time = time_module.perf_counter()

    # Get session
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Only enabled sessions for staff
    if current_user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Cannot modify disabled session")

    # Get session database
    async for session_db in get_session_db(session.schema_name):
        success = await uncommit_timetable(
            session_db, student_id, changed_by_id=current_user.id
        )
        await session_db.commit()

        # Log the request
        duration_ms = (time_module.perf_counter() - start_time) * 1000
        log_timetable_request(
            session_id=session_id,
            student_id=student_id,
            operation=OperationType.UNCOMMIT_TIMETABLE,
            endpoint="/uncommit",
            request_data={},
            user_id=current_user.id,
            user_email=current_user.email,
            success=success,
            response_summary={"uncommitted": success},
            duration_ms=duration_ms,
        )

        if not success:
            raise HTTPException(status_code=404, detail="No timetable to uncommit")

        return Message(message="Timetable uncommitted successfully")


@router.post("/transfer", response_model=TimetableTransferResponse)
async def transfer_student_timetable(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    request: TimetableTransferRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Transfer a student's committed timetable to another student without a timetable."""
    start_time = time_module.perf_counter()

    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if current_user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Cannot modify disabled session")

    async for session_db in get_session_db(session.schema_name):
        try:
            _timetable, source_student, target_student, class_nbrs = (
                await transfer_timetable(
                    session_db,
                    source_student_id=student_id,
                    target_student_id=request.target_student_id,
                    changed_by_id=current_user.id,
                )
            )
            await session_db.commit()
        except ValueError as e:
            await session_db.rollback()
            duration_ms = (time_module.perf_counter() - start_time) * 1000
            log_timetable_request(
                session_id=session_id,
                student_id=student_id,
                operation=OperationType.TRANSFER_TIMETABLE,
                endpoint="/transfer",
                request_data={
                    "target_student_id": str(request.target_student_id),
                },
                user_id=current_user.id,
                user_email=current_user.email,
                success=False,
                error=str(e),
                duration_ms=duration_ms,
            )
            raise HTTPException(status_code=400, detail=str(e)) from e

        # Transfers ship their own ERP files here and are excluded from the session
        # export, so the same add/delete is never sent to ERP twice.
        _, _, zip_path, export_timestamp = export_transfer_timetables(
            source_campus_id=source_student.campus_id,
            target_campus_id=target_student.campus_id,
            class_nbrs=class_nbrs,
            term_code=session.term_code,
            career=session.career,
        )

        duration_ms = (time_module.perf_counter() - start_time) * 1000
        log_timetable_request(
            session_id=session_id,
            student_id=student_id,
            operation=OperationType.TRANSFER_TIMETABLE,
            endpoint="/transfer",
            request_data={
                "target_student_id": str(request.target_student_id),
                "class_nbrs": class_nbrs,
            },
            user_id=current_user.id,
            user_email=current_user.email,
            success=True,
            response_summary={
                "target_student_id": str(request.target_student_id),
                "export_timestamp": export_timestamp,
            },
            duration_ms=duration_ms,
        )

        return TimetableTransferResponse(
            message=(
                f"Timetable transferred from {source_student.name} "
                f"to {target_student.name}"
            ),
            target_student_id=request.target_student_id,
            source_campus_id=source_student.campus_id,
            target_campus_id=target_student.campus_id,
            export_timestamp=export_timestamp,
            delete_filename=f"{export_timestamp}_delete.xlsx",
            add_filename=f"{export_timestamp}_add.xlsx",
            zip_filename=zip_path.name,
        )


# ==================== Timetable Editing Endpoints ====================


@router.post("/compatible-sections", response_model=FindCompatibleSectionsResponse)
async def find_compatible_sections_for_course(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    request: FindCompatibleSectionsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Find all sections of a course that are compatible with the student's current timetable.

    This is useful for:
    - Adding a new course to an existing timetable
    - Swapping sections of an existing course

    Returns sections grouped by component type (LEC, TUT, LAB, etc.)
    """
    start_time = time_module.perf_counter()

    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Use read-only session for better concurrency
    async for session_db in get_session_db_read_only(session.schema_name):
        result = await find_compatible_sections(
            session_db,
            student_id,
            request.course_id,
            request.exclude_section_ids,
        )

        # Log the request
        duration_ms = (time_module.perf_counter() - start_time) * 1000
        log_timetable_request(
            session_id=session_id,
            student_id=student_id,
            operation=OperationType.FIND_COMPATIBLE_SECTIONS,
            endpoint="/compatible-sections",
            request_data={
                "course_id": str(request.course_id),
                "exclude_section_ids": [str(sid) for sid in request.exclude_section_ids]
                if request.exclude_section_ids
                else None,
            },
            user_id=current_user.id,
            user_email=current_user.email,
            success=True,
            response_summary={"components_count": len(result.get("components", []))},
            duration_ms=duration_ms,
        )

        return FindCompatibleSectionsResponse(**result)


@router.post(
    "/compatible-sections-with-conflicts",
    response_model=FindCompatibleSectionsWithConflictsResponse,
)
async def find_compatible_sections_with_conflicts_for_course(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    request: FindCompatibleSectionsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Find ALL sections of a course with compatibility status for multi-swap UI.

    Unlike the basic compatible-sections endpoint, this returns:
    - All sections including incompatible ones
    - Conflict reasons and which course causes the conflict
    - Current section IDs for each component

    Useful for the enhanced swap modal that shows all options with greyed-out conflicts.
    """
    start_time = time_module.perf_counter()

    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Use read-only session for better concurrency
    async for session_db in get_session_db_read_only(session.schema_name):
        result = await find_compatible_sections_with_conflicts(
            session_db,
            student_id,
            request.course_id,
            request.exclude_section_ids,
        )

        # Log the request
        duration_ms = (time_module.perf_counter() - start_time) * 1000
        log_timetable_request(
            session_id=session_id,
            student_id=student_id,
            operation=OperationType.FIND_COMPATIBLE_WITH_CONFLICTS,
            endpoint="/compatible-sections-with-conflicts",
            request_data={
                "course_id": str(request.course_id),
                "exclude_section_ids": [str(sid) for sid in request.exclude_section_ids]
                if request.exclude_section_ids
                else None,
            },
            user_id=current_user.id,
            user_email=current_user.email,
            success=True,
            response_summary={"components_count": len(result.get("components", []))},
            duration_ms=duration_ms,
        )

        return FindCompatibleSectionsWithConflictsResponse(**result)


@router.post("/remove-course", response_model=TimetableEditResponse)
async def remove_course_from_student_timetable(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    request: RemoveCourseRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Remove a course (all its sections) from the student's timetable.

    This decreases enrollment counts for the removed sections.
    """
    start_time = time_module.perf_counter()

    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if current_user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Cannot modify disabled session")

    async for session_db in get_session_db(session.schema_name):
        success, message, timetable = await remove_course_from_timetable(
            session_db, student_id, request.course_id, changed_by_id=current_user.id
        )

        if success:
            await session_db.commit()
            # Reload with relationships
            timetable = await get_timetable_by_student(
                session_db, student_id, include_all_statuses=False
            )

        # Log the request
        duration_ms = (time_module.perf_counter() - start_time) * 1000
        log_timetable_request(
            session_id=session_id,
            student_id=student_id,
            operation=OperationType.REMOVE_COURSE,
            endpoint="/remove-course",
            request_data={"course_id": str(request.course_id)},
            user_id=current_user.id,
            user_email=current_user.email,
            success=success,
            response_summary={"message": message},
            duration_ms=duration_ms,
        )

        return TimetableEditResponse(
            success=success,
            message=message,
            updated_timetable=TimetableResponse.model_validate(timetable)
            if timetable
            else None,
        )


@router.post("/add-course", response_model=TimetableEditResponse)
async def add_course_to_student_timetable(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    request: AddCourseRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Add a new course to the student's existing timetable.

    If section_ids is provided, those specific sections are used.
    Otherwise, the best compatible sections are auto-selected.
    """
    start_time = time_module.perf_counter()

    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if current_user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Cannot modify disabled session")

    async for session_db in get_session_db(session.schema_name):
        success, message, timetable, conflicts = await add_course_to_timetable(
            session_db,
            student_id,
            request.course_id,
            request.section_ids,
            current_user.id,
        )

        if success:
            await session_db.commit()
            # Reload with relationships
            timetable = await get_timetable_by_student(
                session_db, student_id, include_all_statuses=False
            )

        from app.schemas.timetable import TimetableConflictDetail

        # Log the request
        duration_ms = (time_module.perf_counter() - start_time) * 1000
        log_timetable_request(
            session_id=session_id,
            student_id=student_id,
            operation=OperationType.ADD_COURSE,
            endpoint="/add-course",
            request_data={
                "course_id": str(request.course_id),
                "section_ids": [str(sid) for sid in request.section_ids]
                if request.section_ids
                else None,
            },
            user_id=current_user.id,
            user_email=current_user.email,
            success=success,
            response_summary={"message": message, "conflict_count": len(conflicts)},
            duration_ms=duration_ms,
        )

        return TimetableEditResponse(
            success=success,
            message=message,
            updated_timetable=TimetableResponse.model_validate(timetable)
            if timetable
            else None,
            conflicts=[TimetableConflictDetail(**c) for c in conflicts],
        )


@router.post("/swap-section", response_model=TimetableEditResponse)
async def swap_section_in_student_timetable(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    request: SwapSectionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Swap one section (class) for another in the student's timetable.

    Swaps ALL meeting times of the old class with ALL meeting times of the new class.
    Both classes must be from the same course and same component type.
    The new class must be compatible with the rest of the schedule.
    """
    start_time = time_module.perf_counter()

    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if current_user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Cannot modify disabled session")

    async for session_db in get_session_db(session.schema_name):
        success, message, timetable = await swap_section_in_timetable(
            session_db,
            student_id,
            request.old_class_nbr,
            request.new_class_nbr,
            changed_by_id=current_user.id,
        )

        if success:
            await session_db.commit()
            # Reload with relationships
            timetable = await get_timetable_by_student(
                session_db, student_id, include_all_statuses=False
            )

        # Log the request
        duration_ms = (time_module.perf_counter() - start_time) * 1000
        log_timetable_request(
            session_id=session_id,
            student_id=student_id,
            operation=OperationType.SWAP_SECTION,
            endpoint="/swap-section",
            request_data={
                "old_class_nbr": request.old_class_nbr,
                "new_class_nbr": request.new_class_nbr,
            },
            user_id=current_user.id,
            user_email=current_user.email,
            success=success,
            response_summary={"message": message},
            duration_ms=duration_ms,
        )

        return TimetableEditResponse(
            success=success,
            message=message,
            updated_timetable=TimetableResponse.model_validate(timetable)
            if timetable
            else None,
        )


@router.post("/multi-swap", response_model=MultiSwapResponse)
async def multi_swap_sections_in_timetable(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    request: MultiSwapRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Swap multiple sections (classes) at once in the student's timetable.

    This endpoint allows swapping multiple classes of a course simultaneously,
    which is useful for courses with multiple components (LEC, TUT, LAB).

    Each swap exchanges ALL meeting times of the old class with ALL meeting times of the new class.

    All swaps must be for the same course. All swaps are validated first,
    then performed atomically - if any swap fails validation, none are applied.
    """
    start_time = time_module.perf_counter()

    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if current_user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Cannot modify disabled session")

    async for session_db in get_session_db(session.schema_name):
        try:
            # Convert request to list of tuples (class_nbr pairs)
            swaps = [(swap.old_class_nbr, swap.new_class_nbr) for swap in request.swaps]

            (
                success,
                message,
                completed,
                failed,
                timetable,
            ) = await swap_multiple_sections(
                session_db,
                student_id,
                swaps,
                changed_by_id=current_user.id,
            )

            if success:
                await session_db.commit()
                # Reload with relationships
                timetable = await get_timetable_by_student(
                    session_db, student_id, include_all_statuses=False
                )

            response = MultiSwapResponse(
                success=success,
                message=message,
                completed_swaps=completed,
                failed_swaps=failed,
                updated_timetable=TimetableResponse.model_validate(timetable)
                if timetable
                else None,
            )

            # Log the request
            duration_ms = (time_module.perf_counter() - start_time) * 1000
            log_timetable_request(
                session_id=session_id,
                student_id=student_id,
                operation=OperationType.MULTI_SWAP,
                endpoint="/multi-swap",
                request_data={
                    "swaps": [
                        {
                            "old_class_nbr": s.old_class_nbr,
                            "new_class_nbr": s.new_class_nbr,
                        }
                        for s in request.swaps
                    ]
                },
                user_id=current_user.id,
                user_email=current_user.email,
                success=success,
                response_summary={
                    "completed_swaps": completed,
                    "failed_swaps_count": len(failed),
                },
                duration_ms=duration_ms,
            )

            return response
        except Exception as e:
            await session_db.rollback()

            # Log the failed request
            duration_ms = (time_module.perf_counter() - start_time) * 1000
            log_timetable_request(
                session_id=session_id,
                student_id=student_id,
                operation=OperationType.MULTI_SWAP,
                endpoint="/multi-swap",
                request_data={
                    "swaps": [
                        {
                            "old_class_nbr": s.old_class_nbr,
                            "new_class_nbr": s.new_class_nbr,
                        }
                        for s in request.swaps
                    ]
                },
                user_id=current_user.id,
                user_email=current_user.email,
                success=False,
                error=str(e),
                duration_ms=duration_ms,
            )

            return MultiSwapResponse(
                success=False,
                message=f"Failed to swap sections: {str(e)}",
                completed_swaps=0,
                failed_swaps=[str(e)],
                updated_timetable=None,
            )


@router.get("/courses")
async def get_student_timetable_courses(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get list of courses in the student's timetable with their sections.

    Useful for displaying what courses are currently enrolled and which sections.
    """
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Use read-only session for better concurrency
    async for session_db in get_session_db_read_only(session.schema_name):
        courses = await get_timetable_courses(session_db, student_id)
        return {"courses": courses}


@router.post("/revert-to-registration", response_model=RevertToRegistrationResponse)
async def revert_to_registration_timetable(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Revert the student's timetable to their original registration.

    This restores the timetable to the exact state from the registration import,
    undoing any manual edits (added/removed courses, swapped sections).

    Only available if a registration timetable exists for this student.
    """
    start_time = time_module.perf_counter()

    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if current_user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Cannot modify disabled session")

    async for session_db in get_session_db(session.schema_name):
        try:
            success, message, restored_count, timetable = await revert_to_registration(
                session_db, student_id, changed_by_id=current_user.id
            )

            if success:
                await session_db.commit()
                # Reload with relationships
                timetable = await get_timetable_by_student(
                    session_db, student_id, include_all_statuses=False
                )

            response = RevertToRegistrationResponse(
                success=success,
                message=message,
                restored_section_count=restored_count,
                timetable=TimetableResponse.model_validate(timetable)
                if timetable
                else None,
            )

            # Log the request
            duration_ms = (time_module.perf_counter() - start_time) * 1000
            log_timetable_request(
                session_id=session_id,
                student_id=student_id,
                operation=OperationType.REVERT_TO_REGISTRATION,
                endpoint="/revert-to-registration",
                request_data={},
                user_id=current_user.id,
                user_email=current_user.email,
                success=success,
                response_summary={
                    "restored_section_count": restored_count,
                    "message": message,
                },
                duration_ms=duration_ms,
            )

            return response
        except Exception as e:
            await session_db.rollback()

            # Log the failed request
            duration_ms = (time_module.perf_counter() - start_time) * 1000
            log_timetable_request(
                session_id=session_id,
                student_id=student_id,
                operation=OperationType.REVERT_TO_REGISTRATION,
                endpoint="/revert-to-registration",
                request_data={},
                user_id=current_user.id,
                user_email=current_user.email,
                success=False,
                error=str(e),
                duration_ms=duration_ms,
            )

            return RevertToRegistrationResponse(
                success=False,
                message=f"Failed to revert timetable: {str(e)}",
                restored_section_count=0,
                timetable=None,
            )


@router.post("/assign-buffer")
async def assign_buffer_timetable(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    buffer_timetable_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Assign a buffer timetable to a student.

    This creates a new committed timetable for the student using the sections
    from the buffer timetable. Updates:
    - Creates Timetable with source='portal_generated', status='committed'
    - Creates TimetableItem records for each section
    - Calculates and sets total_units
    - Increments assigned_count on the BufferTimetable
    - If enrollment wasn't deducted on upload, increments tot_enrl for each section
    - Logs to audit trail

    Args:
        buffer_timetable_id: The ID of the buffer timetable to assign

    Returns:
        The newly created timetable with its items
    """
    from datetime import datetime, timezone

    from sqlalchemy import update

    from app.models.buffer_timetable import BufferTimetable, BufferTimetableItem
    from app.models.course import Course
    from app.models.course_section import CourseSection
    from app.models.student import Student
    from app.models.timetable import Timetable, TimetableItem
    from app.services import audit_trail_service

    start_time = time_module.perf_counter()

    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if current_user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Cannot modify disabled session")

    async for session_db in get_session_db(session.schema_name):
        try:
            # Verify student exists
            student_result = await session_db.execute(
                select(Student).where(Student.id == student_id)
            )
            student = student_result.scalar_one_or_none()
            if not student:
                raise HTTPException(status_code=404, detail="Student not found")

            # Get buffer timetable with lock
            bt_result = await session_db.execute(
                select(BufferTimetable)
                .where(BufferTimetable.id == buffer_timetable_id)
                .with_for_update()
            )
            buffer_tt = bt_result.scalar_one_or_none()

            if not buffer_tt:
                raise HTTPException(status_code=404, detail="Buffer timetable not found")

            if buffer_tt.is_full:
                raise HTTPException(
                    status_code=400,
                    detail=f"Buffer timetable is full ({buffer_tt.assigned_count}/{buffer_tt.batch_size})",
                )

            # Check if student already has a committed timetable
            existing_result = await session_db.execute(
                select(Timetable).where(
                    Timetable.student_id == student_id,
                    Timetable.status.in_(["committed", "edited"]),
                )
            )
            if existing_result.scalar_one_or_none():
                raise HTTPException(
                    status_code=400,
                    detail="Student already has a committed timetable. Please uncommit it first.",
                )

            # Get buffer timetable items
            items_result = await session_db.execute(
                select(BufferTimetableItem).where(
                    BufferTimetableItem.buffer_timetable_id == buffer_tt.id
                )
            )
            buffer_items = items_result.scalars().all()

            if not buffer_items:
                raise HTTPException(
                    status_code=400,
                    detail="Buffer timetable has no section items",
                )

            # Get course sections to find class_nbrs and calculate total units
            section_ids = [item.course_section_id for item in buffer_items]
            sections_result = await session_db.execute(
                select(CourseSection).where(CourseSection.id.in_(section_ids))
            )
            linked_sections = {s.id: s for s in sections_result.scalars().all()}

            # Get all class_nbrs from linked sections
            class_nbrs = list({s.class_nbr for s in linked_sections.values()})

            # Fetch ALL course_section rows for these class_nbrs (all meeting times)
            all_sections_result = await session_db.execute(
                select(CourseSection).where(CourseSection.class_nbr.in_(class_nbrs))
            )
            all_sections = list(all_sections_result.scalars().all())

            # Calculate total units (only count each course once)
            total_units = 0.0
            course_ids_seen: set[uuid.UUID] = set()
            for section in linked_sections.values():
                if section.course_id not in course_ids_seen:
                    # Get course from global db
                    course = await db.get(Course, section.course_id)
                    if course and course.max_units:
                        total_units += float(course.max_units)
                    course_ids_seen.add(section.course_id)

            # Create new timetable for student
            new_timetable = Timetable(
                student_id=student_id,
                source="portal_generated",
                status="committed",
                total_units=total_units,
                created_at=datetime.now(timezone.utc),
                created_by_id=current_user.id,
                buffer_timetable_id=buffer_tt.id,
            )
            session_db.add(new_timetable)
            await session_db.flush()

            # Create timetable items for ALL meeting times (not just linked ones)
            section_ids_to_update = []
            for section in all_sections:
                timetable_item = TimetableItem(
                    timetable_id=new_timetable.id,
                    course_section_id=section.id,
                )
                session_db.add(timetable_item)
                section_ids_to_update.append(section.id)

            # Increment assigned_count on buffer timetable
            buffer_tt.assigned_count += 1

            # Update enrollment counts if not already done on upload
            if not buffer_tt.enrollment_deducted_on_upload:
                for section_id in set(section_ids_to_update):
                    await session_db.execute(
                        update(CourseSection)
                        .where(CourseSection.id == section_id)
                        .values(tot_enrl=CourseSection.tot_enrl + 1)
                    )

            # Log to audit trail
            await audit_trail_service.log_commit_timetable(
                db=session_db,
                timetable_id=new_timetable.id,
                changed_by_id=current_user.id,
                section_ids=section_ids_to_update,
                total_units=total_units,
                note=f"Assigned from buffer timetable: {buffer_tt.plan} #{buffer_tt.timetable_id}",
            )

            await session_db.commit()

            # Reload timetable with relationships
            timetable = await get_timetable_by_student(session_db, student_id)

            # Log the request
            duration_ms = (time_module.perf_counter() - start_time) * 1000
            log_timetable_request(
                session_id=session_id,
                student_id=student_id,
                operation=OperationType.ASSIGN_BUFFER_TIMETABLE,
                endpoint="/assign-buffer",
                request_data={"buffer_timetable_id": str(buffer_timetable_id)},
                user_id=current_user.id,
                user_email=current_user.email,
                success=True,
                response_summary={
                    "timetable_id": str(new_timetable.id),
                    "items_count": len(buffer_items),
                    "total_units": total_units,
                    "buffer_timetable_plan": buffer_tt.plan,
                },
                duration_ms=duration_ms,
            )

            return {
                "success": True,
                "message": f"Successfully assigned buffer timetable #{buffer_tt.timetable_id} to student",
                "timetable": TimetableResponse.model_validate(timetable) if timetable else None,
                "buffer_timetable": {
                    "id": str(buffer_tt.id),
                    "plan": buffer_tt.plan,
                    "assigned_count": buffer_tt.assigned_count,
                    "remaining_capacity": buffer_tt.remaining_capacity,
                },
            }

        except HTTPException:
            await session_db.rollback()
            raise
        except Exception as e:
            await session_db.rollback()

            # Log the failed request
            duration_ms = (time_module.perf_counter() - start_time) * 1000
            log_timetable_request(
                session_id=session_id,
                student_id=student_id,
                operation=OperationType.ASSIGN_BUFFER_TIMETABLE,
                endpoint="/assign-buffer",
                request_data={"buffer_timetable_id": str(buffer_timetable_id)},
                user_id=current_user.id,
                user_email=current_user.email,
                success=False,
                error=str(e),
                duration_ms=duration_ms,
            )

            raise HTTPException(
                status_code=500,
                detail=f"Failed to assign buffer timetable: {str(e)}",
            )
