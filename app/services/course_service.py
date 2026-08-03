"""Course service for course and section operations"""

import uuid
from typing import Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.models.course_section import CourseSection
from app.schemas.course import CourseCreate
from app.schemas.course_section import CourseSectionCreate


async def create_course(db: AsyncSession, course_create: CourseCreate) -> Course:
    """Create a new course in global database"""
    course = Course(
        course_id=course_create.course_id,
        subject=course_create.subject,
        catalog=course_create.catalog,
        title=course_create.title,
        max_units=course_create.max_units,
    )

    db.add(course)
    await db.flush()
    await db.refresh(course)

    return course


async def bulk_create_courses(
    db: AsyncSession, courses_create: list[CourseCreate]
) -> list[Course]:
    """Bulk create courses with upsert (update on duplicate)"""
    from datetime import datetime, timezone

    from sqlalchemy.dialects.postgresql import insert

    from app.services.course_cache_service import invalidate_course_cache

    if not courses_create:
        return []

    # Process in batches to avoid exceeding max parameters
    BATCH_SIZE = 500

    for i in range(0, len(courses_create), BATCH_SIZE):
        batch = courses_create[i : i + BATCH_SIZE]

        values = [
            {
                "id": uuid.uuid4(),
                "course_id": c.course_id,
                "subject": c.subject,
                "catalog": c.catalog,
                "title": c.title,
                "max_units": c.max_units,
                "created_at": datetime.now(timezone.utc),
            }
            for c in batch
        ]

        stmt = insert(Course).values(values)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_course_identity",
            set_={
                "title": stmt.excluded.title,
                "max_units": stmt.excluded.max_units,
            },
        )

        await db.execute(stmt)

    await db.flush()

    # Invalidate course cache so it refreshes with new data
    invalidate_course_cache()

    return []


async def bulk_create_prerequisites(
    db: AsyncSession, prerequisites_data: list[dict]
) -> None:
    """
    Bulk create prerequisites.

    Expects list of dicts with:
    - course_id (UUID)
    - prereq_course_id (UUID)
    - prereq_type (str)
    - prereq_order (int)
    - is_corequisite (bool)
    """
    from sqlalchemy import delete
    from sqlalchemy.dialects.postgresql import insert

    from app.models.prerequisite import Prerequisite

    if not prerequisites_data:
        return

    # First, delete existing prerequisites for the courses involved to avoid duplicates
    # (Assuming full refresh for the affected courses)
    course_ids = {p["course_id"] for p in prerequisites_data}
    if course_ids:
        await db.execute(
            delete(Prerequisite).where(Prerequisite.course_id.in_(course_ids))
        )

    BATCH_SIZE = 500
    for i in range(0, len(prerequisites_data), BATCH_SIZE):
        batch = prerequisites_data[i : i + BATCH_SIZE]

        values = [
            {
                "id": uuid.uuid4(),
                "course_id": p["course_id"],
                "prereq_course_id": p["prereq_course_id"],
                "prereq_type": p["prereq_type"],
                "prereq_order": p["prereq_order"],
                "is_corequisite": p["is_corequisite"],
            }
            for p in batch
        ]

        stmt = insert(Prerequisite).values(values)
        await db.execute(stmt)

    await db.flush()


async def get_course_by_id(db: AsyncSession, course_id: uuid.UUID) -> Course | None:
    """Get course by UUID"""
    result = await db.execute(select(Course).where(Course.id == course_id))
    return result.scalar_one_or_none()


async def get_course_by_identity(
    db: AsyncSession, course_id: str, subject: str = "", catalog: str = ""
) -> Course | None:
    """Get course by course_id (and optionally subject/catalog)"""
    query = select(Course).where(Course.course_id == course_id)

    # If subject and catalog are provided, use them for more specific match
    if subject:
        query = query.where(Course.subject == subject)
    if catalog:
        query = query.where(Course.catalog == catalog)

    result = await db.execute(query)
    return result.scalars().first()


async def list_courses(db: AsyncSession) -> Sequence[Course]:
    """List all courses"""
    result = await db.execute(select(Course).order_by(Course.subject, Course.catalog))
    return result.scalars().all()


