"""Frontend routes for serving Jinja2 templates"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.database import get_db
from app.models.user import User
from app.services.session_service import get_session_by_id
from app.services.user_service import get_user_by_id

router = APIRouter(tags=["frontend"])
templates = Jinja2Templates(directory="app/templates")


async def get_optional_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Get current user from token cookie or header, returns None if not authenticated"""
    # Try to get token from cookie first
    token = request.cookies.get("access_token")

    # Also check Authorization header for API-like requests
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        return None

    payload = decode_access_token(token)
    if not payload:
        return None

    user_id_str = payload.get("sub")
    if not user_id_str:
        return None

    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        return None

    user = await get_user_by_id(db, user_id)
    if not user or not user.is_active:
        return None

    return user


# Public routes


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, db: AsyncSession = Depends(get_db)):
    """Home page - redirect to login or dashboard"""
    user = await get_optional_user(request, db)
    if user:
        if user.role == "admin":
            return RedirectResponse(url="/admin/dashboard", status_code=302)
        return RedirectResponse(url="/sessions", status_code=302)
    return RedirectResponse(url="/login", status_code=302)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Login page"""
    user = await get_optional_user(request, db)
    if user:
        if user.role == "admin":
            return RedirectResponse(url="/admin/dashboard", status_code=302)
        return RedirectResponse(url="/sessions", status_code=302)

    return templates.TemplateResponse("login.html", {"request": request})


@router.get("/logout", response_class=HTMLResponse)
async def logout(request: Request):
    """Logout - clear token and redirect to login"""
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("access_token")
    return response


# Admin routes


@router.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    """Admin dashboard"""
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role != "admin":
        return RedirectResponse(url="/sessions", status_code=302)

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "current_user": user,
        },
    )


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request, db: AsyncSession = Depends(get_db)):
    """User management page"""
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role != "admin":
        return RedirectResponse(url="/sessions", status_code=302)

    return templates.TemplateResponse(
        "admin/users.html",
        {
            "request": request,
            "current_user": user,
        },
    )


@router.get("/admin/packages", response_class=HTMLResponse)
async def admin_packages(request: Request, db: AsyncSession = Depends(get_db)):
    """Default packages management page"""
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role != "admin":
        return RedirectResponse(url="/sessions", status_code=302)

    return templates.TemplateResponse(
        "admin/packages.html",
        {
            "request": request,
            "current_user": user,
        },
    )


# Session routes


@router.get("/sessions", response_class=HTMLResponse)
async def sessions_list(request: Request, db: AsyncSession = Depends(get_db)):
    """Sessions list page"""
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    return templates.TemplateResponse(
        "sessions.html",
        {
            "request": request,
            "current_user": user,
        },
    )


@router.get("/session/{session_id}", response_class=HTMLResponse)
async def session_dashboard(
    session_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Session dashboard"""
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Staff can only access enabled sessions
    if user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Session is disabled")

    return templates.TemplateResponse(
        "session/dashboard.html",
        {
            "request": request,
            "current_user": user,
            "session": session,
        },
    )


@router.get("/session/{session_id}/students", response_class=HTMLResponse)
async def session_students(
    session_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Students list page"""
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Session is disabled")

    return templates.TemplateResponse(
        "session/students.html",
        {
            "request": request,
            "current_user": user,
            "session": session,
        },
    )


@router.get("/session/{session_id}/courses", response_class=HTMLResponse)
async def session_courses(
    session_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Courses catalog page"""
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return templates.TemplateResponse(
        "session/courses.html",
        {
            "request": request,
            "current_user": user,
            "session": session,
        },
    )


@router.get("/session/{session_id}/statistics", response_class=HTMLResponse)
async def session_statistics(
    session_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Statistics page"""
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return templates.TemplateResponse(
        "session/statistics.html",
        {
            "request": request,
            "current_user": user,
            "session": session,
        },
    )


@router.get("/session/{session_id}/timetable-buckets", response_class=HTMLResponse)
async def timetable_buckets_page(
    session_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Capacity-based timetable bucket enumeration (staff/admin)."""
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Session is disabled")

    return templates.TemplateResponse(
        "timetable/buckets.html",
        {
            "request": request,
            "current_user": user,
            "session": session,
        },
    )


# Timetable routes


@router.get(
    "/session/{session_id}/student/{student_id}/generate", response_class=HTMLResponse
)
async def generate_timetable_page(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Generate timetable page"""
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Cannot modify disabled session")

    return templates.TemplateResponse(
        "timetable/generate.html",
        {
            "request": request,
            "current_user": user,
            "session": session,
            "student_id": str(student_id),
        },
    )


@router.get(
    "/session/{session_id}/student/{student_id}/advanced-generate",
    response_class=HTMLResponse,
)
async def advanced_generate_timetable_page(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Advanced timetable generation page with algorithm selection and constraints"""
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Cannot modify disabled session")

    return templates.TemplateResponse(
        "timetable/advanced_generate.html",
        {
            "request": request,
            "current_user": user,
            "session": session,
            "student_id": str(student_id),
        },
    )


@router.get(
    "/session/{session_id}/student/{student_id}/timetable", response_class=HTMLResponse
)
async def view_timetable_page(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """View timetable page"""
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return templates.TemplateResponse(
        "timetable/view.html",
        {
            "request": request,
            "current_user": user,
            "session": session,
            "student_id": str(student_id),
        },
    )


@router.get(
    "/session/{session_id}/student/{student_id}/timetable/edit",
    response_class=HTMLResponse,
)
async def edit_timetable_page(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Edit timetable page - allows adding/removing courses and swapping sections"""
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if user.role == "staff" and not session.is_enabled:
        raise HTTPException(status_code=403, detail="Cannot modify disabled session")

    return templates.TemplateResponse(
        "timetable/edit.html",
        {
            "request": request,
            "current_user": user,
            "session": session,
            "student_id": str(student_id),
        },
    )


@router.get(
    "/session/{session_id}/student/{student_id}/registration-timetable",
    response_class=HTMLResponse,
)
async def view_registration_timetable_page(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """View registration timetable page"""
    user = await get_optional_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    session = await get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return templates.TemplateResponse(
        "timetable/view_registration.html",
        {
            "request": request,
            "current_user": user,
            "session": session,
            "student_id": str(student_id),
        },
    )
