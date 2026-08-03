"""Advanced timetable generation API routes"""

import asyncio
import time as time_module
import uuid
from datetime import time

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.algorithms import AlgorithmRegistry, GenerationConstraints, get_algorithm
from app.core.algorithms.base import SectionData
from app.core.task_manager import TaskStatus, task_manager
from app.database import get_session_db_read_only
from app.models.course import Course
from app.models.course_section import CourseSection
from app.models.user import User
from app.schemas.advanced_generation import (
    AdvancedGenerationRequest,
    AdvancedGenerationResponse,
    AdvancedGenerationResult,
    AsyncGenerationResponse,
    CourseComponentInfo,
    CourseSectionsResponse,
    CourseWithSectionsInfo,
    GeneratedMeetingAdvanced,
    ListAlgorithmsResponse,
    SectionOptionInfo,
    TaskStatusResponse,
)
from app.services.request_logger_service import OperationType, log_timetable_request
from app.services.session_service import get_session_by_id

router = APIRouter(
    prefix="/api/sessions/{session_id}/students/{student_id}/advanced-generate",
    tags=["advanced-generation"],
)


@router.get("/algorithms", response_model=ListAlgorithmsResponse)
async def list_algorithms(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
):
    """List available timetable generation algorithms"""
    algorithms = AlgorithmRegistry.list_algorithms()
    return ListAlgorithmsResponse(algorithms=algorithms)


