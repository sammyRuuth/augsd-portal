"""
Constraint checking for timetable generation.

Provides efficient time clash detection using bitmasks and
capacity tracking across the generation process.
"""

from collections import defaultdict
from typing import Optional

from ..config import Config
from ..models import ComponentType, Section


def time_to_minutes(time_str: str) -> int:
    """Convert HH:MM to minutes since midnight."""
    try:
        parts = time_str.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError, AttributeError):
        return -1


def create_time_mask(start: str, end: str) -> int:
    """
    Create a bitmask for a time interval at 5-minute resolution.

    Each bit represents a 5-minute slot. Overlapping bits between
    two masks indicate a time conflict.

    Args:
        start: Start time in HH:MM format
        end: End time in HH:MM format

    Returns:
        Bitmask representing the time interval
    """
    if not start or not end:
        return 0

    start_min = time_to_minutes(start)
    end_min = time_to_minutes(end)

    if start_min < 0 or end_min <= start_min:
        return 0

    start_idx = start_min // 5
    end_idx = end_min // 5

    if end_idx <= start_idx:
        return 0

    width = end_idx - start_idx
    return ((1 << width) - 1) << start_idx


def sections_clash(section1: Section, section2: Section) -> bool:
    """
    Check if two sections have a time clash.

    Uses bitmask comparison for efficient overlap detection.

    Args:
        section1: First section
        section2: Second section

    Returns:
        True if sections clash
    """
    # Build day-specific masks for each section
    masks1: dict[str, int] = defaultdict(int)
    masks2: dict[str, int] = defaultdict(int)

    for meeting in section1.meetings:
        mask = create_time_mask(meeting.start, meeting.end)
        if mask:
            masks1[meeting.day] |= mask

    for meeting in section2.meetings:
        mask = create_time_mask(meeting.start, meeting.end)
        if mask:
            masks2[meeting.day] |= mask

    # Check for overlap on any day
    for day, mask1 in masks1.items():
        if mask1 and (mask1 & masks2.get(day, 0)):
            return True

    # Check exam clash
    if section1.exam_date and section2.exam_date:
        if section1.exam_date == section2.exam_date:
            return True

    return False


def combo_clashes_with_current(
    combo: list[Section],
    current: list[Section],
) -> bool:
    """
    Check if a combo of sections clashes with already selected sections.

    Args:
        combo: New sections to add
        current: Already selected sections

    Returns:
        True if any clash exists
    """
    for new_sec in combo:
        for curr_sec in current:
            if sections_clash(new_sec, curr_sec):
                return True
    return False


def has_internal_clash(combo: list[Section]) -> bool:
    """
    Check if sections within a combo clash with each other.

    Args:
        combo: List of sections

    Returns:
        True if any internal clash exists
    """
    for i in range(len(combo)):
        for j in range(i + 1, len(combo)):
            if sections_clash(combo[i], combo[j]):
                return True
    return False


