"""Conflict detection for timetable generation"""

from datetime import date, time
from typing import NamedTuple

from app.core.utils import time_to_minutes


class TimeSlot(NamedTuple):
    """Represents a time slot"""

    day: str
    start_minutes: int
    end_minutes: int


class Conflict(NamedTuple):
    """Represents a scheduling conflict"""

    type: str  # "time_clash", "exam_clash", "unit_limit"
    message: str
    section_ids: list[str]


class ConflictDetector:
    """Detects conflicts in timetable assignments"""

    # Resolution: 5 minutes (as per old system)
    RESOLUTION_MINUTES = 5
    # Total slots in a day (24 hours * 60 minutes / 5 minutes)
    SLOTS_PER_DAY = (24 * 60) // RESOLUTION_MINUTES
    # Days of the week
    DAYS = [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]

    def __init__(self):
        """Initialize conflict detector with empty bitsets"""
        # Bitset for each day (using list of booleans)
        self.day_bitsets: dict[str, list[bool]] = {
            day: [False] * self.SLOTS_PER_DAY for day in self.DAYS
        }
        # Track exam dates
        self.exam_dates: set[date] = set()
        # Track total units
        self.total_units: float = 0.0
        # Track assigned sections
        self.assigned_sections: list[dict] = []

    def _time_to_slot(self, t: time) -> int:
        """Convert time to slot index"""
        minutes = time_to_minutes(t)
        return minutes // self.RESOLUTION_MINUTES

    def add_section(
        self,
        section_id: str,
        day: str,
        start: time | None,
        end: time | None,
        exam_date: date | None,
        units: float = 0.0,
    ) -> list[Conflict]:
        """
        Add a section and check for conflicts.

        Returns list of conflicts (empty if no conflicts).
        """
        conflicts: list[Conflict] = []

        # Check time clash
        if start and end and day in self.day_bitsets:
            start_slot = self._time_to_slot(start)
            end_slot = self._time_to_slot(end)

            # Check if any slot is already occupied
            for slot in range(start_slot, end_slot):
                if self.day_bitsets[day][slot]:
                    conflicts.append(
                        Conflict(
                            type="time_clash",
                            message=f"Time clash on {day} at {start}-{end}",
                            section_ids=[section_id],
                        )
                    )
                    break

            # If no conflict, mark slots as occupied
            if not conflicts:
                for slot in range(start_slot, end_slot):
                    self.day_bitsets[day][slot] = True

        # Check exam clash
        if exam_date:
            if exam_date in self.exam_dates:
                conflicts.append(
                    Conflict(
                        type="exam_clash",
                        message=f"Exam clash on {exam_date}",
                        section_ids=[section_id],
                    )
                )
            else:
                self.exam_dates.add(exam_date)

        # Track units
        self.total_units += units

        # Track assigned section
        if not conflicts:
            self.assigned_sections.append(
                {
                    "section_id": section_id,
                    "day": day,
                    "start": start,
                    "end": end,
                    "exam_date": exam_date,
                    "units": units,
                }
            )

        return conflicts

    def check_unit_limit(self, max_units: float = 30.0) -> list[Conflict]:
        """Check if total units exceed limit"""
        if self.total_units > max_units:
            return [
                Conflict(
                    type="unit_limit",
                    message=f"Total units ({self.total_units}) exceeds limit ({max_units})",
                    section_ids=[],
                )
            ]
        return []

    def get_total_units(self) -> float:
        """Get total units assigned"""
        return self.total_units

    def reset(self) -> None:
        """Reset the detector"""
        self.day_bitsets = {day: [False] * self.SLOTS_PER_DAY for day in self.DAYS}
        self.exam_dates = set()
        self.total_units = 0.0
        self.assigned_sections = []
