"""Capacity-based timetable bucket generation (session-scoped)."""

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.database import get_session_db, get_session_db_read_only
from app.models.user import User
from app.schemas.timetable import (
    TimetableBucketGenerationRequest,
    TimetableBucketGenerationResponse,
    TimetableBucketSaveRequest,
    TimetableBucketSaveResponse,
    TimetableConflictDetail,
)
from app.services.session_service import get_session_by_id
from app.services.timetable_bucket_save_service import (
    BucketSaveInput,
    apply_bucket_save_limits,
    save_buckets_as_buffer_timetables,
)
from app.services.timetable_service import (
    fetch_sections_for_generation,
    run_bucket_enumeration,
)

router = APIRouter(
    prefix="/api/sessions/{session_id}/timetable-buckets",
    tags=["timetable-buckets"],
)


@router.post("/generate", response_model=TimetableBucketGenerationResponse)
async def generate_timetable_buckets(
    session_id: uuid.UUID,
    request: TimetableBucketGenerationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Enumerate all clash-free timetables for the given course set in this session.

    Each result bucket lists compatible LEC/LAB/TUT choices (same clash logic as
    single-student generation). ``capacity`` is the minimum available seats across
    those sections, i.e. how many students can take that exact combination without
    exceeding the tightest section cap.

    Existing per-student timetable generation endpoints are unchanged.
    """
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if current_user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Cannot use disabled session")

    branches = request.branch_codes if request.branch_codes else None

    sections_by_course = None
    async for session_db in get_session_db_read_only(session.schema_name):
        sections_by_course, error_msg, courses_with_issues = (
            await fetch_sections_for_generation(
                db, session_db, request.course_ids, branches
            )
        )
        if error_msg:
            return TimetableBucketGenerationResponse(
                success=False,
                message=error_msg,
                conflicts=[
                    TimetableConflictDetail(
                        type="missing_sections",
                        message=error_msg,
                        courses=courses_with_issues,
                    )
                ],
            )

    fixed_payload = None
    if request.fixed_sections:
        fixed_payload = [
            {
                "course_id": fs.course_id,
                "component": fs.component,
                "class_nbr": fs.class_nbr,
            }
            for fs in request.fixed_sections
        ]

    result = await asyncio.to_thread(
        run_bucket_enumeration,
        sections_by_course,
        request.max_units,
        request.max_buckets,
        request.max_search_nodes,
        fixed_payload,
    )

    return result


@router.post("/save", response_model=TimetableBucketSaveResponse)
async def save_timetable_buckets_to_buffer(
    session_id: uuid.UUID,
    request: TimetableBucketSaveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Store buckets as ``buffer_timetables`` rows (same store used for CSV uploads).

    After saving, open a student’s generate page → **Assign timetable**; buffers for
    that student’s branch appear if the **plan** string matches the branch filter
    (exact branch code, comma-separated plans, or any plan substring containing the branch).
    """
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if current_user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Cannot use disabled session")

    inputs = [
        BucketSaveInput(capacity=b.capacity, section_ids=list(b.section_ids))
        for b in request.buckets
    ]
    limited = apply_bucket_save_limits(
        inputs,
        max_buckets_to_save=request.max_buckets_to_save,
        max_total_capacity=request.max_total_capacity,
    )
    if not limited:
        if request.max_total_capacity is not None:
            detail = (
                f"Cannot reserve {request.max_total_capacity} seat(s) from the generated "
                "buckets (no exact match, no bucket large enough, and smaller buckets "
                "cannot sum to the target)."
            )
        else:
            detail = "No buckets to save (need capacity > 0)."
        raise HTTPException(status_code=400, detail=detail)

    async for session_db in get_session_db(session.schema_name):
        try:
            created, skipped, warnings = await save_buckets_as_buffer_timetables(
                db,
                session_db,
                plan=request.plan.strip(),
                buckets=limited,
                created_by_id=current_user.id,
                enrollment_deducted_on_upload=request.enrollment_deducted_on_upload,
            )
            await session_db.commit()
        except Exception as e:
            await session_db.rollback()
            raise HTTPException(
                status_code=400, detail=f"Failed to save buckets: {e!s}"
            ) from e
        break

    msg = f"Saved {created} buffer timetable(s)"
    if skipped:
        msg += f"; {skipped} skipped"

    return TimetableBucketSaveResponse(
        success=created > 0,
        created=created,
        skipped=skipped,
        message=msg,
        warnings=warnings,
    )