async def search_courses(
    db: AsyncSession, query: str | None = None
) -> Sequence[Course]:
    """
    Search courses with smart multi-word query support.

    Supports patterns like:
    - "CS F2" → matches CS F211, CS F213, etc.
    - "data structures" → matches title containing both words
    - "CS" → matches all CS courses
    - "F211" → matches catalog F211
    """
    from sqlalchemy import and_, func

    stmt = select(Course)

    if query:
        # Normalize query: strip and collapse multiple spaces
        query = " ".join(query.split())

        # Split into tokens
        tokens = query.split()

        if len(tokens) == 1:
            # Single word: match against subject, catalog, or title
            token = tokens[0]
            stmt = stmt.where(
                or_(
                    Course.subject.ilike(f"%{token}%"),
                    Course.catalog.ilike(f"%{token}%"),
                    Course.title.ilike(f"%{token}%"),
                )
            )
        else:
            # Multi-word query: smart matching
            # Strategy 1: First token is subject, rest is catalog prefix
            # e.g., "CS F2" → subject="CS", catalog starts with "F2"
            first_token = tokens[0].upper()
            rest_tokens = " ".join(tokens[1:])

            subject_catalog_match = and_(
                func.upper(Course.subject) == first_token,
                Course.catalog.ilike(f"{rest_tokens}%"),
            )

            # Strategy 2: All tokens must appear in subject+catalog+title combined
            # e.g., "data structures" matches title containing both words
            combined_field = func.concat(
                Course.subject, " ", Course.catalog, " ", Course.title
            )
            all_tokens_match_conditions = [
                combined_field.ilike(f"%{token}%") for token in tokens
            ]

            # Strategy 3: Try matching catalog with spaces removed
            # e.g., "CS F 211" → catalog "F211"
            catalog_no_space = "".join(tokens[1:]) if len(tokens) > 1 else ""
            subject_catalog_nospace_match = and_(
                func.upper(Course.subject) == first_token,
                Course.catalog.ilike(f"{catalog_no_space}%"),
            )

            # Combine all strategies with OR
            stmt = stmt.where(
                or_(
                    subject_catalog_match,
                    and_(*all_tokens_match_conditions),
                    subject_catalog_nospace_match,
                )
            )

    result = await db.execute(stmt.order_by(Course.subject, Course.catalog))
    return result.scalars().all()


# Course Section operations (in session schema)


async def create_course_section(
    db: AsyncSession, section_create: CourseSectionCreate
) -> CourseSection:
    """Create a new course section in session schema"""
    section = CourseSection(
        course_id=section_create.course_id,
        class_nbr=section_create.class_nbr,
        section=section_create.section,
        component=section_create.component,
        day=section_create.day,
        class_pattern=section_create.class_pattern,
        mtg_start=section_create.mtg_start,
        mtg_end=section_create.mtg_end,
        exam_date=section_create.exam_date,
        exam_start=section_create.exam_start,
        exam_end=section_create.exam_end,
        instructor=section_create.instructor,
        room=section_create.room,
        cap_enrl=section_create.cap_enrl,
        tot_enrl=0,
    )

    db.add(section)
    await db.flush()
    await db.refresh(section)

    return section


