"""
Course cache service for fast in-memory search.

Loads all courses into memory and provides instant search capabilities
without hitting the database on every request.
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course


@dataclass
class CachedCourse:
    """Cached course data for fast search."""

    id: uuid.UUID
    course_id: str
    subject: str
    catalog: str
    title: str
    max_units: float | None
    created_at: datetime
    # Pre-computed search fields (lowercase)
    subject_lower: str
    catalog_lower: str
    title_lower: str
    combined_lower: str  # subject + catalog + title
    course_code_lower: str  # subject + catalog (no space)


class CourseCache:
    """In-memory cache for courses with smart search."""

    def __init__(self):
        self._courses: list[CachedCourse] = []
        self._courses_by_id: dict[str, CachedCourse] = {}
        self._last_refresh: datetime | None = None
        self._lock = asyncio.Lock()
        self._initialized = False

    async def refresh(self, db: AsyncSession) -> None:
        """Refresh the cache from database."""
        async with self._lock:
            result = await db.execute(
                select(Course).order_by(Course.subject, Course.catalog)
            )
            courses = result.scalars().all()

            self._courses = []
            self._courses_by_id = {}

            for course in courses:
                cached = CachedCourse(
                    id=course.id,
                    course_id=course.course_id,
                    subject=course.subject,
                    catalog=course.catalog,
                    title=course.title,
                    max_units=course.max_units,
                    created_at=course.created_at,
                    subject_lower=course.subject.lower(),
                    catalog_lower=course.catalog.lower(),
                    title_lower=course.title.lower(),
                    combined_lower=f"{course.subject} {course.catalog} {course.title}".lower(),
                    course_code_lower=f"{course.subject}{course.catalog}".lower(),
                )
                self._courses.append(cached)
                self._courses_by_id[str(course.id)] = cached

            self._last_refresh = datetime.now(timezone.utc)
            self._initialized = True

    async def ensure_initialized(self, db: AsyncSession) -> None:
        """Ensure cache is initialized, refreshing if needed."""
        if not self._initialized:
            await self.refresh(db)

    def invalidate(self) -> None:
        """Invalidate the cache, forcing a refresh on next access."""
        self._initialized = False
        self._courses = []
        self._courses_by_id = {}

    def search(self, query: str | None) -> list[CachedCourse]:
        """
        Search courses with smart multi-word query support.

        Supports patterns like:
        - "CS F2" → matches CS F211, CS F213, etc.
        - "data structures" → matches title containing both words
        - "CS" → matches all CS courses
        - "F211" → matches catalog F211
        """
        if not query or not query.strip():
            return self._courses

        # Normalize query
        query = " ".join(query.split()).lower()
        tokens = query.split()

        results = []

        for course in self._courses:
            if self._matches(course, query, tokens):
                results.append(course)

        return results

    def _matches(self, course: CachedCourse, query: str, tokens: list[str]) -> bool:
        """Check if a course matches the search query."""
        if len(tokens) == 1:
            # Single token: match subject/catalog with prefix, title with word match
            token = tokens[0]

            # Subject/catalog: exact or prefix match
            if (
                course.subject_lower == token
                or course.subject_lower.startswith(token)
                or course.catalog_lower.startswith(token)
            ):
                return True

            # Title: word-based matching (avoid matching "cs" in "mathematics")
            # For short tokens (<= 3 chars), require word boundary match
            title_words = course.title_lower.split()
            if len(token) <= 3:
                # Short token: must match start of a word
                if any(word.startswith(token) for word in title_words):
                    return True
            else:
                # Longer token: substring match is fine
                if token in course.title_lower:
                    return True

            return False

        # Multi-token search strategies
        first_token = tokens[0]
        rest_tokens = " ".join(tokens[1:])
        rest_tokens_no_space = "".join(tokens[1:])

        # Strategy 1: "CS F2" → subject=CS, catalog starts with F2
        if course.subject_lower == first_token and course.catalog_lower.startswith(
            rest_tokens
        ):
            return True

        # Strategy 2: "CS F2" → subject=CS, catalog starts with F2 (no space)
        if course.subject_lower == first_token and course.catalog_lower.startswith(
            rest_tokens_no_space
        ):
            return True

        # Strategy 3: All tokens appear somewhere in combined fields
        if all(token in course.combined_lower for token in tokens):
            return True

        # Strategy 4: Exact course code match (e.g., "csf211" matches "CS F211")
        query_no_space = query.replace(" ", "")
        if query_no_space in course.course_code_lower:
            return True

        return False

    def get_by_id(self, course_id: str) -> CachedCourse | None:
        """Get a course by its ID."""
        return self._courses_by_id.get(course_id)

    @property
    def count(self) -> int:
        """Get the number of cached courses."""
        return len(self._courses)

    @property
    def last_refresh(self) -> datetime | None:
        """Get the last refresh timestamp."""
        return self._last_refresh


# Global cache instance
_course_cache = CourseCache()


async def get_course_cache(db: AsyncSession) -> CourseCache:
    """Get the course cache, initializing if needed."""
    await _course_cache.ensure_initialized(db)
    return _course_cache


def invalidate_course_cache() -> None:
    """Invalidate the course cache."""
    _course_cache.invalidate()


async def search_courses_cached(
    db: AsyncSession, query: str | None = None
) -> list[CachedCourse]:
    """
    Search courses using the in-memory cache.

    Returns CachedCourse objects that can be directly converted to CourseResponse.
    No additional database queries after initial cache load.
    """
    cache = await get_course_cache(db)
    return cache.search(query)
