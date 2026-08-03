"""Session API routes for session data management"""

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin, get_current_user, get_db
from app.core.parsers import (
    parse_buffer_timetables_csv,
    parse_courses_timetable_excel,
    parse_registration_excel,
    parse_students_excel,
)
from app.database import get_session_db
from app.models.buffer_timetable import BufferTimetable, BufferTimetableItem
from app.models.course import Course
from app.models.course_section import CourseSection
from app.models.registration import RegistrationData
from app.models.registration_timetable import (
    RegistrationTimetable,
    RegistrationTimetableItem,
)
from app.models.student import Student
from app.models.timetable import Timetable, TimetableItem
from app.models.user import User
from app.schemas.common import Message, UploadResult
from app.schemas.session import SessionResponse
from app.services.course_service import (
    bulk_create_course_sections,
    bulk_create_courses,
)
from app.services.session_service import (
    ensure_schema_columns,
    get_session_by_id,
    list_sessions,
)
from app.services.student_service import bulk_create_students

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("", response_model=list[SessionResponse])
async def get_sessions(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """
    List sessions.

    Admin: sees all sessions
    Staff: sees only enabled sessions
    """
    if current_user.role == "admin":
        sessions = await list_sessions(db)
    else:
        sessions = await list_sessions(db, is_enabled=True)

    return [SessionResponse.model_validate(s) for s in sessions]


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Get session by ID"""
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Staff can only access enabled sessions
    if current_user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Session is disabled")

    return SessionResponse.model_validate(session)


# Session data uploads (admin only)


@router.post("/{session_id}/upload/students", response_model=UploadResult)
async def upload_students(
    session_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Upload students Excel file (admin only)"""
    # Get session
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Save file permanently in session folder
    from app.core.utils import save_upload_file

    content = await file.read()
    file_path = await save_upload_file(content, file.filename, session.name)

    try:
        # Parse file - now returns ParseResult with duplicate tracking
        parse_result = parse_students_excel(file_path)
        students_data = parse_result.data

        # Get session database
        async for session_db in get_session_db(session.schema_name):
            # Bulk create students
            await bulk_create_students(session_db, students_data)
            await session_db.commit()
            break

        return UploadResult(
            message=f"Uploaded {len(students_data)} students successfully",
            total_rows=parse_result.total_rows,
            records_created=len(students_data),
            duplicates_removed=parse_result.duplicates_removed,
            duplicate_details=parse_result.duplicate_details[:50]
            if parse_result.duplicate_details
            else None,  # Limit to 50
            warnings=parse_result.warnings[:20] if parse_result.warnings else None,
            errors=parse_result.errors[:20] if parse_result.errors else None,
        )

    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Failed to upload students: {str(e)}"
        )


