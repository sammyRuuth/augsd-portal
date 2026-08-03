"""
Timetable allocation for individual plans.

Handles the greedy algorithm for generating timetables that satisfy
all course requirements without conflicts.
"""

import math
import random
from typing import Optional

from ..config import Config
from ..models import Section, Timetable, TimetableAssignment
from ..parsers.packages import find_course_match
from .combos import generate_course_combos
from .constraints import (
    CapacityTracker,
    combo_clashes_with_current,
    has_internal_clash,
)


class TimetableAllocator:
    """
    Allocates timetables for a single academic plan.

    Uses a greedy algorithm with multiple random restarts to find
    valid timetables that satisfy all course requirements.
    """

    def __init__(
        self,
        config: Config,
        sections_by_course: dict[str, list[Section]],
        capacity_tracker: CapacityTracker,
    ):
        """
        Initialize allocator.

        Args:
            config: Configuration object
            sections_by_course: All available sections by course
            capacity_tracker: Shared capacity tracker
        """
        self.config = config
        self.sections_by_course = sections_by_course
        self.capacity_tracker = capacity_tracker

    def allocate_plan(
        self,
        plan: str,
        courses: list[str],
        student_count: int,
    ) -> TimetableAssignment:
        """
        Allocate timetables for a plan.

        Args:
            plan: Plan name
            courses: Required course codes
            student_count: Number of students to assign

        Returns:
            TimetableAssignment with generated timetables
        """
        assignment = TimetableAssignment(
            plan=plan,
            student_count=student_count,
        )

        if student_count == 0:
            return assignment

        # Resolve course codes to available courses
        resolved_courses = []
        for code in courses:
            matched = find_course_match(code, set(self.sections_by_course.keys()))
            if matched:
                resolved_courses.append(matched)

        if not resolved_courses:
            assignment.unassigned_students = student_count
            return assignment

        # Generate timetables
        students_remaining = student_count
        timetable_id = 1
        allow_tut_overfill = False
        seen_class_sets: set[frozenset[int]] = set()

        min_timetables = self.config.generator.min_timetables_per_plan

        while students_remaining > 0 or len(assignment.timetables) < min_timetables:
            # Try to generate a timetable
            sections = self._generate_single_timetable(
                resolved_courses,
                allow_tut_overfill=allow_tut_overfill,
            )

            if sections is None:
                if not allow_tut_overfill:
                    # Retry with TUT overfill allowed
                    allow_tut_overfill = True
                    sections = self._generate_single_timetable(
                        resolved_courses,
                        allow_tut_overfill=True,
                    )

                if sections is None:
                    break

            # Check for duplicate combos and try to diversify
            class_set = frozenset(s.class_nbr for s in sections)

            if (
                class_set in seen_class_sets
                and len(assignment.timetables) < min_timetables
            ):
                # Try to find a different combo
                diversified = False
                for _ in range(self.config.generator.variant_retry_attempts):
                    alt_sections = self._generate_single_timetable(
                        resolved_courses,
                        allow_tut_overfill=allow_tut_overfill,
                        avoid_class_nbrs=set(class_set),
                    )
                    if alt_sections:
                        alt_set = frozenset(s.class_nbr for s in alt_sections)
                        if alt_set not in seen_class_sets:
                            sections = alt_sections
                            class_set = alt_set
                            diversified = True
                            break

                if not diversified and students_remaining <= 0:
                    # Can't diversify and no students left - stop
                    break

            seen_class_sets.add(class_set)

            # Calculate batch size
            max_batch = self.capacity_tracker.calculate_max_batch(
                sections,
                allow_tut_overfill,
            )

            if max_batch <= 0 and students_remaining > 0:
                break

            # Determine batch size
            variant_only = students_remaining <= 0
            target_remaining = max(1, min_timetables - len(assignment.timetables))

            if students_remaining > 0:
                max_for_target = max(
                    1, math.ceil(students_remaining / target_remaining)
                )
            else:
                max_for_target = self.config.capacity.default_capacity

            if not variant_only:
                batch_size = self._choose_batch_size(
                    base_size=min(max_batch, students_remaining),
                    default_cap=min(
                        self.config.capacity.default_capacity, max_for_target
                    ),
                    remaining=students_remaining,
                )
            else:
                batch_size = 0

            if batch_size <= 0 and not variant_only:
                break

            # Calculate capacity ceiling
            capacity_ceiling = min(
                max_batch if max_batch < 999999 else students_remaining,
                max_for_target,
            )

            if variant_only and capacity_ceiling <= 0:
                break

            # Create timetable
            timetable = Timetable(
                plan=plan,
                timetable_id=timetable_id,
                sections=list(sections),
                batch_size=batch_size,
                capacity_ceiling=max(0, capacity_ceiling),
                is_variant=variant_only,
            )

            assignment.timetables.append(timetable)

            # Allocate capacity
            if batch_size > 0:
                for section in sections:
                    self.capacity_tracker.allocate(section.class_nbr, batch_size)
                students_remaining -= batch_size
                assignment.students_assigned += batch_size

            timetable_id += 1

        assignment.unassigned_students = max(
            0, student_count - assignment.students_assigned
        )
        return assignment

    def _generate_single_timetable(
        self,
        courses: list[str],
        allow_tut_overfill: bool = False,
        avoid_class_nbrs: Optional[set[int]] = None,
    ) -> Optional[list[Section]]:
        """
        Generate a single valid timetable using greedy algorithm.

        Args:
            courses: Required course codes
            allow_tut_overfill: Whether TUT sections can overfill
            avoid_class_nbrs: Sections to avoid (for variant generation)

        Returns:
            List of sections forming a valid timetable, or None
        """
        # Build combos for each course
        course_combos: list[tuple[str, list[list[Section]]]] = []

        for code in courses:
            sections = self.sections_by_course.get(code, [])
            if not sections:
                return None  # Required course not available

            combos = generate_course_combos(
                sections,
                self.capacity_tracker,
                self.config,
                allow_tut_overfill,
                avoid_class_nbrs,
            )

            if not combos:
                return None  # No valid combos for this course

            course_combos.append((code, combos))

        if not course_combos:
            return None

        # Sort by constraint (fewer combos first)
        course_combos.sort(key=lambda x: len(x[1]))

        # Try multiple random attempts
        max_attempts = self.config.generator.max_greedy_attempts

        for attempt in range(max_attempts):
            selected: list[Section] = []
            success = True

            # Shuffle for variety (except first attempt)
            attempt_combos = course_combos[:]
            if attempt > 0:
                random.shuffle(attempt_combos)

            for code, combos in attempt_combos:
                # Shuffle combos for variety
                if attempt > 0 and len(combos) > 1:
                    combos = random.sample(combos, len(combos))

                found = False
                for combo in combos:
                    if has_internal_clash(combo):
                        continue
                    if combo_clashes_with_current(combo, selected):
                        continue

                    # Valid combo found
                    selected.extend(combo)
                    found = True
                    break

                if not found:
                    success = False
                    break

            if success:
                return selected

        return None

    def _choose_batch_size(
        self,
        base_size: int,
        default_cap: int,
        remaining: int,
    ) -> int:
        """
        Choose batch size with optional randomization.

        Args:
            base_size: Maximum batch size from capacity
            default_cap: Default capacity limit
            remaining: Students remaining to assign

        Returns:
            Batch size to use
        """
        if base_size <= 0 or remaining <= 0:
            return 0

        upper = min(base_size, default_cap, remaining)
        lower = max(1, default_cap // 2)
        lower = max(1, min(lower, upper))

        if not self.config.generator.batch_randomness or upper <= lower:
            return upper

        return random.randint(lower, upper)
