"""Base classes for timetable generation algorithms"""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, time
from enum import Enum
from functools import lru_cache
from itertools import product
from typing import Any


class AlgorithmType(str, Enum):
    """Available algorithm types"""

    GREEDY = "greedy"
    BACKTRACK = "backtrack"
    BACKTRACK_OPTIMIZED = "backtrack_optimized"
    GENETIC = "genetic"
    RANDOM = "random"
    RANDOM_RESTART = "random_restart"
    SIMULATED_ANNEALING = "simulated_annealing"
    HYBRID = "hybrid"
    PARALLEL_BEST = "parallel_best"
    PARALLEL_RACE = "parallel_race"


@dataclass
class SectionData:
    """Flattened section data for algorithm processing"""

    id: uuid.UUID
    course_id: uuid.UUID
    class_nbr: int
    section: str
    component: str
    day: str | None
    mtg_start: time | None
    mtg_end: time | None
    exam_date: date | None
    exam_start: time | None
    exam_end: time | None
    instructor: str | None
    room: str | None
    cap_enrl: int
    tot_enrl: int
    # Course info
    subject: str
    catalog: str
    title: str
    max_units: float


@dataclass
class LogicalSection:
    """
    A logical section groups all meeting times for one class.
    E.g., class_nbr 12345 might meet MW 10:00 and TH 15:00 - this is ONE logical section.
    """

    course_id: uuid.UUID
    class_nbr: int
    component: str
    section: str
    subject: str
    catalog: str
    title: str
    max_units: float
    meetings: list[SectionData] = field(default_factory=list)
    # Precomputed bitmasks for each day
    day_masks: dict[str, int] = field(default_factory=dict)
    # Exam info (from first meeting)
    exam_date: date | None = None
    exam_start: time | None = None
    exam_end: time | None = None
    # Seat availability score
    seat_score: int = 0
    instructor: str | None = None
    room: str | None = None

    def __hash__(self):
        return hash((self.course_id, self.class_nbr))

    def __eq__(self, other):
        if not isinstance(other, LogicalSection):
            return False
        return self.course_id == other.course_id and self.class_nbr == other.class_nbr


@dataclass
class SeatPreference:
    """Seat availability preferences for component types"""

    prefer_lab_seats: bool = True
    prefer_tut_seats: bool = True
    prefer_lec_seats: bool = False


@dataclass
class GenerationConstraints:
    """Constraints for timetable generation"""

    max_units: float = 25.0
    # Fixed sections: course_id -> {component -> class_nbr}
    # e.g., {"course-uuid": {"LEC": 12345}} means "must use LEC with class_nbr 12345"
    fixed_sections: dict[uuid.UUID, dict[str, int]] = field(default_factory=dict)
    # Blocked time slots: list of (day, start_time, end_time)
    blocked_slots: list[tuple[str, time, time]] = field(default_factory=list)
    # Seat preferences
    seat_preferences: SeatPreference | None = None


@dataclass
class AlgorithmResult:
    """Result from algorithm execution"""

    success: bool
    partial: bool = False
    # Selected logical sections
    selected_sections: list[LogicalSection] = field(default_factory=list)
    # All section IDs (meeting times) for commit
    section_ids: list[uuid.UUID] = field(default_factory=list)
    # Conflicts/reasons for unscheduled courses
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    # Stats
    total_units: float = 0.0
    course_count: int = 0
    # Algorithm metadata
    algorithm_name: str = ""
    execution_time_ms: float = 0.0
    iterations: int = 0
    message: str = ""


