"""Audit trail service for tracking timetable modifications"""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.timetable_audit_trail import TimetableAuditTrail


async def log_audit_trail(
    db: AsyncSession,
    timetable_id: uuid.UUID,
    action: str,
    changed_by_id: uuid.UUID,
    details: dict[str, Any] | None = None,
    note: str | None = None,
) -> TimetableAuditTrail:
    """
    Log a timetable change to the audit trail.

    Args:
        db: Database session
        timetable_id: ID of the timetable being modified
        action: Type of action (add_course, remove_course, swap_section, commit, uncommit, initialize)
        changed_by_id: User ID who made the change
        details: Structured JSON details about the change
        note: Optional human-readable note

    Returns:
        Created audit trail entry
    """
    audit_entry = TimetableAuditTrail(
        timetable_id=timetable_id,
        action=action,
        details=details,
        changed_by_id=changed_by_id,
        note=note,
        changed_at=datetime.now(timezone.utc),
    )

    db.add(audit_entry)
    await db.flush()
    await db.refresh(audit_entry)

    return audit_entry


async def log_initialize_from_registration(
    db: AsyncSession,
    timetable_id: uuid.UUID,
    changed_by_id: uuid.UUID,
    section_ids: list[uuid.UUID],
    total_units: float,
) -> TimetableAuditTrail:
    """Log initialization of timetable from registration data"""
    return await log_audit_trail(
        db=db,
        timetable_id=timetable_id,
        action="initialize",
        changed_by_id=changed_by_id,
        details={
            "source": "registration",
            "section_ids": [str(sid) for sid in section_ids],
            "total_units": total_units,
        },
        note="Initialized from registration data upload",
    )


async def log_commit_timetable(
    db: AsyncSession,
    timetable_id: uuid.UUID,
    changed_by_id: uuid.UUID,
    section_ids: list[uuid.UUID],
    total_units: float,
    note: str | None = None,
) -> TimetableAuditTrail:
    """Log timetable commit action"""
    return await log_audit_trail(
        db=db,
        timetable_id=timetable_id,
        action="commit",
        changed_by_id=changed_by_id,
        details={
            "section_ids": [str(sid) for sid in section_ids],
            "total_units": total_units,
        },
        note=note or "Timetable committed",
    )


async def log_uncommit_timetable(
    db: AsyncSession,
    timetable_id: uuid.UUID,
    changed_by_id: uuid.UUID,
) -> TimetableAuditTrail:
    """Log timetable uncommit action"""
    return await log_audit_trail(
        db=db,
        timetable_id=timetable_id,
        action="uncommit",
        changed_by_id=changed_by_id,
        note="Timetable uncommitted",
    )


async def log_transfer_timetable(
    db: AsyncSession,
    timetable_id: uuid.UUID,
    changed_by_id: uuid.UUID,
    from_student_id: uuid.UUID,
    to_student_id: uuid.UUID,
    class_nbrs: list[int],
) -> TimetableAuditTrail:
    """Log timetable transfer between students."""
    return await log_audit_trail(
        db=db,
        timetable_id=timetable_id,
        action="transfer",
        changed_by_id=changed_by_id,
        details={
            "from_student_id": str(from_student_id),
            "to_student_id": str(to_student_id),
            "class_nbrs": class_nbrs,
        },
        note="Timetable transferred to another student",
    )


async def log_add_course(
    db: AsyncSession,
    timetable_id: uuid.UUID,
    changed_by_id: uuid.UUID,
    course_id: uuid.UUID,
    section_ids: list[uuid.UUID],
) -> TimetableAuditTrail:
    """Log course addition to timetable"""
    return await log_audit_trail(
        db=db,
        timetable_id=timetable_id,
        action="add_course",
        changed_by_id=changed_by_id,
        details={
            "course_id": str(course_id),
            "section_ids": [str(sid) for sid in section_ids],
        },
        note=f"Added course {course_id}",
    )


async def log_remove_course(
    db: AsyncSession,
    timetable_id: uuid.UUID,
    changed_by_id: uuid.UUID,
    course_id: uuid.UUID,
    section_ids: list[uuid.UUID],
) -> TimetableAuditTrail:
    """Log course removal from timetable"""
    return await log_audit_trail(
        db=db,
        timetable_id=timetable_id,
        action="remove_course",
        changed_by_id=changed_by_id,
        details={
            "course_id": str(course_id),
            "section_ids": [str(sid) for sid in section_ids],
        },
        note=f"Removed course {course_id}",
    )


async def log_swap_section(
    db: AsyncSession,
    timetable_id: uuid.UUID,
    changed_by_id: uuid.UUID,
    course_id: uuid.UUID,
    old_section_id: uuid.UUID,
    new_section_id: uuid.UUID,
) -> TimetableAuditTrail:
    """Log section swap in timetable"""
    return await log_audit_trail(
        db=db,
        timetable_id=timetable_id,
        action="swap_section",
        changed_by_id=changed_by_id,
        details={
            "course_id": str(course_id),
            "old_section_id": str(old_section_id),
            "new_section_id": str(new_section_id),
        },
        note=f"Swapped section for course {course_id}",
    )


async def log_multi_swap(
    db: AsyncSession,
    timetable_id: uuid.UUID,
    changed_by_id: uuid.UUID,
    course_id: uuid.UUID,
    swaps: list[tuple[uuid.UUID, uuid.UUID]],  # [(old_section_id, new_section_id), ...]
) -> TimetableAuditTrail:
    """Log multi-section swap in timetable"""
    return await log_audit_trail(
        db=db,
        timetable_id=timetable_id,
        action="multi_swap",
        changed_by_id=changed_by_id,
        details={
            "course_id": str(course_id),
            "swaps": [
                {"old_section_id": str(old_id), "new_section_id": str(new_id)}
                for old_id, new_id in swaps
            ],
            "swap_count": len(swaps),
        },
        note=f"Swapped {len(swaps)} section(s) for course {course_id}",
    )


async def get_audit_trail(
    db: AsyncSession,
    timetable_id: uuid.UUID,
    limit: int | None = None,
) -> list[TimetableAuditTrail]:
    """
    Get audit trail for a timetable, ordered by most recent first.

    Args:
        db: Database session
        timetable_id: ID of the timetable
        limit: Optional limit on number of entries to return

    Returns:
        List of audit trail entries
    """
    query = (
        select(TimetableAuditTrail)
        .where(TimetableAuditTrail.timetable_id == timetable_id)
        .order_by(TimetableAuditTrail.changed_at.desc())
    )

    if limit:
        query = query.limit(limit)

    result = await db.execute(query)
    return list(result.scalars().all())


async def get_latest_change(
    db: AsyncSession,
    timetable_id: uuid.UUID,
) -> TimetableAuditTrail | None:
    """Get the most recent audit trail entry for a timetable"""
    result = await db.execute(
        select(TimetableAuditTrail)
        .where(TimetableAuditTrail.timetable_id == timetable_id)
        .order_by(TimetableAuditTrail.changed_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