async def bulk_create_course_sections(
    db: AsyncSession, sections_create: list[CourseSectionCreate]
) -> list[CourseSection]:
    """
    Bulk create course sections.

    Note: class_nbr is NOT unique (a section can have multiple meeting times).
    We use (class_nbr, day, mtg_start) as the logical key for updates.

    Re-upload is blocked if timetables exist referencing these sections.
    The timetable Excel is the source of truth for initial capacity/enrollment data.
    """
    from sqlalchemy import func, select
    from sqlalchemy.dialects.postgresql import insert

    from app.models.registration_timetable import RegistrationTimetableItem
    from app.models.timetable import TimetableItem

    if not sections_create:
        return []

    # Get unique class_nbrs being uploaded
    class_nbrs = {s.class_nbr for s in sections_create}

    # Check if there are existing sections for these class_nbrs
    if class_nbrs:
        # Get the section IDs that would be affected
        section_ids_result = await db.execute(
            select(CourseSection.id).where(CourseSection.class_nbr.in_(class_nbrs))
        )
        section_ids = [row[0] for row in section_ids_result.fetchall()]

        if section_ids:
            # Check if any registration timetables reference these sections
            reg_count_result = await db.execute(
                select(func.count())
                .select_from(RegistrationTimetableItem)
                .where(RegistrationTimetableItem.course_section_id.in_(section_ids))
            )
            reg_count = reg_count_result.scalar()

            # Check if any portal timetables reference these sections
            timetable_count_result = await db.execute(
                select(func.count())
                .select_from(TimetableItem)
                .where(TimetableItem.course_section_id.in_(section_ids))
            )
            timetable_count = timetable_count_result.scalar()

            if reg_count > 0 or timetable_count > 0:
                raise ValueError(
                    f"Cannot re-upload courses: {reg_count + timetable_count} timetable items exist. "
                    "Please delete timetable data first or create a new session."
                )

    BATCH_SIZE = 500

    for i in range(0, len(sections_create), BATCH_SIZE):
        batch = sections_create[i : i + BATCH_SIZE]

        values = [
            {
                "id": uuid.uuid4(),
                "course_id": s.course_id,
                "class_nbr": s.class_nbr,
                "section": s.section,
                "component": s.component,
                "day": s.day,
                "class_pattern": s.class_pattern,
                "mtg_start": s.mtg_start,
                "mtg_end": s.mtg_end,
                "exam_date": s.exam_date,
                "exam_start": s.exam_start,
                "exam_end": s.exam_end,
                "instructor": s.instructor,
                "room": s.room,
                "cap_enrl": s.cap_enrl,
                "tot_enrl": s.tot_enrl,
            }
            for s in batch
        ]

        stmt = insert(CourseSection).values(values)

        # On conflict, update with new Excel values
        stmt = stmt.on_conflict_do_update(
            index_elements=["class_nbr", "day", "mtg_start"],
            set_={
                "section": stmt.excluded.section,
                "component": stmt.excluded.component,
                "class_pattern": stmt.excluded.class_pattern,
                "mtg_end": stmt.excluded.mtg_end,
                "exam_date": stmt.excluded.exam_date,
                "exam_start": stmt.excluded.exam_start,
                "exam_end": stmt.excluded.exam_end,
                "instructor": stmt.excluded.instructor,
                "room": stmt.excluded.room,
                "cap_enrl": stmt.excluded.cap_enrl,
                "tot_enrl": stmt.excluded.tot_enrl,
            },
        )

        await db.execute(stmt)

    await db.flush()
    return []


async def get_course_section_by_id(
    db: AsyncSession, section_id: uuid.UUID
) -> CourseSection | None:
    """Get course section by UUID"""
    result = await db.execute(
        select(CourseSection).where(CourseSection.id == section_id)
    )
    return result.scalar_one_or_none()


async def get_sections_by_course(
    db: AsyncSession, course_id: uuid.UUID
) -> Sequence[CourseSection]:
    """Get all sections for a course"""
    result = await db.execute(
        select(CourseSection)
        .where(CourseSection.course_id == course_id)
        .order_by(CourseSection.section)
    )
    return result.scalars().all()


async def list_course_sections(db: AsyncSession) -> Sequence[CourseSection]:
    """List all course sections"""
    result = await db.execute(select(CourseSection).order_by(CourseSection.class_nbr))
    return result.scalars().all()


async def get_sections_by_class_nbr(
    db: AsyncSession, class_nbr: int
) -> Sequence[CourseSection]:
    """
    Get all sections with the given class_nbr.

    A class_nbr can have multiple rows (one per meeting time),
    so this returns all rows for that section.
    """
    result = await db.execute(
        select(CourseSection)
        .where(CourseSection.class_nbr == class_nbr)
        .order_by(CourseSection.day, CourseSection.mtg_start)
    )
    return result.scalars().all()


async def update_section_capacity(
    db: AsyncSession, class_nbr: int, cap_enrl: int | None
) -> Sequence[CourseSection]:
    """
    Update capacity for all meeting times of a section.

    Since a section can have multiple meeting times (rows with same class_nbr),
    this updates cap_enrl for all of them to maintain consistency.
    """
    from sqlalchemy import update

    # Update all rows with this class_nbr
    stmt = (
        update(CourseSection)
        .where(CourseSection.class_nbr == class_nbr)
        .values(cap_enrl=cap_enrl)
    )
    await db.execute(stmt)
    await db.flush()

    # Return updated sections
    return await get_sections_by_class_nbr(db, class_nbr)