class TimetableAlgorithm(ABC):
    """Base class for timetable generation algorithms"""

    name: str = "base"
    description: str = "Base algorithm"

    def __init__(self, constraints: GenerationConstraints | None = None):
        self.constraints = constraints or GenerationConstraints()
        self._iterations = 0

    @abstractmethod
    def generate(
        self,
        sections_by_course: dict[uuid.UUID, list[SectionData]],
    ) -> AlgorithmResult:
        """
        Generate a timetable from available sections.

        Args:
            sections_by_course: Dict mapping course_id to list of section data

        Returns:
            AlgorithmResult with selected sections or conflicts
        """
        pass

    # ==================== Shared Utilities ====================

    @staticmethod
    @lru_cache(maxsize=8192)
    def time_to_minutes(time_str: str) -> int:
        """Convert HH:MM:SS or HH:MM to minutes since midnight"""
        try:
            parts = time_str.split(":")
            return int(parts[0]) * 60 + int(parts[1])
        except Exception:
            return -1

    @staticmethod
    @lru_cache(maxsize=16384)
    def mask_for_interval(start_minutes: int, end_minutes: int) -> int:
        """Build a 5-min resolution bitmask for [start, end)"""
        if start_minutes < 0 or end_minutes <= start_minutes:
            return 0
        s_idx = start_minutes // 5
        e_idx = end_minutes // 5
        if e_idx <= s_idx:
            return 0
        width = e_idx - s_idx
        return ((1 << width) - 1) << s_idx

    @staticmethod
    @lru_cache(maxsize=8192)
    def time_obj_to_minutes(t: time | None) -> int:
        """Convert time object to minutes since midnight"""
        if t is None:
            return -1
        return t.hour * 60 + t.minute

    def combo_clashes_with_masks(
        self,
        combo: list[LogicalSection],
        schedule_masks: dict[str, int],
        schedule_exams: dict[uuid.UUID, tuple[date, int, int]],
    ) -> bool:
        """Check if an entire combo clashes with schedule - optimized batch check"""
        # Aggregate all masks from the combo first
        for section in combo:
            # Time clash - direct bitmask check
            for day, mask in section.day_masks.items():
                if mask & schedule_masks.get(day, 0):
                    return True

            # Exam clash
            if section.exam_date and section.exam_start and section.exam_end:
                sec_start = self.time_obj_to_minutes(section.exam_start)
                sec_end = self.time_obj_to_minutes(section.exam_end)
                for course_id, (ex_date, ex_start, ex_end) in schedule_exams.items():
                    if course_id == section.course_id:
                        continue
                    if section.exam_date == ex_date:
                        if max(sec_start, ex_start) < min(sec_end, ex_end):
                            return True
        return False

    def merge_combo_to_schedule(
        self,
        combo: list[LogicalSection],
        schedule_masks: dict[str, int],
        schedule_exams: dict[uuid.UUID, tuple[date, int, int]],
    ):
        """Merge entire combo to schedule at once"""
        for section in combo:
            for day, mask in section.day_masks.items():
                schedule_masks[day] = schedule_masks.get(day, 0) | mask

            if (
                section.course_id not in schedule_exams
                and section.exam_date
                and section.exam_start
                and section.exam_end
            ):
                schedule_exams[section.course_id] = (
                    section.exam_date,
                    self.time_obj_to_minutes(section.exam_start),
                    self.time_obj_to_minutes(section.exam_end),
                )

    def build_logical_sections(
        self,
        sections_by_course: dict[uuid.UUID, list[SectionData]],
    ) -> dict[uuid.UUID, dict[str, list[LogicalSection]]]:
        """
        Build logical sections grouped by course and component.

        Returns: {course_id: {component: [LogicalSection, ...]}}
        """
        result: dict[uuid.UUID, dict[str, list[LogicalSection]]] = {}

        for course_id, sections in sections_by_course.items():
            if not sections:
                continue

            # Group by (component, class_nbr)
            logical_map: dict[tuple[str, int], LogicalSection] = {}

            for s in sections:
                key = (s.component, s.class_nbr)

                if key not in logical_map:
                    logical_map[key] = LogicalSection(
                        course_id=course_id,
                        class_nbr=s.class_nbr,
                        component=s.component,
                        section=s.section,
                        subject=s.subject,
                        catalog=s.catalog,
                        title=s.title,
                        max_units=s.max_units,
                        exam_date=s.exam_date,
                        exam_start=s.exam_start,
                        exam_end=s.exam_end,
                        instructor=s.instructor,
                        room=s.room,
                    )

                logical = logical_map[key]
                logical.meetings.append(s)

                # Build day mask
                if s.day and s.mtg_start and s.mtg_end:
                    start_min = self.time_obj_to_minutes(s.mtg_start)
                    end_min = self.time_obj_to_minutes(s.mtg_end)
                    mask = self.mask_for_interval(start_min, end_min)
                    logical.day_masks[s.day] = logical.day_masks.get(s.day, 0) | mask

                # Calculate seat score
                cap = s.cap_enrl or 0
                tot = s.tot_enrl or 0
                logical.seat_score += max(cap - tot, 0)

            # Group by component
            by_component: dict[str, list[LogicalSection]] = {}
            for logical in logical_map.values():
                comp = logical.component
                if comp not in by_component:
                    by_component[comp] = []
                by_component[comp].append(logical)

            # Sort each component by seat score (descending) based on preferences
            for comp in by_component:
                should_prefer_seats = False
                if self.constraints.seat_preferences:
                    prefs = self.constraints.seat_preferences
                    comp_upper = comp.upper()
                    if comp_upper == "LAB" and prefs.prefer_lab_seats:
                        should_prefer_seats = True
                    elif comp_upper == "TUT" and prefs.prefer_tut_seats:
                        should_prefer_seats = True
                    elif comp_upper == "LEC" and prefs.prefer_lec_seats:
                        should_prefer_seats = True
                else:
                    # Default: prefer seats for LAB and TUT
                    comp_upper = comp.upper()
                    should_prefer_seats = comp_upper in ("LAB", "TUT")

                if should_prefer_seats:
                    # Sort by available seats (sections with seats first)
                    by_component[comp].sort(
                        key=lambda x: (x.seat_score <= 0, -x.seat_score),
                    )
                else:
                    # Just sort by seat score descending
                    by_component[comp].sort(key=lambda x: x.seat_score, reverse=True)

            result[course_id] = by_component

        return result

    def check_clash(self, sec_a: LogicalSection, sec_b: LogicalSection) -> bool:
        """Check if two logical sections clash (time or exam)"""
        # Time clash using masks
        for day, mask_a in sec_a.day_masks.items():
            mask_b = sec_b.day_masks.get(day, 0)
            if mask_a & mask_b:
                return True

        # Exam clash - only check between different courses
        # (all sections of the same course share the same exam)
        if sec_a.course_id != sec_b.course_id:
            if sec_a.exam_date and sec_b.exam_date:
                if sec_a.exam_date == sec_b.exam_date:
                    if (
                        sec_a.exam_start
                        and sec_a.exam_end
                        and sec_b.exam_start
                        and sec_b.exam_end
                    ):
                        a_start = self.time_obj_to_minutes(sec_a.exam_start)
                        a_end = self.time_obj_to_minutes(sec_a.exam_end)
                        b_start = self.time_obj_to_minutes(sec_b.exam_start)
                        b_end = self.time_obj_to_minutes(sec_b.exam_end)
                        if max(a_start, b_start) < min(a_end, b_end):
                            return True

        return False

    def check_clash_with_schedule(
        self,
        section: LogicalSection,
        schedule_masks: dict[str, int],
        schedule_exams: dict[uuid.UUID, tuple[date, int, int]],
    ) -> bool:
        """Check if section clashes with existing schedule

        Args:
            section: The section to check
            schedule_masks: Day -> bitmask for scheduled times
            schedule_exams: course_id -> (exam_date, exam_start_mins, exam_end_mins)
        """
        # Time clash
        for day, mask in section.day_masks.items():
            if mask & schedule_masks.get(day, 0):
                return True

        # Exam clash - only check against exams from different courses
        if section.exam_date and section.exam_start and section.exam_end:
            sec_start = self.time_obj_to_minutes(section.exam_start)
            sec_end = self.time_obj_to_minutes(section.exam_end)
            for course_id, (ex_date, ex_start, ex_end) in schedule_exams.items():
                # Skip if same course (all sections of a course share the same exam)
                if course_id == section.course_id:
                    continue
                if section.exam_date == ex_date:
                    if max(sec_start, ex_start) < min(sec_end, ex_end):
                        return True

        return False

    def merge_section_to_schedule(
        self,
        section: LogicalSection,
        schedule_masks: dict[str, int],
        schedule_exams: dict[uuid.UUID, tuple[date, int, int]],
    ):
        """Add section's time slots to schedule (mutates in place)

        Args:
            section: The section to add
            schedule_masks: Day -> bitmask for scheduled times
            schedule_exams: course_id -> (exam_date, exam_start_mins, exam_end_mins)
        """
        for day, mask in section.day_masks.items():
            schedule_masks[day] = schedule_masks.get(day, 0) | mask

        # Only add exam once per course
        if (
            section.course_id not in schedule_exams
            and section.exam_date
            and section.exam_start
            and section.exam_end
        ):
            schedule_exams[section.course_id] = (
                section.exam_date,
                self.time_obj_to_minutes(section.exam_start),
                self.time_obj_to_minutes(section.exam_end),
            )

    def get_section_ids(self, sections: list[LogicalSection]) -> list[uuid.UUID]:
        """Extract all meeting IDs from logical sections"""
        ids = []
        seen = set()
        for sec in sections:
            for meeting in sec.meetings:
                if meeting.id not in seen:
                    ids.append(meeting.id)
                    seen.add(meeting.id)
        return ids

    def apply_fixed_constraints(
        self,
        course_id: uuid.UUID,
        component: str,
        sections: list[LogicalSection],
    ) -> list[LogicalSection]:
        """Filter sections based on fixed constraints"""
        fixed = self.constraints.fixed_sections.get(course_id, {})
        if component in fixed:
            required_class_nbr = fixed[component]
            return [s for s in sections if s.class_nbr == required_class_nbr]
        return sections

    def check_blocked_slots(self, section: LogicalSection) -> bool:
        """Check if section conflicts with blocked time slots"""
        for day, block_start, block_end in self.constraints.blocked_slots:
            if day in section.day_masks:
                block_start_min = self.time_obj_to_minutes(block_start)
                block_end_min = self.time_obj_to_minutes(block_end)
                block_mask = self.mask_for_interval(block_start_min, block_end_min)
                if section.day_masks[day] & block_mask:
                    return True
        return False

    # ==================== Combo Enumeration (Shared) ====================

    def get_component_priority(self, component: str) -> int:
        """Get priority for component type (lower = higher priority, schedule first)"""
        c_upper = component.upper()
        if c_upper == "LAB":
            return 0
        elif c_upper == "TUT":
            return 1
        elif c_upper == "LEC":
            return 2
        return 3

    def enumerate_course_combos(
        self,
        course_id: uuid.UUID,
        by_component: dict[str, list[LogicalSection]],
        sort_by_seats: bool = True,
    ) -> list[list[LogicalSection]]:
        """
        Enumerate all valid combinations (one section per component) for a course.

        Uses itertools.product for efficient combo generation with conflict filtering.

        Args:
            course_id: The course ID
            by_component: Dict mapping component type to list of LogicalSection
            sort_by_seats: If True, sort combos by seat score (descending)

        Returns:
            List of valid combos, each combo is a list of LogicalSection (one per component)
        """
        if not by_component:
            return []

        # Sort components by priority (LAB > TUT > LEC)
        components = sorted(by_component.keys(), key=self.get_component_priority)

        # Build filtered section lists for each component
        component_sections: list[list[LogicalSection]] = []

        for component in components:
            sections = by_component.get(component, [])

            # Apply fixed constraints
            sections = self.apply_fixed_constraints(course_id, component, sections)

            # Filter blocked slots
            valid_sections = [s for s in sections if not self.check_blocked_slots(s)]

            # Filter sections with no available seats for LAB and TUT components
            # to prevent tot_enrl from exceeding cap_enrl
            comp_upper = component.upper()
            if comp_upper in ("LAB"):
                valid_sections = [s for s in valid_sections if s.seat_score > 0]

            if not valid_sections:
                # No valid sections for this component - no valid combos possible
                return []

            component_sections.append(valid_sections)

        # Use itertools.product to generate all combinations
        # Then filter for internal clashes
        valid_combos: list[list[LogicalSection]] = []

        for combo_tuple in product(*component_sections):
            combo = list(combo_tuple)

            # Check for internal clashes within the combo
            has_clash = False
            for i in range(len(combo)):
                for j in range(i + 1, len(combo)):
                    if self.check_clash(combo[i], combo[j]):
                        has_clash = True
                        break
                if has_clash:
                    break

            if not has_clash:
                valid_combos.append(combo)

        # Sort by seat score if requested
        if sort_by_seats and valid_combos:
            valid_combos.sort(key=lambda c: sum(s.seat_score for s in c), reverse=True)

        return valid_combos

    def enumerate_all_course_combos(
        self,
        logical_by_course: dict[uuid.UUID, dict[str, list[LogicalSection]]],
        sort_by_seats: bool = True,
    ) -> dict[uuid.UUID, tuple[list[list[LogicalSection]], dict[str, Any]]]:
        """
        Enumerate combos for all courses at once.

        Returns dict mapping course_id to (combos, metadata).
        Metadata includes subject, catalog, title, units.
        """
        result: dict[uuid.UUID, tuple[list[list[LogicalSection]], dict[str, Any]]] = {}

        for course_id, by_component in logical_by_course.items():
            combos = self.enumerate_course_combos(
                course_id, by_component, sort_by_seats
            )

            if combos:
                first = combos[0][0] if combos[0] else None
                meta = {
                    "subject": first.subject if first else "",
                    "catalog": first.catalog if first else "",
                    "title": first.title if first else "",
                    "units": first.max_units if first else 0,
                }
                result[course_id] = (combos, meta)

        return result
