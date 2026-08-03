"""API routes"""

from app.api import (
    admin,
    auth,
    courses,
    export,
    frontend,
    registration_timetables,
    sessions,
    statistics,
    students,
    timetable_buckets,
    timetables,
)

__all__ = [
    "admin",
    "auth",
    "courses",
    "export",
    "frontend",
    "registration_timetables",
    "sessions",
    "statistics",
    "students",
    "timetable_buckets",
    "timetables",
]
