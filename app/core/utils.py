"""Utility functions"""

import random
import string
import uuid
from datetime import datetime, time, timezone
from pathlib import Path

from app.config import get_settings


def generate_schema_name() -> str:
    """Generate a unique schema name for a session"""
    return f"session_{uuid.uuid4().hex[:12]}"


def time_to_minutes(t: time) -> int:
    """Convert time to minutes since midnight"""
    return t.hour * 60 + t.minute


def minutes_to_time(minutes: int) -> time:
    """Convert minutes since midnight to time"""
    hours = minutes // 60
    mins = minutes % 60
    return time(hour=hours, minute=mins)


def time_overlap(start1: time, end1: time, start2: time, end2: time) -> bool:
    """Check if two time ranges overlap"""
    start1_mins = time_to_minutes(start1)
    end1_mins = time_to_minutes(end1)
    start2_mins = time_to_minutes(start2)
    end2_mins = time_to_minutes(end2)

    return start1_mins < end2_mins and start2_mins < end1_mins


def sanitize_filename(filename: str) -> str:
    """Sanitize a filename to remove dangerous characters"""
    # Remove path separators and other dangerous characters
    dangerous_chars = ["\\", "/", ":", "*", "?", '"', "<", ">", "|"]
    for char in dangerous_chars:
        filename = filename.replace(char, "_")
    return filename


def generate_unique_filename(original_filename: str) -> str:
    """Generate a unique filename by appending a 3-character alphanumeric identifier

    Args:
        original_filename: The original uploaded filename

    Returns:
        A unique filename with format: name_XXX.ext where XXX is 3 random chars

    Example:
        >>> generate_unique_filename("students.xlsx")
        "students_A7B.xlsx"
    """
    # Sanitize the filename first
    safe_filename = sanitize_filename(original_filename)

    # Get the file path object
    file_path = Path(safe_filename)

    # Generate 3-character unique ID
    unique_id = "".join(random.choices(string.ascii_uppercase + string.digits, k=3))

    # Split into stem and suffix
    # e.g., "students.xlsx" -> stem="students", suffix=".xlsx"
    stem = file_path.stem
    suffix = file_path.suffix

    # Return formatted filename
    return f"{stem}_{unique_id}{suffix}"


def get_timestamped_filename(original_filename: str) -> str:
    """Generate a unique filename with timestamp to prevent overwrites

    Args:
        original_filename: The original uploaded filename

    Returns:
        A unique filename with format: YYYYMMDD_HHMMSS_name_XXX.ext

    Example:
        >>> get_timestamped_filename("students.xlsx")
        "20260110_143052_students_A7B.xlsx"
    """
    # Sanitize the filename first
    safe_filename = sanitize_filename(original_filename)

    # Get the file path object
    file_path = Path(safe_filename)

    # Generate timestamp
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # Generate 3-character unique ID
    unique_id = "".join(random.choices(string.ascii_uppercase + string.digits, k=3))

    # Split into stem and suffix
    stem = file_path.stem
    suffix = file_path.suffix

    # Return formatted filename with timestamp prefix
    return f"{timestamp}_{stem}_{unique_id}{suffix}"


def get_session_upload_dir(session_name: str) -> Path:
    """Get the upload directory for a session

    Creates a sanitized session folder under uploads/sessions/

    Args:
        session_name: The session name (will be sanitized)

    Returns:
        Path to the session upload directory
    """
    settings = get_settings()
    base_dir = Path(settings.upload_dir)

    # Sanitize session name for directory
    safe_session_name = sanitize_filename(session_name).replace(" ", "_")

    # Create path: uploads/sessions/{session_name}/
    session_dir = base_dir / "sessions" / safe_session_name
    session_dir.mkdir(parents=True, exist_ok=True)

    return session_dir


def get_global_upload_dir() -> Path:
    """Get the upload directory for global files (admin uploads)

    Creates the global folder under uploads/global/

    Returns:
        Path to the global upload directory
    """
    settings = get_settings()
    base_dir = Path(settings.upload_dir)

    # Create path: uploads/global/
    global_dir = base_dir / "global"
    global_dir.mkdir(parents=True, exist_ok=True)

    return global_dir


async def save_upload_file(
    file_content: bytes,
    original_filename: str,
    session_name: str | None = None,
) -> Path:
    """Save an uploaded file to the appropriate directory

    Files are saved with timestamps to prevent overwrites.

    Args:
        file_content: The file content bytes
        original_filename: Original filename from upload
        session_name: If provided, saves to session folder. Otherwise saves to global.

    Returns:
        Path to the saved file
    """
    # Get the appropriate directory
    if session_name:
        upload_dir = get_session_upload_dir(session_name)
    else:
        upload_dir = get_global_upload_dir()

    # Generate unique timestamped filename
    unique_filename = get_timestamped_filename(original_filename)
    file_path = upload_dir / unique_filename

    # Write the file
    with open(file_path, "wb") as f:
        f.write(file_content)

    return file_path
