"""Registration Timetable API routes for visual editor"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_db
from app.database import get_session_db, get_session_db_read_only
from app.models.course import Course
from app.models.course_section import CourseSection
from app.models.registration_timetable import (
    RegistrationTimetable,
    RegistrationTimetableItem,
)
from app.models.student import Student
from app.models.user import User
from app.schemas.registration_timetable import (
    RegistrationTimetableUpdateRequest,
    RegistrationTimetableUpdateResponse,
    RegistrationTimetableWithCourseInfo,
)
from app.services.session_service import get_session_by_id

router = APIRouter(
    prefix="/api/sessions/{session_id}/students/{student_id}/registration-timetable",
    tags=["registration-timetables"],
)


@router.get("", response_model=RegistrationTimetableWithCourseInfo)
async def get_registration_timetable(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get student's registration timetable with full course details.

    Returns all timetable items (including deleted ones with deleted=True flag)
    along with complete course section and course information for the visual editor.
    """
    # Get session
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Use read-only session for better concurrency
    async for session_db in get_session_db_read_only(session.schema_name):
        # Get student
        student_result = await session_db.execute(
            select(Student).where(Student.id == student_id)
        )
        student = student_result.scalar_one_or_none()
        if not student:
            raise HTTPException(status_code=404, detail="Student not found")

        # Get registration timetable with items
        timetable_result = await session_db.execute(
            select(RegistrationTimetable)
            .where(RegistrationTimetable.student_id == student_id)
            .options(
                selectinload(RegistrationTimetable.items).selectinload(
                    RegistrationTimetableItem.course_section
                )
            )
        )
        timetable = timetable_result.scalar_one_or_none()

        if not timetable:
            raise HTTPException(
                status_code=404, detail="No registration timetable found for student"
            )

        # Collect course IDs to fetch course info from global database
        course_ids = set()
        for item in timetable.items:
            if item.course_section:
                course_ids.add(item.course_section.course_id)

        # Fetch course information from global database
        courses_map = {}
        if course_ids:
            courses_result = await db.execute(
                select(Course).where(Course.id.in_(course_ids))
            )
            for course in courses_result.scalars().all():
                courses_map[course.id] = course

        # Build response with full details
        items_data = []
        for item in timetable.items:
            section = item.course_section
            course = courses_map.get(section.course_id) if section else None

            item_data = {
                "id": str(item.id),
                "course_section_id": str(item.course_section_id),
                "changed": item.changed,
                "deleted": item.deleted,
                "added": item.added,
                "changed_at": item.changed_at.isoformat() if item.changed_at else None,
                "changed_by_id": str(item.changed_by_id)
                if item.changed_by_id
                else None,
                "change_note": item.change_note,
                "section": {
                    "id": str(section.id),
                    "class_nbr": section.class_nbr,
                    "section": section.section,
                    "component": section.component,
                    "day": section.day,
                    "class_pattern": section.class_pattern,
                    "mtg_start": section.mtg_start.strftime("%H:%M")
                    if section.mtg_start
                    else None,
                    "mtg_end": section.mtg_end.strftime("%H:%M")
                    if section.mtg_end
                    else None,
                    "exam_date": section.exam_date.isoformat()
                    if section.exam_date
                    else None,
                    "exam_start": section.exam_start.strftime("%H:%M")
                    if section.exam_start
                    else None,
                    "exam_end": section.exam_end.strftime("%H:%M")
                    if section.exam_end
                    else None,
                    "instructor": section.instructor,
                    "room": section.room,
                    "cap_enrl": section.cap_enrl,
                    "tot_enrl": section.tot_enrl,
                }
                if section
                else None,
                "course": {
                    "id": str(course.id),
                    "course_id": course.course_id,
                    "subject": course.subject,
                    "catalog": course.catalog,
                    "title": course.title,
                    "max_units": float(course.max_units) if course.max_units else None,
                }
                if course
                else None,
            }
            items_data.append(item_data)

        return RegistrationTimetableWithCourseInfo(
            id=timetable.id,
            student_id=timetable.student_id,
            student_campus_id=student.campus_id,
            student_name=student.name,
            created_at=timetable.created_at,
            updated_at=timetable.updated_at,
            total_units=float(timetable.total_units) if timetable.total_units else None,
            items=items_data,
        )


