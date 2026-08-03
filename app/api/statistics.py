"""Statistics API routes"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.database import get_session_db_read_only
from app.models.course import Course
from app.models.course_section import CourseSection
from app.models.student import Student
from app.models.timetable import ACTIVE_TIMETABLE_STATUSES, Timetable
from app.models.user import User
from app.schemas.statistics import CourseStatistics, EnrollmentStatistics
from app.services.course_service import get_course_by_id
from app.services.session_service import get_session_by_id

router = APIRouter(prefix="/api/sessions/{session_id}/statistics", tags=["statistics"])


class SessionStats(BaseModel):
    """Session statistics"""

    total_students: int = 0
    total_courses: int = 0
    total_sections: int = 0
    total_timetables: int = 0
    students_with_timetables: int = 0


@router.get("/summary", response_model=SessionStats)
async def get_session_stats(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get session summary statistics including timetable counts"""
    # Get session
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get session database - use read-only for better concurrency
    async for session_db in get_session_db_read_only(session.schema_name):
        # Get student count
        student_count_result = await session_db.execute(select(func.count(Student.id)))
        total_students = student_count_result.scalar() or 0

        # Get section count and distinct courses
        section_query = select(
            func.count(CourseSection.id).label("total_sections"),
            func.count(func.distinct(CourseSection.course_id)).label("total_courses"),
        )
        section_result = await session_db.execute(section_query)
        section_row = section_result.one()
        total_sections = section_row.total_sections or 0
        total_courses = section_row.total_courses or 0

        # Get live timetable count (committed or edited)
        timetable_count_result = await session_db.execute(
            select(func.count(Timetable.id)).where(
                cast(Timetable.status, String).in_(ACTIVE_TIMETABLE_STATUSES)
            )
        )
        total_timetables = timetable_count_result.scalar() or 0

        # Get count of students with live timetables
        students_with_tt_result = await session_db.execute(
            select(func.count(func.distinct(Timetable.student_id))).where(
                cast(Timetable.status, String).in_(ACTIVE_TIMETABLE_STATUSES)
            )
        )
        students_with_timetables = students_with_tt_result.scalar() or 0

        return SessionStats(
            total_students=total_students,
            total_courses=total_courses,
            total_sections=total_sections,
            total_timetables=total_timetables,
            students_with_timetables=students_with_timetables,
        )


