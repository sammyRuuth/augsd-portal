"""Timetable generation using backtracking algorithm"""

from typing import Any, NamedTuple

from app.core.conflict_detector import Conflict, ConflictDetector


class CourseSection(NamedTuple):
    """Simplified course section for algorithm"""

    id: str
    course_id: str
    section: str
    component: str
    day: str | None
    start: str | None  # time as string
    end: str | None  # time as string
    exam_date: str | None  # date as string
    units: float
    class_nbr: int | None = None  # Section identifier
    meetings: list[dict[str, Any]] | None = None  # All meeting times for this section


class TimetableResult(NamedTuple):
    """Result of timetable generation"""

    success: bool
    assigned_sections: list[str]  # Section IDs
    conflicts: list[Conflict]
    total_units: float


class TimetableGenerator:
    """
    Generate timetables using backtracking algorithm with constraint satisfaction.

    Handles sections with multiple meeting times (e.g., MW 10:00-10:50 + TH 15:00-15:50).
    """

    def __init__(self, max_units: float = 30.0):
        """Initialize the generator with unit limit"""
        self.max_units = max_units
        self.detector = ConflictDetector()

    def _parse_time(self, time_str: str | None):
        """Parse time string to time object"""
        if not time_str:
            return None
        from datetime import time

        parts = time_str.split(":")
        return time(int(parts[0]), int(parts[1]))

    def _parse_date(self, date_str: str | None):
        """Parse date string to date object"""
        if not date_str:
            return None
        from datetime import datetime

        return datetime.fromisoformat(date_str).date()

    def _add_section_meetings(self, section: CourseSection) -> list[Conflict]:
        """
        Add all meeting times for a section and return conflicts.

        A section may have multiple meeting times stored in the `meetings` field.
        If `meetings` is not provided, falls back to single day/start/end.
        """
        conflicts: list[Conflict] = []

        # If section has explicit meetings list, use that
        if section.meetings:
            for meeting in section.meetings:
                day = meeting.get("day")
                start = self._parse_time(meeting.get("start"))
                end = self._parse_time(meeting.get("end"))

                if day and start and end:
                    c = self.detector.add_section(
                        section_id=section.id,
                        day=day,
                        start=start,
                        end=end,
                        exam_date=self._parse_date(section.exam_date),
                        units=0,  # Only count units once
                    )
                    conflicts.extend(c)

            # Add units once for the whole section
            self.detector.total_units += section.units
        else:
            # Fallback to single meeting time
            conflicts = self.detector.add_section(
                section_id=section.id,
                day=section.day,
                start=self._parse_time(section.start),
                end=self._parse_time(section.end),
                exam_date=self._parse_date(section.exam_date),
                units=section.units,
            )

        return conflicts

    def generate(
        self, course_sections_by_course: dict[str, list[CourseSection]]
    ) -> TimetableResult:
        """
        Generate timetable using backtracking.

        Args:
            course_sections_by_course: Dict mapping course_id to list of its sections
                Each section may have multiple meeting times.

        Returns:
            TimetableResult with success status and assigned sections or conflicts
        """
        # Reset detector
        self.detector.reset()

        # Convert to list of courses for backtracking
        courses = list(course_sections_by_course.items())

        # Sort courses by constraint (most constrained first - fewer sections)
        courses.sort(key=lambda x: len(x[1]))

        assigned: list[str] = []

        def backtrack(course_idx: int) -> bool:
            """Recursive backtracking function"""
            # Base case: all courses assigned
            if course_idx >= len(courses):
                # Check unit limit
                unit_conflicts = self.detector.check_unit_limit(self.max_units)
                return len(unit_conflicts) == 0

            course_id, sections = courses[course_idx]

            # Try each section for this course
            for section in sections:
                # Create backup of detector state
                backup_bitsets = {
                    day: bits.copy() for day, bits in self.detector.day_bitsets.items()
                }
                backup_exams = self.detector.exam_dates.copy()
                backup_units = self.detector.total_units

                # Try adding all meeting times for this section
                conflicts = self._add_section_meetings(section)

                # If no conflicts, try next course
                if not conflicts:
                    assigned.append(section.id)

                    if backtrack(course_idx + 1):
                        return True

                    # Backtrack: remove this section
                    assigned.pop()

                # Restore detector state
                self.detector.day_bitsets = backup_bitsets
                self.detector.exam_dates = backup_exams
                self.detector.total_units = backup_units

            # No valid section found for this course
            return False

        # Run backtracking
        success = backtrack(0)

        if success:
            return TimetableResult(
                success=True,
                assigned_sections=assigned,
                conflicts=[],
                total_units=self.detector.get_total_units(),
            )
        else:
            # Try to find conflicts by attempting greedy assignment
            self.detector.reset()
            all_conflicts: list[Conflict] = []

            for course_id, sections in courses:
                if sections:
                    # Try first section to see conflicts
                    section = sections[0]
                    conflicts = self._add_section_meetings(section)
                    all_conflicts.extend(conflicts)

            # Check unit limit
            all_conflicts.extend(self.detector.check_unit_limit(self.max_units))

            return TimetableResult(
                success=False,
                assigned_sections=[],
                conflicts=all_conflicts,
                total_units=0.0,
            )
