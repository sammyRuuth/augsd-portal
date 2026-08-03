#!/usr/bin/env python3
"""
Optimized Bulk Timetable Generator (Global Optimization)

Unlike the per-student approach in bulk_timetable_db.py, this script uses
global optimization to maximize overall success rate:

1. Pre-computes all valid combos once per package (not per student)
2. Analyzes global demand vs capacity to identify bottlenecks
3. Orders students by constraint level (most constrained first - MCV heuristic)
4. Uses capacity-aware scoring to prefer sections with room for remaining students
5. Optional redistribution phase to fix partial timetables via swaps

Usage:
    uv run python scripts/bulk_timetable_optimized.py --excel data/students.xlsx --year 2025

    # With options:
    uv run python scripts/bulk_timetable_optimized.py \\
        --excel data/students.xlsx \\
        --year 2025 \\
        --session <session-id> \\
        --strategy greedy \\
        --redistribution \\
        --dry-run
"""

import argparse
import asyncio
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.algorithms.base import (
    AlgorithmResult,
    GenerationConstraints,
    LogicalSection,
    SectionData,
    TimetableAlgorithm,
)

# Reuse database functions from original script
from scripts.bulk_timetable_db import (
    StudentInfo,
    commit_timetable,
    get_all_packages_for_year,
    get_course_id_map,
    get_or_create_student,
    get_package_key_for_student,
    get_sections_for_courses,
    get_session_by_id,
    interactive_session_select,
    list_sessions,
    parse_student_excel,
)

# ==================== Data Classes ====================


@dataclass
class GlobalOptimizationConfig:
    """Configuration for global optimization"""

    strategy: Literal["greedy", "backtrack"] = "backtrack"
    enable_redistribution: bool = True
    max_redistribution_rounds: int = 3
    prefer_load_balancing: bool = True
    bottleneck_threshold: float = 0.8
    verbose: bool = True


@dataclass
class SectionDemand:
    """Tracks demand for a specific section"""

    class_nbr: int
    course_id: uuid.UUID
    component: str
    capacity: int
    initial_enrollment: int
    current_enrollment: int = 0
    potential_demand: int = 0  # Students who could use this section

    @property
    def available_seats(self) -> int:
        return max(0, self.capacity - self.current_enrollment)

    @property
    def scarcity_score(self) -> float:
        """Higher score = more scarce (demand > available)"""
        available = self.available_seats
        if available <= 0:
            return float("inf")
        return self.potential_demand / available


@dataclass
class PackageData:
    """Pre-computed data for a package"""

    package_key: str
    course_ids: list[uuid.UUID]
    course_codes: list[str]
    # Logical sections by course
    logical_by_course: dict[uuid.UUID, dict[str, list[LogicalSection]]] = field(
        default_factory=dict
    )
    # Pre-computed combos per course: course_id -> (combos, metadata)
    combos_by_course: dict[uuid.UUID, tuple[list[list[LogicalSection]], dict]] = field(
        default_factory=dict
    )
    # Students assigned to this package
    students: list[StudentInfo] = field(default_factory=list)


@dataclass
class StudentAllocation:
    """Tracks allocation state for a student"""

    student_info: StudentInfo
    student_id: uuid.UUID | None = None
    package_key: str = ""
    # Allocated sections
    selected_sections: list[LogicalSection] = field(default_factory=list)
    # Schedule state
    schedule_masks: dict[str, int] = field(default_factory=dict)
    schedule_exams: dict[uuid.UUID, tuple[date, int, int]] = field(default_factory=dict)
    # Status
    success: bool = False
    partial: bool = False
    scheduled_course_ids: set[uuid.UUID] = field(default_factory=set)
    failed_course_ids: set[uuid.UUID] = field(default_factory=set)
    # Flexibility score (lower = more constrained)
    flexibility_score: int = 0


@dataclass
class GlobalAllocationState:
    """Global state tracking capacity across all packages"""

    # Section enrollment: class_nbr -> current enrollment count
    section_enrollment: dict[int, int] = field(default_factory=dict)
    # Initial enrollment from DB (for tracking what we added)
    initial_enrollment: dict[int, int] = field(default_factory=dict)
    # Section demand info: class_nbr -> SectionDemand
    section_demands: dict[int, SectionDemand] = field(default_factory=dict)
    # Bottleneck sections: class_nbrs with high scarcity
    bottleneck_sections: set[int] = field(default_factory=set)
    # Student allocations: campus_id -> StudentAllocation
    allocations: dict[str, StudentAllocation] = field(default_factory=dict)
    # Section to students mapping: class_nbr -> set of campus_ids using it
    section_to_students: dict[int, set[str]] = field(default_factory=dict)


# ==================== Algorithm Utilities ====================


@dataclass
class CourseState:
    """Tracks valid combos for a course during search - for dynamic MRV"""

    course_id: uuid.UUID
    all_combos: list[list[LogicalSection]]
    valid_combo_indices: list[int]  # Indices into all_combos that are currently valid
    meta: dict

    @property
    def domain_size(self) -> int:
        return len(self.valid_combo_indices)