@router.put("", response_model=RegistrationTimetableUpdateResponse)
async def update_registration_timetable(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    request: RegistrationTimetableUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update a registration timetable via visual editor.

    Frontend sends the complete list of section_ids that should be in the timetable.
    Backend compares with current state and:
    - Marks removed items as deleted=True
    - Adds new items with added=True
    - Validates all section_ids exist
    - Updates enrollment counts accordingly
    """
    # Get session
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Staff can only modify enabled sessions
    if current_user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Cannot modify disabled session")

    async for session_db in get_session_db(session.schema_name):
        # Get registration timetable
        timetable_result = await session_db.execute(
            select(RegistrationTimetable)
            .where(RegistrationTimetable.student_id == student_id)
            .options(selectinload(RegistrationTimetable.items))
        )
        timetable = timetable_result.scalar_one_or_none()

        if not timetable:
            raise HTTPException(
                status_code=404, detail="No registration timetable found for student"
            )

        # Validate all section_ids exist
        new_section_ids = set(request.section_ids)
        if new_section_ids:
            sections_result = await session_db.execute(
                select(CourseSection.id).where(CourseSection.id.in_(new_section_ids))
            )
            existing_section_ids = {row[0] for row in sections_result.fetchall()}
            missing_ids = new_section_ids - existing_section_ids
            if missing_ids:
                return RegistrationTimetableUpdateResponse(
                    success=False,
                    message="Some section IDs not found",
                    errors=[
                        f"Section IDs not found: {[str(sid) for sid in list(missing_ids)[:10]]}"
                    ],
                )

        # Get current state (non-deleted items)
        current_section_ids = {
            item.course_section_id for item in timetable.items if not item.deleted
        }

        # Calculate changes
        sections_to_add = new_section_ids - current_section_ids
        sections_to_remove = current_section_ids - new_section_ids

        changes = []
        now = datetime.now(timezone.utc)

        # Handle removals (mark as deleted)
        for item in timetable.items:
            if item.course_section_id in sections_to_remove and not item.deleted:
                item.deleted = True
                item.changed_at = now
                item.changed_by_id = current_user.id
                item.change_note = "Removed via visual editor"

                # Decrement enrollment count
                section = await session_db.get(CourseSection, item.course_section_id)
                if section and section.tot_enrl > 0:
                    section.tot_enrl -= 1

                changes.append(
                    {
                        "action": "removed",
                        "section_id": str(item.course_section_id),
                        "class_nbr": section.class_nbr if section else None,
                    }
                )

        # Handle additions
        for section_id in sections_to_add:
            # Check if this section was previously deleted (restore it)
            existing_item = None
            for item in timetable.items:
                if item.course_section_id == section_id and item.deleted:
                    existing_item = item
                    break

            if existing_item:
                # Restore previously deleted item
                existing_item.deleted = False
                existing_item.changed = True
                existing_item.changed_at = now
                existing_item.changed_by_id = current_user.id
                existing_item.change_note = "Restored via visual editor"
            else:
                # Create new item
                new_item = RegistrationTimetableItem(
                    timetable_id=timetable.id,
                    course_section_id=section_id,
                    changed=False,
                    deleted=False,
                    added=True,
                    changed_at=now,
                    changed_by_id=current_user.id,
                    change_note="Added via visual editor",
                )
                session_db.add(new_item)

            # Increment enrollment count
            section = await session_db.get(CourseSection, section_id)
            if section:
                section.tot_enrl += 1

            changes.append(
                {
                    "action": "added",
                    "section_id": str(section_id),
                    "class_nbr": section.class_nbr if section else None,
                }
            )

        # Update timetable metadata
        timetable.updated_at = now
        timetable.updated_by_id = current_user.id

        await session_db.commit()

        # Reload timetable for response
        await session_db.refresh(timetable)

        return RegistrationTimetableUpdateResponse(
            success=True,
            message=f"Updated timetable: {len(sections_to_add)} added, {len(sections_to_remove)} removed",
            changes=changes,
        )


@router.get("/active-items")
async def get_active_registration_items(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get only active (non-deleted) registration timetable items.

    Useful for displaying the current effective timetable.
    """
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Use read-only session for better concurrency
    async for session_db in get_session_db_read_only(session.schema_name):
        # Get registration timetable
        timetable_result = await session_db.execute(
            select(RegistrationTimetable)
            .where(RegistrationTimetable.student_id == student_id)
            .options(
                selectinload(RegistrationTimetable.items).selectinload(
                    RegistrationTimetableItem.course_section
                )
            )
        )
        timetable = timetable_result.scalar_one_or_none()

        if not timetable:
            raise HTTPException(
                status_code=404, detail="No registration timetable found"
            )

        # Filter to active items only
        active_items = [item for item in timetable.items if not item.deleted]

        # Get course info
        course_ids = {
            item.course_section.course_id
            for item in active_items
            if item.course_section
        }
        courses_map = {}
        if course_ids:
            courses_result = await db.execute(
                select(Course).where(Course.id.in_(course_ids))
            )
            courses_map = {c.id: c for c in courses_result.scalars().all()}

        items_data = []
        for item in active_items:
            section = item.course_section
            course = courses_map.get(section.course_id) if section else None
            items_data.append(
                {
                    "id": str(item.id),
                    "course_section_id": str(item.course_section_id),
                    "class_nbr": section.class_nbr if section else None,
                    "section": section.section if section else None,
                    "component": section.component if section else None,
                    "day": section.day if section else None,
                    "mtg_start": section.mtg_start.strftime("%H:%M")
                    if section and section.mtg_start
                    else None,
                    "mtg_end": section.mtg_end.strftime("%H:%M")
                    if section and section.mtg_end
                    else None,
                    "instructor": section.instructor if section else None,
                    "room": section.room if section else None,
                    "course_subject": course.subject if course else None,
                    "course_catalog": course.catalog if course else None,
                    "course_title": course.title if course else None,
                    "added": item.added,
                    "changed": item.changed,
                }
            )

        return {
            "timetable_id": str(timetable.id),
            "student_id": str(student_id),
            "item_count": len(items_data),
            "items": items_data,
        }


@router.get("/changes")
async def get_registration_timetable_changes(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get only the changed items (added/deleted/modified) for a registration timetable.

    Useful for reviewing what will be exported.
    """
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Use read-only session for better concurrency
    async for session_db in get_session_db_read_only(session.schema_name):
        # Get items with changes
        items_result = await session_db.execute(
            select(RegistrationTimetableItem)
            .join(RegistrationTimetable)
            .where(
                RegistrationTimetable.student_id == student_id,
                (RegistrationTimetableItem.added == True)  # noqa: E712
                | (RegistrationTimetableItem.deleted == True)  # noqa: E712
                | (RegistrationTimetableItem.changed == True),  # noqa: E712
            )
            .options(selectinload(RegistrationTimetableItem.course_section))
        )
        changed_items = items_result.scalars().all()

        added = []
        deleted = []
        modified = []

        for item in changed_items:
            section = item.course_section
            item_data = {
                "id": str(item.id),
                "class_nbr": section.class_nbr if section else None,
                "section": section.section if section else None,
                "component": section.component if section else None,
                "changed_at": item.changed_at.isoformat() if item.changed_at else None,
                "change_note": item.change_note,
            }

            if item.deleted:
                deleted.append(item_data)
            elif item.added:
                added.append(item_data)
            elif item.changed:
                modified.append(item_data)

        return {
            "student_id": str(student_id),
            "added_count": len(added),
            "deleted_count": len(deleted),
            "modified_count": len(modified),
            "added": added,
            "deleted": deleted,
            "modified": modified,
        }
