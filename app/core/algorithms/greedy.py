"""Greedy algorithm for fast timetable generation"""

import time as time_module
import uuid
from typing import Any

from app.core.algorithms.base import (
    AlgorithmResult,
    LogicalSection,
    SectionData,
    TimetableAlgorithm,
)


class GreedyAlgorithm(TimetableAlgorithm):
    """
    Fast greedy algorithm for timetable generation.

    Strategy:
    1. Sort courses by constraint level (fewest options first)
    2. For each course, greedily select the best fitting section combo
    3. No backtracking - if a course can't fit, skip it

    Pros: Very fast O(n*m) where n=courses, m=sections per course
    Cons: May miss valid solutions that require different ordering
    """

    name = "greedy"
    description = "Fast greedy selection - prioritizes courses with fewer options"

    def generate(
        self,
        sections_by_course: dict[uuid.UUID, list[SectionData]],
    ) -> AlgorithmResult:
        start_time = time_module.perf_counter()
        self._iterations = 0

        if not sections_by_course:
            return AlgorithmResult(
                success=False,
                message="No courses provided",
                algorithm_name=self.name,
            )

        # Build logical sections
        logical_by_course = self.build_logical_sections(sections_by_course)

        # Precompute valid combos using shared method
        all_combos = self.enumerate_all_course_combos(logical_by_course)

        # Convert to list with constraint level for sorting
        course_data: list[
            tuple[uuid.UUID, list[list[LogicalSection]], dict[str, Any]]
        ] = [
            (course_id, combos, meta)
            for course_id, (combos, meta) in all_combos.items()
        ]

        # Sort by constraint (most constrained first - fewer combos)
        course_data.sort(key=lambda x: len(x[1]))

        # Current schedule state
        schedule_masks: dict[str, int] = {}
        schedule_exams: dict[
            uuid.UUID, tuple
        ] = {}  # course_id -> (exam_date, start_mins, end_mins)
        current_units = 0.0
        selected_sections: list[LogicalSection] = []
        scheduled_courses: set[uuid.UUID] = set()
        conflicts: list[dict[str, Any]] = []

        # Greedy selection using precomputed combos
        for course_id, combos, meta in course_data:
            self._iterations += 1

            if not combos:
                continue

            course_units = meta["units"]

            # Check unit limit
            if current_units + course_units > self.constraints.max_units:
                conflicts.append(
                    {
                        "type": "unit_limit",
                        "message": f"Cannot add {meta['subject']} {meta['catalog']} - would exceed unit limit",
                        "course_id": str(course_id),
                    }
                )
                continue

            # Find first combo that fits the schedule (already sorted by seat score)
            found_combo = None
            for combo in combos:
                if not self.combo_clashes_with_masks(
                    combo, schedule_masks, schedule_exams
                ):
                    found_combo = combo
                    break

            if found_combo:
                # Add to schedule using batch method
                self.merge_combo_to_schedule(
                    found_combo, schedule_masks, schedule_exams
                )
                selected_sections.extend(found_combo)
                current_units += course_units
                scheduled_courses.add(course_id)
            else:
                conflicts.append(
                    {
                        "type": "no_valid_combo",
                        "message": f"No compatible sections for {meta['subject']} {meta['catalog']} (all section combinations conflict with scheduled courses or are blocked)",
                        "course_id": str(course_id),
                    }
                )

        execution_time = (time_module.perf_counter() - start_time) * 1000

        return AlgorithmResult(
            success=len(conflicts) == 0 and len(selected_sections) > 0,
            partial=len(conflicts) > 0 and len(selected_sections) > 0,
            selected_sections=selected_sections,
            section_ids=self.get_section_ids(selected_sections),
            conflicts=conflicts,
            total_units=current_units,
            course_count=len(scheduled_courses),
            algorithm_name=self.name,
            execution_time_ms=execution_time,
            iterations=self._iterations,
            message=self._build_message(scheduled_courses, conflicts),
        )

    def _build_message(self, scheduled: set[uuid.UUID], conflicts: list[dict]) -> str:
        if not conflicts:
            return f"Successfully scheduled {len(scheduled)} course(s)"
        elif scheduled:
            return f"Partial schedule: {len(scheduled)} course(s) scheduled, {len(conflicts)} could not fit"
        else:
            return "Could not schedule any courses"