class GlobalOptimizer(TimetableAlgorithm):
    """
    Optimized global capacity-aware bulk optimizer.

    Key optimizations over basic approach:
    1. Capacity caching - Avoid repeated capacity lookups
    2. Forward checking - Pre-filter combos and detect failures early
    3. Dynamic MRV - Re-order courses during search based on remaining valid options
    4. Incremental state updates - Efficient undo without full dict copies
    5. Lookahead scoring - Consider impact on future course options
    6. Early termination - Stop when perfect solution found
    """

    name = "global_optimizer"
    description = "Global capacity-aware bulk optimizer"

    def __init__(
        self,
        config: GlobalOptimizationConfig,
        constraints: GenerationConstraints | None = None,
    ):
        super().__init__(constraints)
        self.config = config
        # Cache for section capacities (class_nbr -> capacity)
        self._capacity_cache: dict[int, int] = {}

    def generate(
        self,
        sections_by_course: dict[uuid.UUID, list[SectionData]],
    ) -> AlgorithmResult:
        """Not used directly - this class uses allocate_student instead"""
        raise NotImplementedError("Use allocate_student for global optimization")

    def _get_capacity(self, section: LogicalSection) -> int:
        """Get capacity with caching"""
        if section.class_nbr not in self._capacity_cache:
            cap = section.meetings[0].cap_enrl if section.meetings else 0
            self._capacity_cache[section.class_nbr] = cap
        return self._capacity_cache[section.class_nbr]

    def get_available_seats(
        self,
        section: LogicalSection,
        global_state: GlobalAllocationState,
        reserved: dict[int, int] | None = None,
    ) -> int:
        """Get available seats considering global state and reservations"""
        enrollment = global_state.section_enrollment.get(section.class_nbr, 0)
        if reserved:
            enrollment += reserved.get(section.class_nbr, 0)
        capacity = self._get_capacity(section)
        return max(0, capacity - enrollment)

    def is_section_available(
        self,
        section: LogicalSection,
        global_state: GlobalAllocationState,
        reserved: dict[int, int] | None = None,
    ) -> bool:
        """Check if section has available seats"""
        return self.get_available_seats(section, global_state, reserved) > 0

    def is_combo_available(
        self,
        combo: list[LogicalSection],
        global_state: GlobalAllocationState,
        reserved: dict[int, int] | None = None,
    ) -> bool:
        """Check if all sections in combo have capacity"""
        return all(
            self.is_section_available(sec, global_state, reserved) for sec in combo
        )

    def filter_valid_combos(
        self,
        combos: list[list[LogicalSection]],
        masks: dict[str, int],
        exams: dict[uuid.UUID, tuple[date, int, int]],
        global_state: GlobalAllocationState,
        reserved: dict[int, int] | None = None,
    ) -> list[int]:
        """Return indices of combos that are valid (no clash, has capacity)"""
        valid_indices = []
        for i, combo in enumerate(combos):
            if self.combo_clashes_with_masks(combo, masks, exams):
                continue
            if not self.is_combo_available(combo, global_state, reserved):
                continue
            valid_indices.append(i)
        return valid_indices

    def score_combo(
        self,
        combo: list[LogicalSection],
        global_state: GlobalAllocationState,
        remaining_students: int,
        reserved: dict[int, int] | None = None,
    ) -> float:
        """
        Score a combo considering global capacity impact.

        Higher score = better choice. Returns -inf if combo is invalid.
        """
        score = 0.0

        for section in combo:
            available = self.get_available_seats(section, global_state, reserved)

            if available <= 0:
                return float("-inf")

            capacity = self._get_capacity(section)

            # Normalized availability score (prefer sections with room)
            if capacity > 0:
                availability_ratio = available / capacity
                score += availability_ratio * 50
            else:
                score += available * 10

            # Load balancing: exponential penalty for high utilization
            if self.config.prefer_load_balancing and capacity > 0:
                utilization = 1.0 - (available / capacity)
                score -= (utilization**2) * 30

            # Bottleneck penalty
            if section.class_nbr in global_state.bottleneck_sections:
                demand = global_state.section_demands.get(section.class_nbr)
                if demand and demand.potential_demand > 0:
                    scarcity = min(demand.scarcity_score, 5.0)
                    remaining_factor = min(remaining_students / 100, 1.0)
                    score -= scarcity * remaining_factor * 40

        return score

    def allocate_student_greedy(
        self,
        student: StudentAllocation,
        package: PackageData,
        global_state: GlobalAllocationState,
        remaining_students: int,
    ) -> bool:
        """
        Optimized greedy allocation with dynamic MRV and lookahead.

        1. Pre-filter combos by capacity
        2. Dynamic MRV ordering (re-sort after each assignment)
        3. Lookahead scoring for combo selection
        """
        # Build course states with pre-filtered combos
        course_states: list[CourseState] = []
        for course_id, (combos, meta) in package.combos_by_course.items():
            valid_indices = self.filter_valid_combos(
                combos,
                student.schedule_masks,
                student.schedule_exams,
                global_state,
            )
            course_states.append(
                CourseState(
                    course_id=course_id,
                    all_combos=combos,
                    valid_combo_indices=valid_indices,
                    meta=meta,
                )
            )

        while course_states:
            # Re-filter combos based on current schedule and global state
            # Note: global_state is updated after each course, so no need for separate reserved tracking
            for state in course_states:
                state.valid_combo_indices = self.filter_valid_combos(
                    state.all_combos,
                    student.schedule_masks,
                    student.schedule_exams,
                    global_state,
                )

            # Sort by domain size (MRV - most constrained first)
            course_states.sort(key=lambda s: s.domain_size)

            # Take the most constrained course
            state = course_states.pop(0)

            if state.domain_size == 0:
                # No valid combos for this course
                student.failed_course_ids.add(state.course_id)
                continue

            # Score valid combos with lookahead
            best_combo = None
            best_score = float("-inf")

            for idx in state.valid_combo_indices:
                combo = state.all_combos[idx]
                score = self.score_combo(combo, global_state, remaining_students)

                # Add lookahead bonus: prefer combos that leave more options
                if course_states and score > float("-inf"):
                    lookahead_bonus = self._compute_lookahead(
                        combo,
                        course_states,
                        student.schedule_masks,
                        student.schedule_exams,
                        global_state,
                    )
                    score += lookahead_bonus

                if score > best_score:
                    best_score = score
                    best_combo = combo

            if best_combo and best_score > float("-inf"):
                self._commit_combo(student, best_combo, global_state)
                student.scheduled_course_ids.add(state.course_id)
            else:
                student.failed_course_ids.add(state.course_id)

        # Set success status
        total_courses = len(package.combos_by_course)
        scheduled = len(student.scheduled_course_ids)
        student.success = scheduled == total_courses
        student.partial = 0 < scheduled < total_courses

        return student.success

    def _compute_lookahead(
        self,
        combo: list[LogicalSection],
        remaining_states: list[CourseState],
        masks: dict[str, int],
        exams: dict[uuid.UUID, tuple[date, int, int]],
        global_state: GlobalAllocationState,
        pending_sections: dict[int, int] | None = None,
    ) -> float:
        """Compute lookahead bonus for a combo based on remaining options"""
        # Simulate adding this combo
        temp_masks = dict(masks)
        temp_exams = dict(exams)
        self.merge_combo_to_schedule(combo, temp_masks, temp_exams)

        # Track sections that would be used by this combo (not yet in global_state)
        temp_pending = dict(pending_sections) if pending_sections else {}
        for sec in combo:
            temp_pending[sec.class_nbr] = temp_pending.get(sec.class_nbr, 0) + 1

        # Count remaining valid combos for other courses
        import math

        bonus = 0.0
        for state in remaining_states:
            valid_count = 0
            for idx in state.valid_combo_indices:
                c = state.all_combos[idx]
                if not self.combo_clashes_with_masks(c, temp_masks, temp_exams):
                    if self.is_combo_available(c, global_state, temp_pending):
                        valid_count += 1
            if valid_count > 0:
                bonus += math.log2(valid_count + 1) * 3
            else:
                # Penalty for eliminating all options
                bonus -= 50

        return bonus

    def allocate_student_backtrack(
        self,
        student: StudentAllocation,
        package: PackageData,
        global_state: GlobalAllocationState,
        remaining_students: int,
        max_iterations: int = 100000,
    ) -> bool:
        """
        Optimized backtracking with:
        1. Forward checking with domain wipeout detection
        2. Dynamic MRV variable ordering
        3. Incremental state management (no full dict copies)
        4. Score-based combo ordering
        5. Early termination on perfect solution
        """
        # Pre-filter combos by capacity once
        course_data: dict[uuid.UUID, tuple[list[list[LogicalSection]], dict]] = {}
        for course_id, (combos, meta) in package.combos_by_course.items():
            valid_combos = [
                c for c in combos if self.is_combo_available(c, global_state)
            ]
            if valid_combos:
                course_data[course_id] = (valid_combos, meta)

        total_courses = len(course_data)
        if total_courses == 0:
            return False

        best_solution: list[tuple[uuid.UUID, list[LogicalSection]]] = []
        best_count = 0
        iterations = [0]

        def get_valid_combos(
            course_id: uuid.UUID,
            masks: dict[str, int],
            exams: dict[uuid.UUID, tuple[date, int, int]],
            reserved: dict[int, int],
        ) -> list[tuple[float, list[LogicalSection]]]:
            """Get valid combos for a course, scored and sorted"""
            combos, _ = course_data[course_id]
            scored = []
            for combo in combos:
                if self.combo_clashes_with_masks(combo, masks, exams):
                    continue
                if not self.is_combo_available(combo, global_state, reserved):
                    continue
                score = self.score_combo(
                    combo, global_state, remaining_students, reserved
                )
                if score > float("-inf"):
                    scored.append((score, combo))
            # Sort by score descending
            scored.sort(key=lambda x: -x[0])
            return scored

        def select_mrv(
            unassigned: set[uuid.UUID],
            masks: dict[str, int],
            exams: dict[uuid.UUID, tuple[date, int, int]],
            reserved: dict[int, int],
        ) -> tuple[uuid.UUID | None, list[tuple[float, list[LogicalSection]]]]:
            """Select course with fewest valid combos (MRV), return combos"""
            best_course = None
            best_combos: list[tuple[float, list[LogicalSection]]] = []
            best_size = float("inf")

            for course_id in unassigned:
                valid = get_valid_combos(course_id, masks, exams, reserved)
                size = len(valid)
                if size == 0:
                    # Domain wipeout - can't schedule this course
                    continue
                if size < best_size:
                    best_size = size
                    best_course = course_id
                    best_combos = valid

            return best_course, best_combos

        def backtrack(
            unassigned: set[uuid.UUID],
            solution: list[tuple[uuid.UUID, list[LogicalSection]]],
            masks: dict[str, int],
            exams: dict[uuid.UUID, tuple[date, int, int]],
            reserved: dict[int, int],
        ) -> bool:
            nonlocal best_solution, best_count

            iterations[0] += 1
            if iterations[0] > max_iterations:
                return best_count == total_courses

            # Pruning
            if len(solution) + len(unassigned) <= best_count:
                return False

            # Check if done
            if not unassigned:
                if len(solution) > best_count:
                    best_count = len(solution)
                    best_solution = list(solution)
                return best_count == total_courses

            # Select next course using MRV
            next_course, valid_combos = select_mrv(unassigned, masks, exams, reserved)

            if next_course is None:
                # Some courses have no valid combos - skip them
                remaining = set(unassigned)
                for cid in unassigned:
                    if not get_valid_combos(cid, masks, exams, reserved):
                        remaining.discard(cid)
                if remaining == unassigned:
                    # No progress possible
                    if len(solution) > best_count:
                        best_count = len(solution)
                        best_solution = list(solution)
                    return False
                return backtrack(remaining, solution, masks, exams, reserved)

            new_unassigned = unassigned - {next_course}

            # Try each valid combo (already sorted by score)
            for score, combo in valid_combos:
                # Incremental state update - track what we add
                added_masks: list[tuple[str, int]] = []
                added_exams: list[uuid.UUID] = []
                added_reserved: list[tuple[int, int]] = []

                # Apply combo to masks
                for section in combo:
                    for day, mask in section.day_masks.items():
                        old_val = masks.get(day, 0)
                        new_val = old_val | mask
                        if new_val != old_val:
                            added_masks.append((day, old_val))
                            masks[day] = new_val

                    # Apply exam
                    if (
                        section.course_id not in exams
                        and section.exam_date
                        and section.exam_start
                        and section.exam_end
                    ):
                        added_exams.append(section.course_id)
                        exams[section.course_id] = (
                            section.exam_date,
                            self.time_obj_to_minutes(section.exam_start),
                            self.time_obj_to_minutes(section.exam_end),
                        )

                    # Reserve section
                    old_res = reserved.get(section.class_nbr, 0)
                    reserved[section.class_nbr] = old_res + 1
                    added_reserved.append((section.class_nbr, old_res))

                solution.append((next_course, combo))

                if backtrack(new_unassigned, solution, masks, exams, reserved):
                    return True

                # Restore state incrementally
                solution.pop()

                for day, old_val in added_masks:
                    if old_val == 0:
                        del masks[day]
                    else:
                        masks[day] = old_val

                for cid in added_exams:
                    del exams[cid]

                for class_nbr, old_val in added_reserved:
                    if old_val == 0:
                        del reserved[class_nbr]
                    else:
                        reserved[class_nbr] = old_val

            # Try skipping this course if we haven't found all yet
            if best_count < total_courses:
                return backtrack(new_unassigned, solution, masks, exams, reserved)

            return best_count == total_courses

        # Run search
        backtrack(
            set(course_data.keys()),
            [],
            dict(student.schedule_masks),
            dict(student.schedule_exams),
            {},
        )

        # Apply best solution
        for course_id, combo in best_solution:
            self._commit_combo(student, combo, global_state)
            student.scheduled_course_ids.add(course_id)

        # Mark failed courses
        for course_id in course_data:
            if course_id not in student.scheduled_course_ids:
                student.failed_course_ids.add(course_id)

        scheduled = len(student.scheduled_course_ids)
        student.success = scheduled == total_courses
        student.partial = 0 < scheduled < total_courses

        return student.success

    def _commit_combo(
        self,
        student: StudentAllocation,
        combo: list[LogicalSection],
        global_state: GlobalAllocationState,
        debug: bool = False,
    ):
        """Commit a combo to student's schedule and update global state"""
        student.selected_sections.extend(combo)
        self.merge_combo_to_schedule(
            combo, student.schedule_masks, student.schedule_exams
        )

        for section in combo:
            class_nbr = section.class_nbr
            current = global_state.section_enrollment.get(class_nbr, 0)
            capacity = self._get_capacity(section)

            if debug and current >= capacity:
                print(
                    f"  WARNING: Committing to full section {class_nbr} "
                    f"({current}/{capacity}) for {student.student_info.campus_id}"
                )

            global_state.section_enrollment[class_nbr] = current + 1

            if class_nbr not in global_state.section_to_students:
                global_state.section_to_students[class_nbr] = set()
            global_state.section_to_students[class_nbr].add(
                student.student_info.campus_id
            )


