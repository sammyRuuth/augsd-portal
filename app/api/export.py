"""Export API routes"""

import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.database import get_session_db_read_only
from app.models.user import User
from app.services.export_service import export_timetables, get_exports_dir
from app.services.session_service import get_session_by_id

router = APIRouter(prefix="/api/sessions/{session_id}/export", tags=["export"])

_TRANSFER_EXPORT_PATTERN = re.compile(r"^\d{4}_\d{4}_(delete|add|transfer)(\.xlsx|\.zip)?$")


@router.get("/transfer/{filename}")
async def download_transfer_export(
    session_id: uuid.UUID,
    filename: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Download a timetable transfer export file (xlsx or zip)."""
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if current_user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Session is disabled")

    if not _TRANSFER_EXPORT_PATTERN.match(filename):
        raise HTTPException(status_code=400, detail="Invalid export filename")

    exports_dir = get_exports_dir().resolve()
    file_path = (exports_dir / filename).resolve()
    if not str(file_path).startswith(str(exports_dir)) or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Export file not found")

    if filename.endswith(".zip"):
        media_type = "application/zip"
    else:
        media_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type=media_type,
    )


@router.get("")
async def export_session_timetables(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Export the session's ERP changes.

    Returns a ZIP holding two workbooks: one of rows to add in ERP and one of rows
    to delete. Timetable transfers are not included - they are exported separately
    at transfer time via /export/transfer/{filename}.
    """
    # Get session
    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get session database and export (read-only since we're just exporting)
    async for session_db in get_session_db_read_only(session.schema_name):
        file_path = await export_timetables(
            session_db, session.name, session.term_code, session.career
        )

        return FileResponse(
            path=str(file_path),
            filename=file_path.name,
            media_type="application/zip",
        )
