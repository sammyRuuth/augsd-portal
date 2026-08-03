"""Saved Timetable Drafts API routes"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.database import get_session_db
from app.models.saved_timetable_draft import SavedTimetableDraft
from app.models.user import User
from app.schemas.common import Message
from app.schemas.saved_timetable_draft import (
    SavedDraftCreate,
    SavedDraftListResponse,
    SavedDraftResponse,
    SavedDraftUpdate,
)
from app.services.session_service import get_session_by_id

router = APIRouter(
    prefix="/api/sessions/{session_id}/students/{student_id}/saved-drafts",
    tags=["saved-drafts"],
)


@router.get("", response_model=SavedDraftListResponse)
async def list_saved_drafts(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all saved timetable drafts for a student"""
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    async for session_db in get_session_db(session.schema_name):
        result = await session_db.execute(
            select(SavedTimetableDraft)
            .where(SavedTimetableDraft.student_id == student_id)
            .order_by(SavedTimetableDraft.created_at.desc())
        )
        drafts = result.scalars().all()

        return SavedDraftListResponse(
            drafts=[
                SavedDraftResponse(
                    id=d.id,
                    student_id=d.student_id,
                    name=d.name,
                    notes=d.notes,
                    selected_course_ids=d.selected_course_ids,
                    pinned_sections=d.pinned_sections,
                    seat_preferences=d.seat_preferences,
                    created_at=d.created_at,
                    updated_at=d.updated_at,
                    created_by_id=d.created_by_id,
                )
                for d in drafts
            ],
            total=len(drafts),
        )


@router.post("", response_model=SavedDraftResponse)
async def create_saved_draft(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    request: SavedDraftCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new saved timetable draft"""
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if current_user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Cannot modify disabled session")

    async for session_db in get_session_db(session.schema_name):
        now = datetime.now(timezone.utc)
        draft_id = uuid.uuid4()
        course_ids_list = [str(cid) for cid in request.selected_course_ids]

        # Create the draft
        draft = SavedTimetableDraft(
            id=draft_id,
            student_id=student_id,
            name=request.name,
            notes=request.notes,
            selected_course_ids=course_ids_list,
            pinned_sections=request.pinned_sections,
            seat_preferences=request.seat_preferences,
            created_at=now,
            created_by_id=current_user.id,
        )

        session_db.add(draft)
        await session_db.commit()

        return SavedDraftResponse(
            id=draft_id,
            student_id=student_id,
            name=request.name,
            notes=request.notes,
            selected_course_ids=course_ids_list,
            pinned_sections=request.pinned_sections,
            seat_preferences=request.seat_preferences,
            created_at=now,
            updated_at=None,
            created_by_id=current_user.id,
        )


@router.get("/{draft_id}", response_model=SavedDraftResponse)
async def get_saved_draft(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    draft_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a specific saved timetable draft"""
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    async for session_db in get_session_db(session.schema_name):
        result = await session_db.execute(
            select(SavedTimetableDraft).where(
                SavedTimetableDraft.id == draft_id,
                SavedTimetableDraft.student_id == student_id,
            )
        )
        draft = result.scalar_one_or_none()

        if not draft:
            raise HTTPException(status_code=404, detail="Draft not found")

        return SavedDraftResponse(
            id=draft.id,
            student_id=draft.student_id,
            name=draft.name,
            notes=draft.notes,
            selected_course_ids=draft.selected_course_ids,
            pinned_sections=draft.pinned_sections,
            seat_preferences=draft.seat_preferences,
            created_at=draft.created_at,
            updated_at=draft.updated_at,
            created_by_id=draft.created_by_id,
        )


@router.put("/{draft_id}", response_model=SavedDraftResponse)
async def update_saved_draft(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    draft_id: uuid.UUID,
    request: SavedDraftUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a saved timetable draft"""
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if current_user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Cannot modify disabled session")

    async for session_db in get_session_db(session.schema_name):
        result = await session_db.execute(
            select(SavedTimetableDraft).where(
                SavedTimetableDraft.id == draft_id,
                SavedTimetableDraft.student_id == student_id,
            )
        )
        draft = result.scalar_one_or_none()

        if not draft:
            raise HTTPException(status_code=404, detail="Draft not found")

        # Update fields
        if request.name is not None:
            draft.name = request.name
        if request.notes is not None:
            draft.notes = request.notes
        if request.selected_course_ids is not None:
            draft.selected_course_ids = [
                str(cid) for cid in request.selected_course_ids
            ]
        if request.pinned_sections is not None:
            draft.pinned_sections = request.pinned_sections
        if request.seat_preferences is not None:
            draft.seat_preferences = request.seat_preferences

        now = datetime.now(timezone.utc)
        draft.updated_at = now

        # Capture values before commit
        response_data = SavedDraftResponse(
            id=draft.id,
            student_id=draft.student_id,
            name=draft.name,
            notes=draft.notes,
            selected_course_ids=draft.selected_course_ids,
            pinned_sections=draft.pinned_sections,
            seat_preferences=draft.seat_preferences,
            created_at=draft.created_at,
            updated_at=now,
            created_by_id=draft.created_by_id,
        )

        await session_db.commit()

        return response_data


@router.delete("/{draft_id}", response_model=Message)
async def delete_saved_draft(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    draft_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a saved timetable draft"""
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if current_user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Cannot modify disabled session")

    async for session_db in get_session_db(session.schema_name):
        result = await session_db.execute(
            delete(SavedTimetableDraft).where(
                SavedTimetableDraft.id == draft_id,
                SavedTimetableDraft.student_id == student_id,
            )
        )

        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Draft not found")

        await session_db.commit()
        return Message(message="Draft deleted successfully")