# ==================== Redistribution ====================


class Redistributor:
    """Handles redistribution phase to fix partial timetables"""

    def __init__(self, optimizer: GlobalOptimizer, config: GlobalOptimizationConfig):
        self.optimizer = optimizer
        self.config = config

    def redistribute(
        self,
        partial_students: list[StudentAllocation],
        packages: dict[str, PackageData],
        global_state: GlobalAllocationState,
    ) -> int:
        """
        Try to fix partial timetables by finding beneficial swaps.

        Returns number of students fixed.
        """
        fixed_count = 0

        for student in partial_students:
            if student.success:
                continue

            package = packages.get(student.package_key)
            if not package:
                continue

            # Try to schedule each missing course
            for course_id in list(student.failed_course_ids):
                if course_id not in package.combos_by_course:
                    continue

                combos, meta = package.combos_by_course[course_id]

                # Try each combo
                for combo in combos:
                    # Check schedule clash first
                    if self.optimizer.combo_clashes_with_masks(
                        combo, student.schedule_masks, student.schedule_exams
                    ):
                        continue

                    # Check if combo is directly available
                    all_available = all(
                        self.optimizer.is_section_available(sec, global_state)
                        for sec in combo
                    )

                    if all_available:
                        # Great! Directly assign
                        self.optimizer._commit_combo(student, combo, global_state)
                        student.scheduled_course_ids.add(course_id)
                        student.failed_course_ids.remove(course_id)
                        break

                    # Try to find a swap
                    swap_found = self._try_swap(
                        student, combo, course_id, packages, global_state
                    )
                    if swap_found:
                        student.scheduled_course_ids.add(course_id)
                        student.failed_course_ids.remove(course_id)
                        break

            # Update status
            len(student.scheduled_course_ids) + len(student.failed_course_ids)
            if len(student.failed_course_ids) == 0:
                student.success = True
                student.partial = False
                fixed_count += 1
            elif len(student.scheduled_course_ids) > 0:
                student.partial = True

        return fixed_count

    def _try_swap(
        self,
        student: StudentAllocation,
        needed_combo: list[LogicalSection],
        course_id: uuid.UUID,
        packages: dict[str, PackageData],
        global_state: GlobalAllocationState,
    ) -> bool:
        """Try to find a swap that frees up needed sections"""
        # Find which sections are blocking
        blocking_sections = []
        for section in needed_combo:
            if not self.optimizer.is_section_available(section, global_state):
                blocking_sections.append(section)

        if not blocking_sections:
            return False

        # For each blocking section, find who's using it
        for section in blocking_sections:
            users = global_state.section_to_students.get(section.class_nbr, set())

            for other_campus_id in users:
                other = global_state.allocations.get(other_campus_id)
                if not other:
                    continue

                # Check if other student can use an alternative combo
                if self._can_other_swap(other, section, packages, global_state):
                    # Execute swap
                    self._execute_swap(
                        student,
                        other,
                        section,
                        needed_combo,
                        course_id,
                        packages,
                        global_state,
                    )
                    return True

        return False

    def _can_other_swap(
        self,
        other: StudentAllocation,
        section: LogicalSection,
        packages: dict[str, PackageData],
        global_state: GlobalAllocationState,
    ) -> bool:
        """Check if other student can switch to a different combo for this course"""
        package = packages.get(other.package_key)
        if not package:
            return False

        course_id = section.course_id
        if course_id not in package.combos_by_course:
            return False

        combos, _ = package.combos_by_course[course_id]

        # Find alternative combos that don't use this section
        for combo in combos:
            # Skip if this combo uses the section we need
            if any(s.class_nbr == section.class_nbr for s in combo):
                continue

            # Build temporary schedule without current course
            temp_masks = {}
            temp_exams = {}

            for sec in other.selected_sections:
                if sec.course_id != course_id:
                    self.optimizer.merge_section_to_schedule(
                        sec, temp_masks, temp_exams
                    )

            # Check if alternative fits
            if self.optimizer.combo_clashes_with_masks(combo, temp_masks, temp_exams):
                continue

            # Check capacity of alternative
            all_available = all(
                self.optimizer.get_available_seats(s, global_state) > 0
                or s.class_nbr == section.class_nbr  # Will be freed by swap
                for s in combo
            )
            if all_available:
                return True

        return False

    def _execute_swap(
        self,
        student: StudentAllocation,
        other: StudentAllocation,
        section: LogicalSection,
        needed_combo: list[LogicalSection],
        course_id: uuid.UUID,
        packages: dict[str, PackageData],
        global_state: GlobalAllocationState,
    ):
        """Execute a swap between two students"""
        # Find the combo other student is currently using for this course
        other_current_combo = [
            s for s in other.selected_sections if s.course_id == section.course_id
        ]

        # Find alternative combo for other student
        package = packages.get(other.package_key)
        combos, _ = package.combos_by_course[section.course_id]

        alternative_combo = None
        for combo in combos:
            if any(s.class_nbr == section.class_nbr for s in combo):
                continue

            temp_masks = {}
            temp_exams = {}
            for sec in other.selected_sections:
                if sec.course_id != section.course_id:
                    self.optimizer.merge_section_to_schedule(
                        sec, temp_masks, temp_exams
                    )

            if not self.optimizer.combo_clashes_with_masks(
                combo, temp_masks, temp_exams
            ):
                alternative_combo = combo
                break

        if not alternative_combo:
            return

        # Remove other's current combo from global state
        for sec in other_current_combo:
            global_state.section_enrollment[sec.class_nbr] -= 1
            global_state.section_to_students[sec.class_nbr].discard(
                other.student_info.campus_id
            )

        # Remove from other's sections
        other.selected_sections = [
            s for s in other.selected_sections if s.course_id != section.course_id
        ]

        # Rebuild other's schedule masks
        other.schedule_masks.clear()
        other.schedule_exams.clear()
        for sec in other.selected_sections:
            self.optimizer.merge_section_to_schedule(
                sec, other.schedule_masks, other.schedule_exams
            )

        # Add alternative combo to other
        self.optimizer._commit_combo(other, alternative_combo, global_state)

        # Add needed combo to student
        self.optimizer._commit_combo(student, needed_combo, global_state)


