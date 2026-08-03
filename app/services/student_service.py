"""Student service for student operations"""

import uuid
from typing import Any, Sequence

from sqlalchemy import String, case, cast, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.registration_timetable import RegistrationTimetable
from app.models.student import Student
from app.models.timetable import ACTIVE_TIMETABLE_STATUSES, Timetable
from app.schemas.common import PaginatedResponse
from app.schemas.student import StudentCreate, StudentSearch


async def create_student(db: AsyncSession, student_create: StudentCreate) -> Student:
    """Create a new student in session schema"""
    student = Student(
        student_id=student_create.student_id,
        campus_id=student_create.campus_id,
        name=student_create.name,
        email=student_create.email,
        sex=student_create.sex,
        birthdate=student_create.birthdate,
        admission_category=student_create.admission_category,
    )

    db.add(student)
    await db.flush()
    await db.refresh(student)

    return student


async def bulk_create_students(
    db: AsyncSession, students_create: list[StudentCreate]
) -> list[Student]:
    """Bulk create students with upsert (update on duplicate) in batches"""
    from sqlalchemy import func, select
    from sqlalchemy.dialects.postgresql import insert

    from app.models.timetable import Timetable

    if not students_create:
        return []

    # Check if any timetables exist before allowing student data re-upload
    reg_timetable_count = await db.execute(
        select(func.count()).select_from(RegistrationTimetable)
    )
    reg_count = reg_timetable_count.scalar()

    portal_timetable_count = await db.execute(
        select(func.count()).select_from(Timetable)
    )
    portal_count = portal_timetable_count.scalar()

    if reg_count > 0 or portal_count > 0:
        raise ValueError(
            f"Cannot re-upload students: {reg_count + portal_count} timetables exist. "
            "Please delete timetable data first or create a new session."
        )

    # Process in batches to avoid exceeding max parameters (32767)
    # Each student has 8 columns, so max ~4000 students per batch
    BATCH_SIZE = 1000

    for i in range(0, len(students_create), BATCH_SIZE):
        batch = students_create[i : i + BATCH_SIZE]

        # Prepare data for insert
        values = [
            {
                "id": uuid.uuid4(),
                "student_id": s.student_id,
                "campus_id": s.campus_id,
                "name": s.name,
                "email": s.email,
                "sex": s.sex,
                "birthdate": s.birthdate,
                "admission_category": s.admission_category,
            }
            for s in batch
        ]

        # Use PostgreSQL INSERT ... ON CONFLICT for upsert
        stmt = insert(Student).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["student_id"],
            set_={
                "campus_id": stmt.excluded.campus_id,
                "name": stmt.excluded.name,
                "email": stmt.excluded.email,
                "sex": stmt.excluded.sex,
                "birthdate": stmt.excluded.birthdate,
                "admission_category": stmt.excluded.admission_category,
            },
        )

        await db.execute(stmt)

    await db.flush()

    return []  # Return empty list since we don't fetch the inserted rows


async def get_student_by_id(db: AsyncSession, student_id: uuid.UUID) -> Student | None:
    """Get student by UUID"""
    result = await db.execute(select(Student).where(Student.id == student_id))
    return result.scalar_one_or_none()


async def get_student_by_campus_id(db: AsyncSession, campus_id: str) -> Student | None:
    """Get student by campus ID"""
    result = await db.execute(select(Student).where(Student.campus_id == campus_id))
    return result.scalar_one_or_none()