class CapacityTracker:
    """
    Tracks remaining capacity across all sections during generation.

    Handles:
    - Initial capacity from Excel (minus enrolled)
    - Tutorial bonus seats
    - Overfill limits by component type
    - Course-specific restrictions
    """

    def __init__(
        self,
        sections_by_course: dict[str, list[Section]],
        config: Config,
    ):
        """
        Initialize capacity tracker.

        Args:
            sections_by_course: All available sections
            config: Configuration object
        """
        self.config = config
        self.remaining: dict[int, int] = {}
        self.original: dict[int, int] = {}
        self.section_lookup: dict[int, Section] = {}

        # Initialize capacity for all sections
        for sections in sections_by_course.values():
            for section in sections:
                available = section.available_seats

                # Add tutorial bonus seats
                if section.component == ComponentType.TUT:
                    available += config.capacity.tutorial_bonus_seats

                self.remaining[section.class_nbr] = available
                self.original[section.class_nbr] = section.capacity
                self.section_lookup[section.class_nbr] = section

    def get_remaining(self, class_nbr: int) -> int:
        """Get remaining capacity for a section."""
        return self.remaining.get(class_nbr, 0)

    def get_original(self, class_nbr: int) -> int:
        """Get original capacity for a section."""
        return self.original.get(class_nbr, 0)

    def get_section(self, class_nbr: int) -> Optional[Section]:
        """Get section by class number."""
        return self.section_lookup.get(class_nbr)

    def is_available(
        self,
        section: Section,
        allow_overfill: bool = False,
    ) -> bool:
        """
        Check if a section has available capacity.

        Args:
            section: Section to check
            allow_overfill: Whether to allow overfilling

        Returns:
            True if section can accept more students
        """
        # Unlimited capacity courses always available
        if self.config.is_unlimited_capacity(section.course_code):
            return True

        remaining = self.get_remaining(section.class_nbr)

        # Get max overfill limit from config for ALL components
        max_overfill = self.config.get_max_overfill(
            section.course_code,
            section.component.value,
        )

        # If there's remaining capacity, always available
        if remaining > 0:
            return True

        # No remaining capacity - check overfill rules
        if self.config.capacity.allow_negative_capacity:
            # Check if overfill is allowed for this component
            if max_overfill == 0:
                return False  # No overfill allowed

            # Check if we're within overfill limit
            return remaining > -max_overfill

        return False

    def calculate_max_batch(
        self,
        sections: list[Section],
        allow_overfill: bool = False,
    ) -> int:
        """
        Calculate the maximum batch size for a set of sections.

        Args:
            sections: Sections in the timetable
            allow_overfill: Whether TUT overfill is allowed

        Returns:
            Maximum students that can be assigned
        """
        if not sections:
            return 0

        max_batch = float("inf")

        for section in sections:
            # Skip unlimited capacity courses
            if self.config.is_unlimited_capacity(section.course_code):
                continue

            remaining = self.get_remaining(section.class_nbr)

            # Get max overfill from config for ALL components
            max_overfill = self.config.get_max_overfill(
                section.course_code,
                section.component.value,
            )

            # Calculate headroom (remaining + allowed overfill)
            headroom = remaining + max_overfill
            max_batch = min(max_batch, max(0, headroom))

        return int(max_batch) if max_batch != float("inf") else 999999

    def allocate(self, class_nbr: int, count: int) -> None:
        """
        Allocate students to a section.

        Args:
            class_nbr: Section class number
            count: Number of students to allocate
        """
        if class_nbr in self.remaining:
            self.remaining[class_nbr] -= count

    def deallocate(self, class_nbr: int, count: int) -> None:
        """
        Deallocate students from a section (rollback).

        Args:
            class_nbr: Section class number
            count: Number of students to deallocate
        """
        if class_nbr in self.remaining:
            self.remaining[class_nbr] += count

    def get_usage(self, class_nbr: int) -> int:
        """
        Get the number of students allocated to a section.

        Args:
            class_nbr: Section class number

        Returns:
            Number of students assigned
        """
        original = self.get_original(class_nbr)
        remaining = self.get_remaining(class_nbr)
        section = self.get_section(class_nbr)

        # Account for tutorial bonus
        if section and section.component == ComponentType.TUT:
            remaining -= self.config.capacity.tutorial_bonus_seats

        return original - remaining - (section.enrolled if section else 0)

    def get_fill_ratio(self, class_nbr: int) -> float:
        """
        Get the fill ratio for a section.

        Args:
            class_nbr: Section class number

        Returns:
            Fill ratio (usage / capacity), can be > 1.0 if overfilled
        """
        original = self.get_original(class_nbr)
        if original == 0:
            return 0.0
        return self.get_usage(class_nbr) / original

    def snapshot(self) -> dict[int, int]:
        """Create a snapshot of current capacity state."""
        return dict(self.remaining)

    def restore(self, snapshot: dict[int, int]) -> None:
        """Restore capacity state from a snapshot."""
        self.remaining = dict(snapshot)