@router.get("/courses", response_model=list[CourseStatistics])
async def get_course_statistics(
    session_id: uuid.UUID,
    overfilled_only: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get course-wise enrollment statistics"""
    # Get session
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get session database - use read-only for better concurrency
    async for session_db in get_session_db_read_only(session.schema_name):
        # Query to get aggregated stats per course
        query = select(
            CourseSection.course_id,
            func.count(CourseSection.id).label("total_sections"),
            func.sum(CourseSection.cap_enrl).label("total_capacity"),
            func.sum(CourseSection.tot_enrl).label("total_enrolled"),
        ).group_by(CourseSection.course_id)

        result = await session_db.execute(query)
        rows = result.all()

        # Build statistics
        stats = []
        for row in rows:
            course = await get_course_by_id(db, row.course_id)
            if not course:
                continue

            total_capacity = row.total_capacity or 0
            total_enrolled = row.total_enrolled or 0
            available = max(0, total_capacity - total_enrolled)
            enrollment_pct = (
                (total_enrolled / total_capacity * 100) if total_capacity > 0 else 0
            )
            is_overfilled = total_enrolled > total_capacity

            # Filter if needed
            if overfilled_only and not is_overfilled:
                continue

            stats.append(
                CourseStatistics(
                    course_id=course.course_id,
                    subject=course.subject,
                    catalog=course.catalog,
                    title=course.title,
                    total_sections=row.total_sections,
                    total_capacity=total_capacity,
                    total_enrolled=total_enrolled,
                    available_seats=available,
                    enrollment_percentage=enrollment_pct,
                    is_overfilled=is_overfilled,
                )
            )

        return stats


@router.get("/enrollment", response_model=EnrollmentStatistics)
async def get_enrollment_statistics(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get overall enrollment statistics"""
    # Get session
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get session database - use read-only for better concurrency
    async for session_db in get_session_db_read_only(session.schema_name):
        # Get totals
        query = select(
            func.count(func.distinct(CourseSection.course_id)).label("total_courses"),
            func.count(CourseSection.id).label("total_sections"),
            func.sum(CourseSection.cap_enrl).label("total_capacity"),
            func.sum(CourseSection.tot_enrl).label("total_enrolled"),
        )

        result = await session_db.execute(query)
        row = result.one()

        total_capacity = row.total_capacity or 0
        total_enrolled = row.total_enrolled or 0
        avg_enrollment = (
            (total_enrolled / total_capacity * 100) if total_capacity > 0 else 0
        )

        # Count overfilled sections
        overfilled_query = select(func.count()).select_from(
            select(CourseSection)
            .where(CourseSection.tot_enrl > CourseSection.cap_enrl)
            .subquery()
        )
        overfilled_result = await session_db.execute(overfilled_query)
        overfilled_count = overfilled_result.scalar() or 0

        return EnrollmentStatistics(
            total_courses=row.total_courses or 0,
            total_sections=row.total_sections or 0,
            total_capacity=total_capacity,
            total_enrolled=total_enrolled,
            average_enrollment_percentage=avg_enrollment,
            overfilled_sections=overfilled_count,
        )


class SectionStatItem(BaseModel):
    """Section statistics item - grouped by class_nbr"""

    class_nbr: int
    course_id: uuid.UUID
    subject: str
    catalog: str
    title: str
    section: str
    component: str
    instructor: str | None
    meeting_days: list[str]  # List of days this section meets
    meeting_times: list[str]  # Formatted meeting times
    cap_enrl: int
    tot_enrl: int
    available: int
    enrollment_pct: float
    status: str  # 'overfilled', 'full', 'available'


class SectionStatsResponse(BaseModel):
    """Response for section statistics endpoint"""

    sections: list[SectionStatItem]
    summary: dict
    subjects: list[str]  # Available subjects for filtering


@router.get("/sections")
async def get_section_statistics(
    session_id: uuid.UUID,
    search: str | None = Query(None, description="Search by course code or title"),
    subject: str | None = Query(None, description="Filter by subject"),
    component: str | None = Query(None, description="Filter by component (LEC, TUT, LAB, PRO)"),
    status: str | None = Query(None, description="Filter by status (overfilled, full, available)"),
    sort_by: str = Query("enrollment_pct", description="Sort by: enrollment_pct, course, section, enrolled, capacity"),
    sort_order: str = Query("desc", description="Sort order: asc, desc"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SectionStatsResponse:
    """
    Get section-level enrollment statistics, properly grouped by class_nbr.

    Each class_nbr represents a unique section (even if it has multiple meeting times).
    This endpoint deduplicates by class_nbr and aggregates meeting times.
    """
    # Get session
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get all courses for lookup
    courses_result = await db.execute(select(Course))
    courses = {c.id: c for c in courses_result.scalars().all()}

    async for session_db in get_session_db_read_only(session.schema_name):
        # Get all sections
        sections_result = await session_db.execute(select(CourseSection))
        all_sections = list(sections_result.scalars().all())

        # Group by class_nbr to deduplicate meeting times
        sections_by_class_nbr: dict[int, list] = {}
        for section in all_sections:
            if section.class_nbr not in sections_by_class_nbr:
                sections_by_class_nbr[section.class_nbr] = []
            sections_by_class_nbr[section.class_nbr].append(section)

        # Build stats items
        stats_items: list[SectionStatItem] = []
        subjects_set: set[str] = set()
        total_enrolled = 0
        total_capacity = 0
        overfilled_count = 0

        for class_nbr, section_rows in sections_by_class_nbr.items():
            # Use first row for common data (all rows have same class_nbr)
            first_row = section_rows[0]
            course = courses.get(first_row.course_id)
            if not course:
                continue

            subjects_set.add(course.subject)

            # Collect meeting days and times
            meeting_days = []
            meeting_times = []
            for row in section_rows:
                if row.day:
                    meeting_days.append(row.day)
                    if row.mtg_start and row.mtg_end:
                        time_str = f"{row.day} {row.mtg_start.strftime('%H:%M')}-{row.mtg_end.strftime('%H:%M')}"
                        meeting_times.append(time_str)

            # Calculate enrollment stats (use first row - all have same values)
            cap = first_row.cap_enrl or 0
            enrl = first_row.tot_enrl or 0
            avail = max(0, cap - enrl)
            pct = (enrl / cap * 100) if cap > 0 else 0

            if enrl > cap:
                stat_status = "overfilled"
                overfilled_count += 1
            elif enrl == cap:
                stat_status = "full"
            else:
                stat_status = "available"

            total_enrolled += enrl
            total_capacity += cap

            # Apply filters
            course_code = f"{course.subject} {course.catalog}".lower()
            if search:
                search_lower = search.lower()
                if search_lower not in course_code and search_lower not in course.title.lower():
                    continue

            if subject and course.subject != subject:
                continue

            if component and first_row.component != component:
                continue

            if status and stat_status != status:
                continue

            stats_items.append(
                SectionStatItem(
                    class_nbr=class_nbr,
                    course_id=first_row.course_id,
                    subject=course.subject,
                    catalog=course.catalog,
                    title=course.title,
                    section=first_row.section,
                    component=first_row.component,
                    instructor=first_row.instructor,
                    meeting_days=meeting_days,
                    meeting_times=meeting_times,
                    cap_enrl=cap,
                    tot_enrl=enrl,
                    available=avail,
                    enrollment_pct=pct,
                    status=stat_status,
                )
            )

        # Sort
        reverse = sort_order == "desc"
        if sort_by == "enrollment_pct":
            stats_items.sort(key=lambda x: x.enrollment_pct, reverse=reverse)
        elif sort_by == "course":
            stats_items.sort(key=lambda x: (x.subject, x.catalog, x.section), reverse=reverse)
        elif sort_by == "section":
            stats_items.sort(key=lambda x: x.section, reverse=reverse)
        elif sort_by == "enrolled":
            stats_items.sort(key=lambda x: x.tot_enrl, reverse=reverse)
        elif sort_by == "capacity":
            stats_items.sort(key=lambda x: x.cap_enrl, reverse=reverse)

        return SectionStatsResponse(
            sections=stats_items,
            summary={
                "total_sections": len(sections_by_class_nbr),
                "total_enrolled": total_enrolled,
                "total_capacity": total_capacity,
                "overfilled_count": overfilled_count,
                "filtered_count": len(stats_items),
            },
            subjects=sorted(subjects_set),
        )