# ==================== Main Orchestration ====================


async def run_global_optimization(
    session_info: dict,
    excel_path: str,
    year: int,
    config: GlobalOptimizationConfig,
    dry_run: bool = False,
    ignore_existing_enrollment: bool = False,
):
    """Run globally optimized bulk timetable generation"""
    schema_name = session_info["schema_name"]

    print("\n" + "=" * 80)
    print(" " * 20 + "GLOBAL OPTIMIZED BULK TIMETABLE GENERATION")
    print("=" * 80)
    print(f"Session: {session_info['name']}")
    print(f"Schema: {schema_name}")
    print(f"Excel: {excel_path}")
    print(f"Year: {year}")
    print(f"Strategy: {config.strategy}")
    print(f"Redistribution: {config.enable_redistribution}")
    print(f"Dry Run: {dry_run}")
    if ignore_existing_enrollment:
        print("Ignore Existing Enrollment: True (starting from 0)")

    # ==================== Phase 1: Load Data ====================
    print("\n" + "-" * 80)
    print("PHASE 1: LOADING DATA")
    print("-" * 80)

    # Parse students
    print("\nParsing Excel file...")
    students = parse_student_excel(excel_path)
    print(f"  Found {len(students)} students")

    if not students:
        print("No students found in Excel file!")
        return

    # Get packages
    print("\nFetching packages...")
    db_packages = await get_all_packages_for_year(year)
    print(f"  Found {len(db_packages)} packages")

    available_packages = set(db_packages.keys())

    # Group students by package
    students_by_package: dict[str, list[StudentInfo]] = defaultdict(list)
    no_package_students = []

    for student in students:
        package_key = get_package_key_for_student(student, available_packages)
        if package_key:
            students_by_package[package_key].append(student)
        else:
            no_package_students.append(student)

    print("\nStudents by package:")
    for pkg_key in sorted(students_by_package.keys()):
        print(f"  {pkg_key}: {len(students_by_package[pkg_key])} students")
    if no_package_students:
        print(f"  (no package): {len(no_package_students)} students")

    # Get all course codes across all used packages
    all_course_codes: set[str] = set()
    for pkg_key in students_by_package.keys():
        pkg_info = db_packages.get(pkg_key)
        if pkg_info:
            all_course_codes.update(pkg_info.course_codes)

    # Get course ID mapping
    print("\nMapping courses...")
    course_id_map = await get_course_id_map(list(all_course_codes))
    print(f"  Mapped {len(course_id_map)}/{len(all_course_codes)} courses")

    # Get all sections
    print("\nFetching sections...")
    all_course_ids = list(course_id_map.values())
    all_sections_by_course = await get_sections_for_courses(schema_name, all_course_ids)
    print(f"  Found sections for {len(all_sections_by_course)} courses")

    # ==================== Phase 2: Pre-computation ====================
    print("\n" + "-" * 80)
    print("PHASE 2: PRE-COMPUTING COMBOS BY PACKAGE")
    print("-" * 80)

    optimizer = GlobalOptimizer(config)
    packages: dict[str, PackageData] = {}
    global_state = GlobalAllocationState()

    # Initialize section enrollment from database values
    already_full = 0
    already_over = 0
    total_sections = 0
    for course_sections in all_sections_by_course.values():
        for sec in course_sections:
            if sec.class_nbr not in global_state.section_enrollment:
                total_sections += 1
                # Use 0 as initial enrollment if ignoring existing
                initial_enrl = 0 if ignore_existing_enrollment else sec.tot_enrl
                global_state.section_enrollment[sec.class_nbr] = initial_enrl
                global_state.initial_enrollment[sec.class_nbr] = initial_enrl
                global_state.section_demands[sec.class_nbr] = SectionDemand(
                    class_nbr=sec.class_nbr,
                    course_id=sec.course_id,
                    component=sec.component,
                    capacity=sec.cap_enrl,
                    initial_enrollment=initial_enrl,
                    current_enrollment=initial_enrl,
                )
                if initial_enrl >= sec.cap_enrl:
                    already_full += 1
                if initial_enrl > sec.cap_enrl:
                    already_over += 1

    print(f"  Total unique sections: {total_sections}")
    print(f"  Sections with available seats: {total_sections - already_full}")
    if already_full > 0 and not ignore_existing_enrollment:
        print(f"  WARNING: {already_full} sections already at/over capacity in DB")
        print("           Use --ignore-existing to start from 0 enrollment")
    if already_over > 0 and not ignore_existing_enrollment:
        print(f"  WARNING: {already_over} sections already OVER capacity in DB")

    for pkg_key, pkg_students in students_by_package.items():
        pkg_info = db_packages.get(pkg_key)
        if not pkg_info:
            continue

        # Get course IDs for this package
        pkg_course_ids = [
            course_id_map[code]
            for code in pkg_info.course_codes
            if code in course_id_map
        ]

        # Filter sections to this package's courses
        pkg_sections = {
            cid: all_sections_by_course[cid]
            for cid in pkg_course_ids
            if cid in all_sections_by_course
        }

        if not pkg_sections:
            print(f"  {pkg_key}: No sections found, skipping")
            continue

        # Build logical sections
        logical_by_course = optimizer.build_logical_sections(pkg_sections)

        # Enumerate combos
        combos_by_course = optimizer.enumerate_all_course_combos(logical_by_course)

        packages[pkg_key] = PackageData(
            package_key=pkg_key,
            course_ids=pkg_course_ids,
            course_codes=pkg_info.course_codes,
            logical_by_course=logical_by_course,
            combos_by_course=combos_by_course,
            students=pkg_students,
        )

        total_combos = sum(len(c[0]) for c in combos_by_course.values())
        print(
            f"  {pkg_key}: {len(pkg_students)} students, "
            f"{len(combos_by_course)} courses, {total_combos} total combos"
        )

    # ==================== Phase 3: Demand Analysis ====================
    print("\n" + "-" * 80)
    print("PHASE 3: ANALYZING DEMAND")
    print("-" * 80)

    # Count potential demand per section
    for pkg_key, package in packages.items():
        for student_info in package.students:
            for course_id, (combos, _) in package.combos_by_course.items():
                # Mark all sections in any valid combo as potentially demanded
                seen_class_nbrs = set()
                for combo in combos:
                    for section in combo:
                        if section.class_nbr not in seen_class_nbrs:
                            seen_class_nbrs.add(section.class_nbr)
                            demand = global_state.section_demands.get(section.class_nbr)
                            if demand:
                                demand.potential_demand += 1

    # Identify bottleneck sections
    for class_nbr, demand in global_state.section_demands.items():
        if demand.scarcity_score > config.bottleneck_threshold:
            global_state.bottleneck_sections.add(class_nbr)

    print(f"  Identified {len(global_state.bottleneck_sections)} bottleneck sections")

    # Calculate student flexibility
    all_students: list[tuple[int, StudentInfo, str]] = []

    for pkg_key, package in packages.items():
        for student_info in package.students:
            # Flexibility = total valid combos across all courses
            flex_score = 0
            for course_id, (combos, _) in package.combos_by_course.items():
                flex_score += len(combos)

            all_students.append((flex_score, student_info, pkg_key))

    # Sort by flexibility (ascending - most constrained first)
    all_students.sort(key=lambda x: x[0])

    print(f"  Total students to allocate: {len(all_students)}")
    if all_students:
        print(f"  Flexibility range: {all_students[0][0]} - {all_students[-1][0]}")

    # ==================== Phase 4: Allocation ====================
    print("\n" + "-" * 80)
    print(f"PHASE 4: ALLOCATING ({config.strategy.upper()} STRATEGY)")
    print("-" * 80)

    success_count = 0
    partial_count = 0
    fail_count = 0

    for i, (flex_score, student_info, pkg_key) in enumerate(all_students):
        package = packages.get(pkg_key)
        if not package:
            continue

        # Create allocation
        student_id = await get_or_create_student(
            schema_name, student_info.campus_id, student_info.name
        )

        allocation = StudentAllocation(
            student_info=student_info,
            student_id=student_id,
            package_key=pkg_key,
            flexibility_score=flex_score,
        )
        global_state.allocations[student_info.campus_id] = allocation

        remaining = len(all_students) - i - 1

        # Allocate based on strategy
        if config.strategy == "greedy":
            optimizer.allocate_student_greedy(
                allocation, package, global_state, remaining
            )
        else:
            optimizer.allocate_student_backtrack(
                allocation, package, global_state, remaining
            )

        if allocation.success:
            success_count += 1
        elif allocation.partial:
            partial_count += 1
        else:
            fail_count += 1

        # Progress update
        if (i + 1) % 50 == 0 or (i + 1) == len(all_students):
            total = success_count + partial_count + fail_count
            pct = (success_count / total * 100) if total > 0 else 0
            print(
                f"  [{i + 1:4d}/{len(all_students)}] "
                f"Success: {success_count} ({pct:.1f}%) | "
                f"Partial: {partial_count} | Failed: {fail_count}"
            )

    # ==================== Phase 5: Redistribution ====================
    if config.enable_redistribution:
        print("\n" + "-" * 80)
        print("PHASE 5: REDISTRIBUTION")
        print("-" * 80)

        partial_students = [
            alloc
            for alloc in global_state.allocations.values()
            if alloc.partial or not alloc.success
        ]
        print(f"  Students needing redistribution: {len(partial_students)}")

        if partial_students:
            redistributor = Redistributor(optimizer, config)
            for round_num in range(config.max_redistribution_rounds):
                fixed = redistributor.redistribute(
                    partial_students, packages, global_state
                )
                print(f"  Round {round_num + 1}: Fixed {fixed} students")

                if fixed == 0:
                    break

                # Update partial list
                partial_students = [
                    alloc for alloc in partial_students if not alloc.success
                ]

            # Recount
            success_count = sum(
                1 for a in global_state.allocations.values() if a.success
            )
            partial_count = sum(
                1 for a in global_state.allocations.values() if a.partial
            )
            fail_count = sum(
                1
                for a in global_state.allocations.values()
                if not a.success and not a.partial
            )

    # ==================== Phase 6: Commit ====================
    if not dry_run:
        print("\n" + "-" * 80)
        print("PHASE 6: COMMITTING TO DATABASE")
        print("-" * 80)

        committed = 0
        for campus_id, allocation in global_state.allocations.items():
            if allocation.success and allocation.selected_sections:
                timetable_id = await commit_timetable(
                    schema_name,
                    allocation.student_id,
                    allocation.selected_sections,
                )
                if timetable_id:
                    committed += 1

        print(f"  Committed {committed} timetables")

    # ==================== Summary ====================
    print("\n" + "=" * 80)
    print(" " * 30 + "SUMMARY")
    print("=" * 80)

    total = success_count + partial_count + fail_count

    print(f"\n{'Overall Results':^80}")
    print("-" * 80)
    print(f"  Total students:       {total:5d}")
    print(f"  No package found:     {len(no_package_students):5d}")
    print()
    print(
        f"  Successful:           {success_count:5d}  "
        f"({success_count / total * 100:.1f}%)"
        if total > 0
        else "  Successful:           0"
    )
    print(
        f"  Partial:              {partial_count:5d}  "
        f"({partial_count / total * 100:.1f}%)"
        if total > 0
        else "  Partial:              0"
    )
    print(
        f"  Failed:               {fail_count:5d}  ({fail_count / total * 100:.1f}%)"
        if total > 0
        else "  Failed:               0"
    )

    # Section utilization
    print(f"\n{'Section Utilization':^80}")
    print("-" * 80)

    # Count what we added
    total_allocations_made = 0
    sections_we_used = 0
    for class_nbr in global_state.section_enrollment:
        current = global_state.section_enrollment[class_nbr]
        initial = global_state.initial_enrollment.get(class_nbr, 0)
        added = current - initial
        if added > 0:
            total_allocations_made += added
            sections_we_used += 1

    print(f"  Total section allocations made: {total_allocations_made}")
    print(f"  Unique sections used: {sections_we_used}")

    high_util = []
    for class_nbr, demand in global_state.section_demands.items():
        enrollment = global_state.section_enrollment.get(class_nbr, 0)
        initial = global_state.initial_enrollment.get(class_nbr, 0)
        added = enrollment - initial
        if demand.capacity > 0:
            util = enrollment / demand.capacity
            if util > 0.9:
                high_util.append((class_nbr, enrollment, demand.capacity, util, added))

    high_util.sort(key=lambda x: -x[3])
    print(f"\n  Sections at >90% capacity: {len(high_util)}")
    for class_nbr, enrl, cap, util, added in high_util[:10]:
        added_str = f" (+{added})" if added > 0 else " (DB only)"
        print(f"    Class {class_nbr}: {enrl}/{cap} ({util * 100:.1f}%){added_str}")
    if len(high_util) > 10:
        print(f"    ... and {len(high_util) - 10} more")

    # Bottleneck analysis
    print(f"\n{'Bottleneck Analysis':^80}")
    print("-" * 80)
    print(
        f"  Sections identified as bottlenecks: {len(global_state.bottleneck_sections)}"
    )

    print("\n" + "=" * 80)