@router.post("/{session_id}/upload/courses", response_model=UploadResult)
async def upload_courses(
    session_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Upload courses/timetable Excel file (admin only)"""
    # Get session
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Save file permanently in session folder
    from app.core.utils import save_upload_file

    content = await file.read()
    file_path = await save_upload_file(content, file.filename, session.name)

    try:
        # Parse file - now returns ParseResult with duplicate tracking
        courses_data, sections_data, parse_result = parse_courses_timetable_excel(
            file_path,
            include_parse_result=True,
        )

        # Create courses in global database
        await bulk_create_courses(db, courses_data)
        await db.commit()

        # Auto-migrate schema to ensure all columns exist (no alembic needed)
        await ensure_schema_columns(session.schema_name)

        # Get session database and create sections
        async for session_db in get_session_db(session.schema_name):
            # Convert section course_id from string to UUID
            from app.schemas.course_section import CourseSectionCreate

            # OPTIMIZATION: Bulk fetch all courses instead of individual lookups
            unique_course_ids = {
                s["course_id"] if isinstance(s, dict) else s.course_id
                for s in sections_data
            }
            courses_result = await db.execute(
                select(Course.id, Course.course_id).where(
                    Course.course_id.in_(unique_course_ids)
                )
            )
            course_id_map = {row.course_id: row.id for row in courses_result.fetchall()}

            sections_create = []
            for section in sections_data:
                # section is already a dict
                section_dict = (
                    section if isinstance(section, dict) else section.model_dump()
                )

                # Use pre-fetched course mapping
                course_uuid = course_id_map.get(section_dict["course_id"])
                if course_uuid:
                    section_dict["course_id"] = course_uuid
                    sections_create.append(CourseSectionCreate(**section_dict))

            await bulk_create_course_sections(session_db, sections_create)
            await session_db.commit()
            break

        return UploadResult(
            message=f"Uploaded {len(courses_data)} courses and {len(sections_create)} sections",
            total_rows=parse_result.total_rows,
            records_created=len(sections_create),
            duplicates_removed=parse_result.duplicates_removed,
            duplicate_details=parse_result.duplicate_details[:50]
            if parse_result.duplicate_details
            else None,
            warnings=parse_result.warnings[:20] if parse_result.warnings else None,
            errors=parse_result.errors[:20] if parse_result.errors else None,
        )

    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Failed to upload courses: {str(e)}"
        )


@router.post("/{session_id}/upload/registration", response_model=UploadResult)
async def upload_registration(
    session_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """
    Upload registration data Excel file (admin only).

    This creates registration_timetables and registration_timetable_items to store
    the imported timetables. These are separate from portal-created timetables.
    Requires students and courses to be uploaded first.
    """
    # Get session
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Save file permanently in session folder
    from app.core.utils import save_upload_file

    content = await file.read()
    file_path = await save_upload_file(content, file.filename, session.name)

    try:
        # Parse file
        registration_data = parse_registration_excel(file_path)
        total_rows = len(registration_data)

        if not registration_data:
            raise HTTPException(
                status_code=400, detail="No valid registration data found in file"
            )

        # Get session database
        async for session_db in get_session_db(session.schema_name):
            # First, check if we have students in the database
            students_result = await session_db.execute(
                select(Student.id, Student.campus_id)
            )
            students_map = {row.campus_id: row.id for row in students_result.fetchall()}

            if not students_map:
                raise HTTPException(
                    status_code=400,
                    detail="No students found in database. Please upload students data first.",
                )

            # Check if we have course sections in the database
            sections_result = await session_db.execute(
                select(
                    CourseSection.id, CourseSection.class_nbr, CourseSection.course_id
                )
            )
            # Map class_nbr to list of section IDs (there can be multiple meetings per class_nbr)
            sections_by_class_nbr: dict[int, list[uuid.UUID]] = {}
            for row in sections_result.fetchall():
                if row.class_nbr not in sections_by_class_nbr:
                    sections_by_class_nbr[row.class_nbr] = []
                sections_by_class_nbr[row.class_nbr].append(row.id)

            if not sections_by_class_nbr:
                raise HTTPException(
                    status_code=400,
                    detail="No course sections found in database. Please upload timetable/courses data first.",
                )

            # Remove duplicates based on campus_id + class_nbr (each student can only register once per section)
            seen = set()
            unique_data = []
            duplicates = []
            warnings = []

            for idx, r in enumerate(registration_data):
                key = (r.campus_id, r.class_nbr)
                if key in seen:
                    duplicates.append(
                        {
                            "row": idx + 2,
                            "type": "duplicate_registration",
                            "value": f"{r.campus_id}",
                            "course": f"{r.subject} {r.catalog} Section {r.section}",
                            "class_nbr": r.class_nbr,
                        }
                    )
                else:
                    seen.add(key)
                    unique_data.append(r)

            # Track missing links
            missing_students = set()
            missing_sections = set()
            linked_registrations = []

            for r in unique_data:
                student_uuid = students_map.get(r.campus_id)
                section_ids = sections_by_class_nbr.get(r.class_nbr)

                if not student_uuid:
                    missing_students.add(r.campus_id)
                elif not section_ids:
                    missing_sections.add(r.class_nbr)
                else:
                    linked_registrations.append(
                        {
                            "registration": r,
                            "student_id": student_uuid,
                            "section_ids": section_ids,
                        }
                    )

            if missing_students:
                warnings.append(
                    f"{len(missing_students)} students not found in database (first 10: {list(missing_students)[:10]})"
                )
            if missing_sections:
                warnings.append(
                    f"{len(missing_sections)} class numbers not found in database (first 10: {list(missing_sections)[:10]})"
                )

            # Group registrations by student for timetable creation
            student_registrations: dict[uuid.UUID, list[dict]] = {}
            for item in linked_registrations:
                student_id = item["student_id"]
                if student_id not in student_registrations:
                    student_registrations[student_id] = []
                student_registrations[student_id].append(item)

            # OPTIMIZATION: Bulk fetch existing timetables and items
            student_ids_list = list(student_registrations.keys())

            # Fetch existing registration timetables
            existing_reg_tt_result = await session_db.execute(
                select(
                    RegistrationTimetable.id, RegistrationTimetable.student_id
                ).where(RegistrationTimetable.student_id.in_(student_ids_list))
            )
            reg_tt_map = {
                row.student_id: row.id for row in existing_reg_tt_result.fetchall()
            }

            # Fetch existing unified timetables
            existing_unified_tt_result = await session_db.execute(
                select(Timetable.id, Timetable.student_id).where(
                    Timetable.student_id.in_(student_ids_list)
                )
            )
            unified_tt_map = {
                row.student_id: row.id for row in existing_unified_tt_result.fetchall()
            }

            # Fetch ALL existing registration timetable items for these timetables
            if reg_tt_map:
                existing_items_result = await session_db.execute(
                    select(
                        RegistrationTimetableItem.timetable_id,
                        RegistrationTimetableItem.course_section_id,
                    ).where(
                        RegistrationTimetableItem.timetable_id.in_(reg_tt_map.values())
                    )
                )
                existing_items_by_tt: dict[uuid.UUID, set[uuid.UUID]] = {}
                for row in existing_items_result.fetchall():
                    if row.timetable_id not in existing_items_by_tt:
                        existing_items_by_tt[row.timetable_id] = set()
                    existing_items_by_tt[row.timetable_id].add(row.course_section_id)
            else:
                existing_items_by_tt = {}

            # Create registration timetables and items
            # Also create unified timetables (source='from_registration') for export/editing
            timetables_created = 0
            items_created = 0
            new_reg_timetables = []
            new_reg_items = []
            new_unified_timetables = []
            new_unified_items = []
            # NOTE: We no longer track enrollment_updates here since tot_enrl
            # is already set from the timetable Excel upload. Incrementing again
            # would cause double-counting.
            audit_logs = []

            for student_id, registrations in student_registrations.items():
                # Get or prepare registration timetable
                reg_timetable_id = reg_tt_map.get(student_id)

                if not reg_timetable_id:
                    # Create new registration timetable (immutable baseline)
                    reg_timetable_id = uuid.uuid4()
                    new_reg_timetables.append(
                        RegistrationTimetable(
                            id=reg_timetable_id,
                            student_id=student_id,
                            created_by_id=current_admin.id,
                            total_units=0.0,
                        )
                    )
                    reg_tt_map[student_id] = reg_timetable_id
                    timetables_created += 1

                # Get existing items for this timetable
                existing_section_ids = existing_items_by_tt.get(reg_timetable_id, set())

                # Add timetable items for each section
                all_section_ids = []
                for reg_item in registrations:
                    for section_id in reg_item["section_ids"]:
                        if section_id not in existing_section_ids:
                            new_reg_items.append(
                                RegistrationTimetableItem(
                                    id=uuid.uuid4(),
                                    timetable_id=reg_timetable_id,
                                    course_section_id=section_id,
                                )
                            )
                            existing_section_ids.add(section_id)
                            all_section_ids.append(section_id)
                            items_created += 1

                # Create corresponding unified timetable (source='from_registration', status='committed')
                unified_tt_id = unified_tt_map.get(student_id)

                if not unified_tt_id and all_section_ids:
                    # Create unified timetable
                    unified_tt_id = uuid.uuid4()
                    new_unified_timetables.append(
                        Timetable(
                            id=unified_tt_id,
                            student_id=student_id,
                            source="from_registration",
                            status="committed",
                            created_by_id=current_admin.id,
                            total_units=0.0,  # TODO: Calculate from sections
                            created_at=datetime.now(timezone.utc),
                        )
                    )

                    # Add items to unified timetable (copy from registration)
                    for section_id in all_section_ids:
                        new_unified_items.append(
                            TimetableItem(
                                id=uuid.uuid4(),
                                timetable_id=unified_tt_id,
                                course_section_id=section_id,
                            )
                        )

                    # Prepare audit log
                    audit_logs.append(
                        {
                            "timetable_id": unified_tt_id,
                            "section_ids": all_section_ids,
                        }
                    )

            # BULK INSERT all new records
            if new_reg_timetables:
                session_db.add_all(new_reg_timetables)
            if new_reg_items:
                session_db.add_all(new_reg_items)
            if new_unified_timetables:
                session_db.add_all(new_unified_timetables)
            if new_unified_items:
                session_db.add_all(new_unified_items)

            # NOTE: We no longer update tot_enrl here. The enrollment counts
            # are already set from the timetable Excel upload (upload_courses).
            # Updating again would cause double-counting.

            # Flush to get IDs before audit logging
            await session_db.flush()

            # Batch create audit logs
            if audit_logs:
                from app.services import audit_trail_service

                for log_data in audit_logs:
                    await audit_trail_service.log_initialize_from_registration(
                        db=session_db,
                        timetable_id=log_data["timetable_id"],
                        changed_by_id=current_admin.id,
                        section_ids=log_data["section_ids"],
                        total_units=0.0,
                    )

            # Also store the raw registration data for reference
            records = [
                RegistrationData(
                    campus_id=r.campus_id,
                    course_id=r.course_id,
                    subject=r.subject,
                    catalog=r.catalog,
                    section=r.section,
                    component=r.component,
                    class_nbr=r.class_nbr,
                    add_dt=r.add_dt,
                    drop_dt=r.drop_dt,
                    unit_taken=r.unit_taken,
                    grade_in=r.grade_in,
                    instructor_name=r.instructor_name,
                    admit_sem=r.admit_sem,
                    last_reg_sem=r.last_reg_sem,
                    degree1=r.degree1,
                    degree2=r.degree2,
                )
                for r in unique_data
            ]
            session_db.add_all(records)

            await session_db.commit()
            break

        return UploadResult(
            message=f"Uploaded {len(unique_data)} registration records. Created {timetables_created} registration timetables with {items_created} enrollments.",
            total_rows=total_rows,
            records_created=len(linked_registrations),
            duplicates_removed=len(duplicates),
            duplicate_details=duplicates[:50] if duplicates else None,
            warnings=warnings[:20] if warnings else None,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Failed to upload registration data: {str(e)}"
        )


@router.post("/{session_id}/upload/buffer-timetables", response_model=UploadResult)
async def upload_buffer_timetables(
    session_id: uuid.UUID,
    file: UploadFile = File(...),
    deduct_enrollment_on_upload: bool = True,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """
    Upload buffer timetables CSV file (admin only).

    This creates BufferTimetable records that can be assigned to students
    via the "Assign Timetable" feature on the generate page.

    Args:
        session_id: Session UUID
        file: CSV file (generated by scripts/generate_buffer_csv.py)
        deduct_enrollment_on_upload: If True, decreases tot_enrl for each section
            immediately. If False, tot_enrl will be decreased when students are
            assigned to these timetables.

    Requires courses to be uploaded first (to match sections by course_code + section + component).
    """
    from datetime import datetime, timezone

    # Get session
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Save file permanently in session folder
    from app.core.utils import save_upload_file

    content = await file.read()
    file_path = await save_upload_file(content, file.filename, session.name)

    try:
        # Parse file
        parse_result = parse_buffer_timetables_csv(file_path)

        if parse_result.errors:
            raise HTTPException(
                status_code=400,
                detail=f"CSV parsing errors: {'; '.join(parse_result.errors[:5])}",
            )

        if not parse_result.timetables:
            raise HTTPException(
                status_code=400, detail="No valid timetables found in CSV file"
            )

        # Get session database
        async for session_db in get_session_db(session.schema_name):
            # Get all course sections and build lookup map
            # Map by (course_code, section, component) where course_code = subject + ' ' + catalog
            sections_result = await session_db.execute(
                select(
                    CourseSection.id,
                    CourseSection.class_nbr,
                    CourseSection.section,
                    CourseSection.component,
                    CourseSection.course_id,
                )
            )
            sections_rows = sections_result.fetchall()

            if not sections_rows:
                raise HTTPException(
                    status_code=400,
                    detail="No course sections found. Please upload courses/timetable first.",
                )

            # Get course info (subject, catalog) from global courses table
            course_ids = list({row.course_id for row in sections_rows})
            courses_result = await db.execute(
                select(Course.id, Course.subject, Course.catalog).where(
                    Course.id.in_(course_ids)
                )
            )
            course_map = {
                row.id: f"{row.subject} {row.catalog}" for row in courses_result.fetchall()
            }

            # Build section lookup: (course_code, section, component) -> section_id
            # Note: multiple rows may exist for same class_nbr (different meeting times)
            # We only need one section_id per unique (course_code, section, component)
            section_lookup: dict[tuple[str, str, str], uuid.UUID] = {}
            section_enrollment_map: dict[uuid.UUID, int] = {}  # For enrollment updates

            for row in sections_rows:
                course_code = course_map.get(row.course_id, "")
                if course_code:
                    key = (course_code, row.section, row.component)
                    if key not in section_lookup:
                        section_lookup[key] = row.id
                    section_enrollment_map[row.id] = 0

            warnings = list(parse_result.warnings)
            timetables_created = 0
            items_created = 0
            items_requested = 0
            missing_sections: set[str] = set()
            enrollment_updates: dict[uuid.UUID, int] = {}  # section_id -> count to deduct

            warnings.append(
                f"Database has {len(section_lookup)} unique (course_code, section, component) combinations "
                f"from {len(course_map)} courses"
            )

            # Create buffer timetables
            for tt_data in parse_result.timetables:
                # Create buffer timetable record
                buffer_tt = BufferTimetable(
                    plan=tt_data.plan,
                    timetable_id=tt_data.timetable_id,
                    batch_size=tt_data.batch_size,
                    capacity_ceiling=tt_data.capacity_ceiling,
                    is_variant=tt_data.is_variant,
                    assigned_count=0,
                    enrollment_deducted_on_upload=deduct_enrollment_on_upload,
                    created_at=datetime.now(timezone.utc),
                    created_by_id=current_admin.id,
                )
                session_db.add(buffer_tt)
                await session_db.flush()  # Get the ID
                timetables_created += 1

                # Create buffer timetable items
                for item in tt_data.items:
                    items_requested += 1
                    key = (item["course_code"], item["section"], item["component"])
                    section_id = section_lookup.get(key)

                    if not section_id:
                        missing_sections.add(
                            f"{item['course_code']} {item['section']} ({item['component']})"
                        )
                        continue

                    buffer_item = BufferTimetableItem(
                        buffer_timetable_id=buffer_tt.id,
                        course_section_id=section_id,
                        course_code=item["course_code"],
                        component=item["component"],
                        section=item["section"],
                    )
                    session_db.add(buffer_item)
                    items_created += 1

                    # Track enrollment updates
                    if deduct_enrollment_on_upload:
                        if section_id not in enrollment_updates:
                            enrollment_updates[section_id] = 0
                        enrollment_updates[section_id] += tt_data.batch_size

            # Update enrollment counts if requested
            if deduct_enrollment_on_upload and enrollment_updates:
                from sqlalchemy import update

                for section_id, count in enrollment_updates.items():
                    await session_db.execute(
                        update(CourseSection)
                        .where(CourseSection.id == section_id)
                        .values(tot_enrl=CourseSection.tot_enrl + count)
                    )

            if missing_sections:
                warnings.append(
                    f"{len(missing_sections)} sections not found (matched {items_created}/{items_requested} items). "
                    f"First 10 missing: {list(missing_sections)[:10]}"
                )

            await session_db.commit()
            break

        return UploadResult(
            message=f"Uploaded {timetables_created} buffer timetables with {items_created}/{items_requested} section items matched. "
            f"{'Enrollment counts updated.' if deduct_enrollment_on_upload else 'Enrollment will update on assignment.'}",
            total_rows=parse_result.total_rows,
            records_created=timetables_created,
            duplicates_removed=0,
            warnings=warnings[:20] if warnings else None,
            errors=parse_result.errors[:20] if parse_result.errors else None,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Failed to upload buffer timetables: {str(e)}"
        )


@router.get("/{session_id}/buffer-timetables")
async def get_buffer_timetables(
    session_id: uuid.UUID,
    plan: str | None = None,
    branch: str | None = None,
    include_full: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get buffer timetables for a session.

    Args:
        session_id: Session UUID
        plan: Filter by exact plan name (e.g., "COMBINED:A3,A4,A5+ALL_MM")
        branch: Filter by branch code (e.g., "A5"). Will match plans containing this branch.
        include_full: If True, include timetables that are already full (assigned_count >= batch_size)

    Returns list of buffer timetables with their items and capacity info.
    """
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    async for session_db in get_session_db(session.schema_name):
        # Build query
        query = select(BufferTimetable)

        # Filter by plan or branch
        if plan:
            query = query.where(BufferTimetable.plan == plan)
        elif branch:
            # Match plans that contain this branch
            # This handles cases like:
            # - "A5" matches "A5"
            # - "A5" matches "A3,A4,A5,A7"
            # - "A5" matches "COMBINED:A3,A4,A5+ALL_MM"
            from sqlalchemy import or_

            query = query.where(
                or_(
                    BufferTimetable.plan == branch,
                    BufferTimetable.plan.contains(f",{branch},"),
                    BufferTimetable.plan.contains(f",{branch}+"),
                    BufferTimetable.plan.startswith(f"{branch},"),
                    BufferTimetable.plan.endswith(f",{branch}"),
                    BufferTimetable.plan.contains(f":{branch},"),
                    BufferTimetable.plan.contains(f":{branch}+"),
                    BufferTimetable.plan == f"{branch}_PCB",
                    # Plans like "FY_A7_CORE" or "ME_CS_A7" still match campus branch A7
                    BufferTimetable.plan.contains(branch),
                )
            )

        # Filter out full timetables unless requested
        if not include_full:
            query = query.where(BufferTimetable.assigned_count < BufferTimetable.batch_size)

        query = query.order_by(BufferTimetable.plan, BufferTimetable.timetable_id)

        result = await session_db.execute(query)
        buffer_timetables = result.scalars().all()

        # Fetch items for each timetable
        response = []
        for bt in buffer_timetables:
            items_result = await session_db.execute(
                select(BufferTimetableItem).where(
                    BufferTimetableItem.buffer_timetable_id == bt.id
                )
            )
            items = items_result.scalars().all()

            # Get unique courses for display
            courses = list({item.course_code for item in items})

            response.append({
                "id": str(bt.id),
                "plan": bt.plan,
                "timetable_id": bt.timetable_id,
                "batch_size": bt.batch_size,
                "capacity_ceiling": bt.capacity_ceiling,
                "assigned_count": bt.assigned_count,
                "remaining_capacity": bt.remaining_capacity,
                "fill_percentage": bt.fill_percentage,
                "is_variant": bt.is_variant,
                "is_full": bt.is_full,
                "enrollment_deducted_on_upload": bt.enrollment_deducted_on_upload,
                "label": bt.label,
                "courses": sorted(courses),
                "items": [
                    {
                        "course_code": item.course_code,
                        "component": item.component,
                        "section": item.section,
                    }
                    for item in items
                ],
            })

        return {"buffer_timetables": response, "total": len(response)}


@router.delete("/{session_id}/buffer-timetables/{buffer_timetable_id}", response_model=Message)
async def delete_buffer_timetable(
    session_id: uuid.UUID,
    buffer_timetable_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete a saved buffer timetable for this session.
    """
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if current_user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Session is disabled")

    from app.services.timetable_bucket_save_service import (
        delete_buffer_timetable_with_enrollment_release,
    )

    async for session_db in get_session_db(session.schema_name):
        found, released = await delete_buffer_timetable_with_enrollment_release(
            session_db, buffer_timetable_id
        )
        if not found:
            raise HTTPException(status_code=404, detail="Buffer timetable not found")
        await session_db.commit()
        msg = "Buffer timetable deleted successfully"
        if released:
            msg += f"; {released} reserved seat(s) released per section"
        return Message(message=msg)


@router.get("/{session_id}/buffer-timetables/{buffer_timetable_id}")
async def get_buffer_timetable_detail(
    session_id: uuid.UUID,
    buffer_timetable_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get detailed information about a specific buffer timetable.

    Returns the buffer timetable with full section details including
    meeting times, rooms, and instructors.
    """
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    async for session_db in get_session_db(session.schema_name):
        # Get buffer timetable
        result = await session_db.execute(
            select(BufferTimetable).where(BufferTimetable.id == buffer_timetable_id)
        )
        bt = result.scalar_one_or_none()

        if not bt:
            raise HTTPException(status_code=404, detail="Buffer timetable not found")

        # Get items with section details
        items_result = await session_db.execute(
            select(BufferTimetableItem).where(
                BufferTimetableItem.buffer_timetable_id == bt.id
            )
        )
        items = items_result.scalars().all()

        # Get course section details - first get linked sections to find class_nbrs
        section_ids = [item.course_section_id for item in items]
        if section_ids:
            # Get the linked sections to find their class_nbrs
            linked_sections_result = await session_db.execute(
                select(CourseSection).where(CourseSection.id.in_(section_ids))
            )
            linked_sections = {s.id: s for s in linked_sections_result.scalars().all()}

            # Get all class_nbrs from linked sections
            class_nbrs = list({s.class_nbr for s in linked_sections.values()})

            # Fetch ALL meeting times for these class_nbrs (not just the linked ones)
            if class_nbrs:
                all_sections_result = await session_db.execute(
                    select(CourseSection).where(CourseSection.class_nbr.in_(class_nbrs))
                )
                all_sections = list(all_sections_result.scalars().all())
                # Group by class_nbr for easy lookup
                sections_by_class_nbr: dict[int, list] = {}
                for s in all_sections:
                    if s.class_nbr not in sections_by_class_nbr:
                        sections_by_class_nbr[s.class_nbr] = []
                    sections_by_class_nbr[s.class_nbr].append(s)
            else:
                sections_by_class_nbr = {}
        else:
            linked_sections = {}
            sections_by_class_nbr = {}

        # Get course info from global db
        course_ids = list({s.course_id for s in linked_sections.values()})
        if course_ids:
            courses_result = await db.execute(
                select(Course.id, Course.subject, Course.catalog, Course.title).where(
                    Course.id.in_(course_ids)
                )
            )
            courses = {r.id: r for r in courses_result.fetchall()}
        else:
            courses = {}

        # Build detailed items response with ALL meeting times
        detailed_items = []
        for item in items:
            linked_section = linked_sections.get(item.course_section_id)
            course = courses.get(linked_section.course_id) if linked_section else None

            if linked_section:
                # Get all meeting times for this class_nbr
                all_meetings = sections_by_class_nbr.get(linked_section.class_nbr, [])
                # Sort by day for consistent display
                day_order = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6}
                all_meetings.sort(key=lambda s: (day_order.get(s.day, 99), str(s.mtg_start or "")))

                # Combine all meeting times into the response
                meetings = []
                for section in all_meetings:
                    meetings.append({
                        "day": section.day,
                        "start_time": str(section.mtg_start) if section.mtg_start else None,
                        "end_time": str(section.mtg_end) if section.mtg_end else None,
                        "room": section.room,
                        "instructor": section.instructor,
                    })

                detailed_items.append({
                    "course_code": item.course_code,
                    "component": item.component,
                    "section": item.section,
                    "course_title": course.title if course else None,
                    "class_nbr": linked_section.class_nbr,
                    "capacity": linked_section.cap_enrl,
                    "enrolled": linked_section.tot_enrl,
                    "meetings": meetings,
                    # Keep legacy single-meeting fields for backward compatibility
                    "day": all_meetings[0].day if all_meetings else None,
                    "start_time": str(all_meetings[0].mtg_start) if all_meetings and all_meetings[0].mtg_start else None,
                    "end_time": str(all_meetings[0].mtg_end) if all_meetings and all_meetings[0].mtg_end else None,
                    "room": all_meetings[0].room if all_meetings else None,
                    "instructor": all_meetings[0].instructor if all_meetings else None,
                })
            else:
                detailed_items.append({
                    "course_code": item.course_code,
                    "component": item.component,
                    "section": item.section,
                    "course_title": None,
                    "class_nbr": None,
                    "capacity": None,
                    "enrolled": None,
                    "meetings": [],
                    "day": None,
                    "start_time": None,
                    "end_time": None,
                    "room": None,
                    "instructor": None,
                })

        return {
            "id": str(bt.id),
            "plan": bt.plan,
            "timetable_id": bt.timetable_id,
            "batch_size": bt.batch_size,
            "capacity_ceiling": bt.capacity_ceiling,
            "assigned_count": bt.assigned_count,
            "remaining_capacity": bt.remaining_capacity,
            "fill_percentage": bt.fill_percentage,
            "is_variant": bt.is_variant,
            "is_full": bt.is_full,
            "enrollment_deducted_on_upload": bt.enrollment_deducted_on_upload,
            "label": bt.label,
            "items": detailed_items,
        }