async def search_students_fast(
    db: AsyncSession,
    query: str | None = None,
    timetable_filter: str = "all",
    branch_filter: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """
    Fast server-side search with timetable status using efficient JOINs.

    Args:
        query: Search text (matches name, campus_id, email)
        timetable_filter: "all", "with", "without", "registration"
        branch_filter: Branch code like "A3", "B2" (extracted from campus_id)
        page: Page number (1-indexed)
        page_size: Number of results per page

    Returns:
        Dict with items, total, page info, and summary stats
    """
    # Build subquery for timetable with source info
    # Check the actual source field in Timetable to determine if it's portal or registration
    tt_subq = (
        select(Timetable.student_id, cast(Timetable.source, String).label("source"))
        .where(cast(Timetable.status, String).in_(ACTIVE_TIMETABLE_STATUSES))
        .distinct()
        .subquery()
    )

    # Main query with LEFT JOIN for timetable status
    base_query = select(
        Student.id,
        Student.student_id,
        Student.campus_id,
        Student.name,
        Student.email,
        case(
            (tt_subq.c.source == "portal_generated", literal("portal")),
            (tt_subq.c.source == "from_registration", literal("registration")),
            else_=literal("none"),
        ).label("timetable_type"),
    ).outerjoin(tt_subq, Student.id == tt_subq.c.student_id)

    # Apply text search filter
    if query and query.strip():
        search_term = f"%{query.strip()}%"
        base_query = base_query.where(
            or_(
                Student.campus_id.ilike(search_term),
                Student.name.ilike(search_term),
                Student.email.ilike(search_term),
            )
        )

    # Apply branch filter (extract from campus_id)
    if branch_filter and branch_filter != "all":
        # For single degree: 2024A3PS0309P - match year(4) + branch
        # For dual degree: 2024B2A30309P - match year(4) + combined_branch
        # Pattern: 4 underscores (year) + branch + anything
        branch_pattern = f"____{branch_filter}%"  # 4 year digits + branch
        base_query = base_query.where(Student.campus_id.like(branch_pattern))

    # Apply timetable filter
    if timetable_filter == "with":
        base_query = base_query.where(tt_subq.c.student_id.isnot(None))
    elif timetable_filter == "without":
        base_query = base_query.where(tt_subq.c.student_id.is_(None))
    elif timetable_filter == "portal":
        base_query = base_query.where(tt_subq.c.source == "portal_generated")
    elif timetable_filter == "registration":
        base_query = base_query.where(tt_subq.c.source == "from_registration")

    # Get total count (before pagination)
    count_query = select(func.count()).select_from(base_query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Apply sorting and pagination
    paginated_query = (
        base_query.order_by(Student.name)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    result = await db.execute(paginated_query)
    rows = result.fetchall()

    # Convert to dict
    items = [
        {
            "id": str(row.id),
            "student_id": row.student_id,
            "campus_id": row.campus_id,
            "name": row.name,
            "email": row.email,
            "timetable_type": row.timetable_type,
            "has_timetable": row.timetable_type != "none",
        }
        for row in rows
    ]

    total_pages = (total + page_size - 1) // page_size if page_size > 0 else 0

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


async def get_student_stats(db: AsyncSession) -> dict:
    """Get summary statistics for students (cached-friendly)"""
    # Total students
    total_result = await db.execute(select(func.count()).select_from(Student))
    total_students = total_result.scalar_one()

    # Students with portal timetables (source='portal_generated')
    portal_tt_count = await db.execute(
        select(func.count(func.distinct(Timetable.student_id)))
        .where(cast(Timetable.status, String).in_(ACTIVE_TIMETABLE_STATUSES))
        .where(cast(Timetable.source, String) == "portal_generated")
    )
    with_portal_timetable = portal_tt_count.scalar_one()

    # Students with registration timetables (source='from_registration')
    reg_tt_count = await db.execute(
        select(func.count(func.distinct(Timetable.student_id)))
        .where(cast(Timetable.status, String).in_(ACTIVE_TIMETABLE_STATUSES))
        .where(cast(Timetable.source, String) == "from_registration")
    )
    with_registration_timetable = reg_tt_count.scalar_one()

    return {
        "total_students": total_students,
        "with_portal_timetable": with_portal_timetable,
        "with_registration_timetable": with_registration_timetable,
        "without_timetable": total_students
        - with_portal_timetable
        - with_registration_timetable,
    }


async def get_branch_list(db: AsyncSession) -> list[str]:
    """Get distinct branch codes from campus IDs"""
    # Extract branches using the branch_extractor logic
    # Single degree: 2024A3PS0309P -> A3
    # Dual degree: 2024B2A30309P -> B2A3
    from app.core.branch_extractor import extract_campus_id_info

    result = await db.execute(select(Student.campus_id))
    campus_ids = [row[0] for row in result.fetchall()]

    branches_set = set()
    for campus_id in campus_ids:
        info = extract_campus_id_info(campus_id)
        if info:
            branches_set.add(info.branches)

    return sorted(list(branches_set))


async def search_students(
    db: AsyncSession, search: StudentSearch
) -> PaginatedResponse[Any]:
    """Search students with pagination (legacy function)"""
    query = select(Student)

    # Apply filters
    if search.campus_id:
        query = query.where(Student.campus_id.ilike(f"%{search.campus_id}%"))
    elif search.name:
        query = query.where(Student.name.ilike(f"%{search.name}%"))
    elif search.query:
        query = query.where(
            or_(
                Student.campus_id.ilike(f"%{search.query}%"),
                Student.name.ilike(f"%{search.query}%"),
            )
        )

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Apply pagination
    query = query.offset((search.page - 1) * search.page_size).limit(search.page_size)

    # Execute query
    result = await db.execute(query)
    students = list(result.scalars().all())

    total_pages = (total + search.page_size - 1) // search.page_size

    return PaginatedResponse(
        items=students,
        total=total,
        page=search.page,
        page_size=search.page_size,
        total_pages=total_pages,
    )


async def list_students(db: AsyncSession) -> Sequence[Student]:
    """List all students"""
    result = await db.execute(select(Student).order_by(Student.campus_id))
    return result.scalars().all()


async def list_students_with_timetable_status(db: AsyncSession) -> list[dict]:
    """List all students with timetable status (legacy - avoid for large datasets)"""
    # Get all students
    result = await db.execute(select(Student).order_by(Student.campus_id))
    students = result.scalars().all()

    # Get all student IDs that have timetables
    timetable_result = await db.execute(
        select(Timetable.student_id).where(
            cast(Timetable.status, String).in_(ACTIVE_TIMETABLE_STATUSES)
        )
    )
    students_with_timetables = set(row[0] for row in timetable_result.fetchall())

    # Build response with has_timetable flag
    students_data = []
    for student in students:
        student_dict = {
            "id": student.id,
            "student_id": student.student_id,
            "campus_id": student.campus_id,
            "name": student.name,
            "email": student.email,
            "sex": student.sex,
            "birthdate": student.birthdate,
            "admission_category": student.admission_category,
            "has_timetable": student.id in students_with_timetables,
        }
        students_data.append(student_dict)

    return students_data