async def main():
    parser = argparse.ArgumentParser(
        description="Globally Optimized Bulk Timetable Generator"
    )
    parser.add_argument(
        "--excel",
        type=str,
        help="Path to Excel file with student list",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=2025,
        help="Year for package filtering (default: 2025)",
    )
    parser.add_argument(
        "--session",
        type=str,
        help="Session ID (if not provided, interactive selection)",
    )
    parser.add_argument(
        "--strategy",
        type=str,
        default="greedy",
        choices=["greedy", "backtrack"],
        help="Allocation strategy (default: greedy)",
    )
    parser.add_argument(
        "--redistribution",
        action="store_true",
        default=True,
        help="Enable redistribution phase (default: True)",
    )
    parser.add_argument(
        "--no-redistribution",
        action="store_true",
        help="Disable redistribution phase",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate without committing to database",
    )
    parser.add_argument(
        "--ignore-existing",
        action="store_true",
        help="Ignore existing enrollment in DB (start from 0 for all sections)",
    )
    parser.add_argument(
        "--list-sessions",
        action="store_true",
        help="List available sessions and exit",
    )

    args = parser.parse_args()

    print("=" * 80)
    print(" " * 15 + "GLOBAL OPTIMIZED BULK TIMETABLE GENERATOR")
    print("=" * 80)

    # List sessions only
    if args.list_sessions:
        sessions = await list_sessions()
        print("\nAvailable Sessions:")
        for s in sessions:
            print(f"  ID: {s['id']}")
            print(f"  Name: {s['name']}")
            print(f"  Term: {s['term_code']}")
            print(f"  Schema: {s['schema_name']}")
            print()
        return

    # Get session
    if args.session:
        session_info = await get_session_by_id(args.session)
        if not session_info:
            print(f"Session not found: {args.session}")
            return
    else:
        session_info = await interactive_session_select()
        if not session_info:
            print("No session selected. Exiting.")
            return

    # Check Excel file
    if not args.excel:
        args.excel = input("Enter path to Excel file: ").strip()

    if not Path(args.excel).exists():
        print(f"Excel file not found: {args.excel}")
        return

    # Build config
    config = GlobalOptimizationConfig(
        strategy=args.strategy,
        enable_redistribution=not args.no_redistribution,
    )

    # Run optimization
    await run_global_optimization(
        session_info=session_info,
        excel_path=args.excel,
        year=args.year,
        config=config,
        dry_run=args.dry_run,
        ignore_existing_enrollment=args.ignore_existing,
    )


if __name__ == "__main__":
    asyncio.run(main())