@router.get("/course-sections", response_model=CourseSectionsResponse)
async def get_course_sections_for_selection(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    course_ids: str,  # Comma-separated UUIDs
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get detailed section information for courses.

    This endpoint provides all sections grouped by component for the
    advanced UI where users can pin specific sections.
    Sections are filtered based on student's branch restrictions.
    """
    from app.core.branch_extractor import extract_branch_info
    from app.core.section_restrictions import is_section_allowed_for_branch
    from app.models.student import Student

    # Parse course IDs
    try:
        parsed_ids = [
            uuid.UUID(cid.strip()) for cid in course_ids.split(",") if cid.strip()
        ]
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid course ID format")

    if not parsed_ids:
        raise HTTPException(status_code=400, detail="No course IDs provided")

    # Get session
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Extract student branch info for section filtering
    student_result = await db.execute(select(Student).where(Student.id == student_id))
    student = student_result.scalar_one_or_none()
    student_branches = None
    if student:
        branch_info = extract_branch_info(student.campus_id)
        student_branches = branch_info.get("branches")

    # Fetch courses from global DB
    courses_result = await db.execute(select(Course).where(Course.id.in_(parsed_ids)))
    courses_map = {c.id: c for c in courses_result.scalars().all()}

    result_courses: list[CourseWithSectionsInfo] = []

    # Use read-only session for sections
    async for session_db in get_session_db_read_only(session.schema_name):
        for course_id in parsed_ids:
            course = courses_map.get(course_id)
            if not course:
                continue

            # Fetch all sections for this course
            sections_result = await session_db.execute(
                select(CourseSection).where(CourseSection.course_id == course_id)
            )
            sections = list(sections_result.scalars().all())

            # Filter sections based on branch restrictions if student_branches provided
            if student_branches:
                sections = [
                    s
                    for s in sections
                    if is_section_allowed_for_branch(
                        section_name=s.section,
                        branches=student_branches,
                        component=s.component,
                    )
                ]

            # Group by component, then by class_nbr
            by_component: dict[str, dict[int, list[CourseSection]]] = {}
            for sec in sections:
                if sec.component not in by_component:
                    by_component[sec.component] = {}
                if sec.class_nbr not in by_component[sec.component]:
                    by_component[sec.component][sec.class_nbr] = []
                by_component[sec.component][sec.class_nbr].append(sec)

            # Build response
            components: list[CourseComponentInfo] = []
            for comp_name, classes in by_component.items():
                section_options: list[SectionOptionInfo] = []

                for class_nbr, meetings in classes.items():
                    rep = meetings[0]  # Representative meeting
                    cap = rep.cap_enrl or 0
                    tot = rep.tot_enrl or 0
                    available = max(0, cap - tot)

                    # Build meeting times summary
                    meeting_times = (
                        ", ".join(
                            f"{m.day} {m.mtg_start.strftime('%H:%M') if m.mtg_start else 'TBA'}-{m.mtg_end.strftime('%H:%M') if m.mtg_end else 'TBA'}"
                            for m in sorted(
                                meetings,
                                key=lambda x: (x.day or "", x.mtg_start or time(0, 0)),
                            )
                            if m.day
                        )
                        or "TBA"
                    )

                    section_options.append(
                        SectionOptionInfo(
                            class_nbr=class_nbr,
                            section=rep.section,
                            instructor=rep.instructor,
                            room=rep.room,
                            meeting_times=meeting_times,
                            exam_date=rep.exam_date,
                            exam_start=rep.exam_start,
                            exam_end=rep.exam_end,
                            cap_enrl=cap,
                            tot_enrl=tot,
                            available_seats=available,
                            seat_score=available,
                        )
                    )

                # Sort by available seats descending
                section_options.sort(key=lambda x: x.available_seats, reverse=True)
                components.append(
                    CourseComponentInfo(
                        component=comp_name,
                        sections=section_options,
                    )
                )

            result_courses.append(
                CourseWithSectionsInfo(
                    course_id=course_id,
                    subject=course.subject,
                    catalog=course.catalog,
                    title=course.title,
                    max_units=float(course.max_units or 0),
                    components=components,
                )
            )

    return CourseSectionsResponse(
        success=True,
        courses=result_courses,
        message=f"Found sections for {len(result_courses)} course(s)",
    )


@router.post("", response_model=AdvancedGenerationResponse | AsyncGenerationResponse)
async def advanced_generate_timetable(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    request: AdvancedGenerationRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Generate timetable with advanced options.

    Supports:
    - Multiple algorithms (greedy, backtrack, backtrack_optimized, genetic)
    - Fixed section constraints (pin specific sections)
    - Blocked time slots
    - Async mode (background processing)
    """
    start_time = time_module.perf_counter()

    # Get session
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Staff check
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

    # Build constraints
    fixed_sections: dict[uuid.UUID, dict[str, int]] = {}
    for fix in request.fixed_sections:
        if fix.course_id not in fixed_sections:
            fixed_sections[fix.course_id] = {}
        fixed_sections[fix.course_id][fix.component] = fix.class_nbr

    blocked_slots = [
        (slot.day, slot.start_time, slot.end_time) for slot in request.blocked_slots
    ]

    constraints = GenerationConstraints(
        max_units=request.max_units,
        fixed_sections=fixed_sections,
        blocked_slots=blocked_slots,
    )

    # Fetch section data
    sections_by_course = await _fetch_sections_for_generation(
        db, session.schema_name, request.course_ids, student_branches
    )

    if not sections_by_course:
        return AdvancedGenerationResponse(
            success=False,
            message="No valid sections found for the selected courses",
        )

    # Async mode - create task and run in background
    if request.async_mode:
        task = await task_manager.create_task(
            session_id=session_id,
            student_id=student_id,
            course_ids=request.course_ids,
            algorithm=request.algorithm,
            constraints={
                "max_units": request.max_units,
                "fixed_sections": [f.model_dump() for f in request.fixed_sections],
                "blocked_slots": [s.model_dump() for s in request.blocked_slots],
            },
        )

        # Schedule background task
        background_tasks.add_task(
            _run_generation_task,
            task.id,
            sections_by_course,
            request.algorithm,
            constraints,
        )

        # Log the async request
        duration_ms = (time_module.perf_counter() - start_time) * 1000
        log_timetable_request(
            session_id=session_id,
            student_id=student_id,
            operation=OperationType.ADVANCED_GENERATE,
            endpoint="/advanced-generate",
            request_data={
                "course_ids": [str(cid) for cid in request.course_ids],
                "algorithm": request.algorithm,
                "max_units": request.max_units,
                "fixed_sections": [f.model_dump() for f in request.fixed_sections],
                "blocked_slots": [s.model_dump() for s in request.blocked_slots],
                "async_mode": True,
            },
            user_id=current_user.id,
            user_email=current_user.email,
            success=True,
            response_summary={"task_id": task.id, "mode": "async"},
            duration_ms=duration_ms,
        )

        return AsyncGenerationResponse(
            task_id=task.id,
            status=TaskStatus.PENDING.value,
            message="Generation task created. Poll /task/{task_id} for status.",
        )

    # Sync mode - run immediately in thread pool to avoid blocking
    try:
        algorithm = get_algorithm(request.algorithm, constraints)
        result = await asyncio.to_thread(algorithm.generate, sections_by_course)

        # Convert to response format
        meetings = _convert_to_meetings(result)

        response = AdvancedGenerationResponse(
            success=result.success or result.partial,
            result=AdvancedGenerationResult(
                success=result.success,
                partial=result.partial,
                meetings=meetings,
                section_ids=result.section_ids,
                conflicts=result.conflicts,
                total_units=result.total_units,
                course_count=result.course_count,
                algorithm_name=result.algorithm_name,
                execution_time_ms=result.execution_time_ms,
                iterations=result.iterations,
                message=result.message,
            ),
            message=result.message,
        )

        # Log the sync request
        duration_ms = (time_module.perf_counter() - start_time) * 1000
        log_timetable_request(
            session_id=session_id,
            student_id=student_id,
            operation=OperationType.ADVANCED_GENERATE,
            endpoint="/advanced-generate",
            request_data={
                "course_ids": [str(cid) for cid in request.course_ids],
                "algorithm": request.algorithm,
                "max_units": request.max_units,
                "fixed_sections": [f.model_dump() for f in request.fixed_sections],
                "blocked_slots": [s.model_dump() for s in request.blocked_slots],
                "async_mode": False,
            },
            user_id=current_user.id,
            user_email=current_user.email,
            success=result.success or result.partial,
            response_summary={
                "mode": "sync",
                "success": result.success,
                "partial": result.partial,
                "course_count": result.course_count,
                "total_units": result.total_units,
                "algorithm_name": result.algorithm_name,
                "execution_time_ms": result.execution_time_ms,
            },
            duration_ms=duration_ms,
        )

        return response
    except Exception as e:
        # Log the failed request
        duration_ms = (time_module.perf_counter() - start_time) * 1000
        log_timetable_request(
            session_id=session_id,
            student_id=student_id,
            operation=OperationType.ADVANCED_GENERATE,
            endpoint="/advanced-generate",
            request_data={
                "course_ids": [str(cid) for cid in request.course_ids],
                "algorithm": request.algorithm,
                "max_units": request.max_units,
                "fixed_sections": [f.model_dump() for f in request.fixed_sections],
                "blocked_slots": [s.model_dump() for s in request.blocked_slots],
                "async_mode": False,
            },
            user_id=current_user.id,
            user_email=current_user.email,
            success=False,
            error=str(e),
            duration_ms=duration_ms,
        )

        return AdvancedGenerationResponse(
            success=False,
            message=f"Generation failed: {str(e)}",
        )


@router.get("/task/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get status of a background generation task"""
    task = await task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Verify task belongs to this session/student
    if task.session_id != session_id or task.student_id != student_id:
        raise HTTPException(
            status_code=403, detail="Task does not belong to this context"
        )

    task_dict = task.to_dict()

    # Convert result to proper schema if present
    result = None
    if task_dict.get("result"):
        r = task_dict["result"]
        result = AdvancedGenerationResult(
            success=r.get("success", False),
            partial=r.get("partial", False),
            meetings=[GeneratedMeetingAdvanced(**m) for m in r.get("meetings", [])],
            section_ids=[uuid.UUID(sid) for sid in r.get("section_ids", [])],
            conflicts=r.get("conflicts", []),
            total_units=r.get("total_units", 0),
            course_count=r.get("course_count", 0),
            algorithm_name=r.get("algorithm_name", ""),
            execution_time_ms=r.get("execution_time_ms", 0),
            iterations=r.get("iterations", 0),
            message=r.get("message", ""),
        )

    return TaskStatusResponse(
        id=task_dict["id"],
        session_id=task_dict["session_id"],
        student_id=task_dict["student_id"],
        algorithm=task_dict["algorithm"],
        status=task_dict["status"],
        progress=task_dict["progress"],
        message=task_dict["message"],
        created_at=task_dict["created_at"],
        started_at=task_dict["started_at"],
        completed_at=task_dict["completed_at"],
        result=result,
        error=task_dict["error"],
    )


@router.post("/task/{task_id}/cancel")
async def cancel_task(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """Cancel a background generation task"""
    task = await task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.session_id != session_id or task.student_id != student_id:
        raise HTTPException(
            status_code=403, detail="Task does not belong to this context"
        )

    success = await task_manager.cancel_task(task_id)
    if success:
        return {"success": True, "message": "Task cancelled"}
    else:
        return {
            "success": False,
            "message": "Task cannot be cancelled (already completed or failed)",
        }


@router.post("/compare")
async def compare_algorithms(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    request: AdvancedGenerationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Run multiple algorithms and compare results.

    Useful for benchmarking and finding the best solution.
    """
    start_time = time_module.perf_counter()

    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Extract student branch info for section filtering
    from app.core.branch_extractor import extract_branch_info
    from app.models.student import Student

    student_result = await db.execute(select(Student).where(Student.id == student_id))
    student = student_result.scalar_one_or_none()
    student_branches = None
    if student:
        branch_info = extract_branch_info(student.campus_id)
        student_branches = branch_info.get("branches")

    # Build constraints
    fixed_sections: dict[uuid.UUID, dict[str, int]] = {}
    for fix in request.fixed_sections:
        if fix.course_id not in fixed_sections:
            fixed_sections[fix.course_id] = {}
        fixed_sections[fix.course_id][fix.component] = fix.class_nbr

    blocked_slots = [
        (slot.day, slot.start_time, slot.end_time) for slot in request.blocked_slots
    ]

    constraints = GenerationConstraints(
        max_units=request.max_units,
        fixed_sections=fixed_sections,
        blocked_slots=blocked_slots,
    )

    # Fetch sections
    sections_by_course = await _fetch_sections_for_generation(
        db, session.schema_name, request.course_ids, student_branches
    )

    if not sections_by_course:
        return {"success": False, "message": "No valid sections found"}

    # Run all algorithms in parallel using thread pool
    algorithms = ["greedy", "backtrack", "backtrack_optimized", "genetic"]
    results = []

    async def run_algorithm(algo_name: str):
        """Run a single algorithm in thread pool"""
        try:
            algo = get_algorithm(algo_name, constraints)
            result = await asyncio.to_thread(algo.generate, sections_by_course)

            meetings = _convert_to_meetings(result)

            return {
                "algorithm": algo_name,
                "success": result.success,
                "partial": result.partial,
                "course_count": result.course_count,
                "total_units": result.total_units,
                "execution_time_ms": result.execution_time_ms,
                "iterations": result.iterations,
                "conflicts_count": len(result.conflicts),
                "message": result.message,
                "meetings": meetings,
                "section_ids": [str(sid) for sid in result.section_ids],
            }
        except Exception as e:
            return {
                "algorithm": algo_name,
                "success": False,
                "error": str(e),
            }

    # Run all algorithms in parallel
    results = list(await asyncio.gather(*[run_algorithm(name) for name in algorithms]))

    # Sort by success, then course count, then execution time
    results.sort(
        key=lambda r: (
            not r.get("success", False),
            -r.get("course_count", 0),
            r.get("execution_time_ms", float("inf")),
        )
    )

    best_algorithm = results[0]["algorithm"] if results else None

    # Log the request
    duration_ms = (time_module.perf_counter() - start_time) * 1000
    log_timetable_request(
        session_id=session_id,
        student_id=student_id,
        operation=OperationType.COMPARE_ALGORITHMS,
        endpoint="/advanced-generate/compare",
        request_data={
            "course_ids": [str(cid) for cid in request.course_ids],
            "max_units": request.max_units,
            "fixed_sections": [f.model_dump() for f in request.fixed_sections],
            "blocked_slots": [s.model_dump() for s in request.blocked_slots],
        },
        user_id=current_user.id,
        user_email=current_user.email,
        success=True,
        response_summary={
            "algorithms_compared": len(algorithms),
            "best_algorithm": best_algorithm,
            "results_count": len(results),
        },
        duration_ms=duration_ms,
    )

    return {
        "success": True,
        "results": results,
        "best_algorithm": best_algorithm,
    }


# ==================== Helper Functions ====================


async def _fetch_sections_for_generation(
    db: AsyncSession,
    schema_name: str,
    course_ids: list[uuid.UUID],
    student_branches: list[str] | None = None,
) -> dict[uuid.UUID, list[SectionData]]:
    """Fetch all section data needed for generation

    Args:
        db: Database session for global database
        schema_name: Session schema name
        course_ids: List of course IDs to fetch sections for
        student_branches: Optional list of student branch codes for filtering
    """
    from app.core.section_restrictions import is_section_allowed_for_branch

    # Fetch courses from global DB
    courses_result = await db.execute(select(Course).where(Course.id.in_(course_ids)))
    courses_map = {c.id: c for c in courses_result.scalars().all()}

    sections_by_course: dict[uuid.UUID, list[SectionData]] = {}

    async for session_db in get_session_db_read_only(schema_name):
        for course_id in course_ids:
            course = courses_map.get(course_id)
            if not course:
                continue

            sections_result = await session_db.execute(
                select(CourseSection).where(CourseSection.course_id == course_id)
            )
            sections = list(sections_result.scalars().all())

            if not sections:
                continue

            # Filter sections based on branch restrictions if student_branches provided
            if student_branches:
                sections = [
                    s
                    for s in sections
                    if is_section_allowed_for_branch(
                        section_name=s.section,
                        branches=student_branches,
                        component=s.component,
                    )
                ]

                if not sections:
                    continue

            sections_by_course[course_id] = [
                SectionData(
                    id=s.id,
                    course_id=s.course_id,
                    class_nbr=s.class_nbr,
                    section=s.section,
                    component=s.component,
                    day=s.day,
                    mtg_start=s.mtg_start,
                    mtg_end=s.mtg_end,
                    exam_date=s.exam_date,
                    exam_start=s.exam_start,
                    exam_end=s.exam_end,
                    instructor=s.instructor,
                    room=s.room,
                    cap_enrl=s.cap_enrl or 0,
                    tot_enrl=s.tot_enrl or 0,
                    subject=course.subject,
                    catalog=course.catalog,
                    title=course.title,
                    max_units=float(course.max_units or 0),
                )
                for s in sections
            ]

    return sections_by_course


def _convert_to_meetings(result) -> list[GeneratedMeetingAdvanced]:
    """Convert algorithm result to meeting list"""
    meetings = []
    seen_ids = set()

    for sec in result.selected_sections:
        for m in sec.meetings:
            if m.id in seen_ids:
                continue
            seen_ids.add(m.id)

            meetings.append(
                GeneratedMeetingAdvanced(
                    section_id=m.id,
                    course_id=sec.course_id,
                    class_nbr=sec.class_nbr,
                    subject=sec.subject,
                    catalog=sec.catalog,
                    title=sec.title,
                    component=sec.component,
                    section=sec.section,
                    day=m.day,
                    mtg_start=m.mtg_start,
                    mtg_end=m.mtg_end,
                    exam_date=m.exam_date,
                    exam_start=m.exam_start,
                    exam_end=m.exam_end,
                    instructor=m.instructor,
                    room=m.room,
                    cap_enrl=m.cap_enrl,
                    tot_enrl=m.tot_enrl,
                    max_units=sec.max_units,
                )
            )

    return meetings


async def _run_generation_task(
    task_id: str,
    sections_by_course: dict[uuid.UUID, list[SectionData]],
    algorithm_name: str,
    constraints: GenerationConstraints,
):
    """Background task runner for generation"""
    try:
        await task_manager.update_task(
            task_id,
            status=TaskStatus.RUNNING,
            progress=0.1,
            message="Starting generation...",
        )
        await task_manager.mark_running(task_id)

        # Small delay to allow HTTP response to complete
        await asyncio.sleep(0.1)

        await task_manager.update_task(
            task_id,
            progress=0.3,
            message=f"Running {algorithm_name} algorithm...",
        )

        # Run algorithm (CPU-bound, but we're already async)
        algorithm = get_algorithm(algorithm_name, constraints)
        result = algorithm.generate(sections_by_course)

        await task_manager.update_task(
            task_id,
            status=TaskStatus.COMPLETED,
            progress=1.0,
            message=result.message,
            result=result,
        )

    except Exception as e:
        await task_manager.update_task(
            task_id,
            status=TaskStatus.FAILED,
            progress=1.0,
            message=f"Generation failed: {str(e)}",
            error=str(e),
        )
