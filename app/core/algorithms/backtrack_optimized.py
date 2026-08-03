"""Optimized backtracking with better pruning and heuristics"""

import time as time_module
import uuid
from typing import Any

from app.core.algorithms.base import (
    AlgorithmResult,
    GenerationConstraints,
    LogicalSection,
    SectionData,
    TimetableAlgorithm,
)


class BacktrackOptimizedAlgorithm(TimetableAlgorithm):
    """
    Optimized backtracking with dynamic MRV and lightweight constraint propagation.

    Improvements over standard backtrack:
    1. Dynamic variable ordering (MRV - Minimum Remaining Values)
    2. Lazy domain filtering (no full copy on each iteration)
    3. Intelligent pruning with conflict counting
    4. Early termination on perfect solution

    This is faster than standard backtrack for constrained problems.
    """

    name = "backtrack_optimized"
    description = "Optimized backtracking with advanced pruning"

    def __init__(
        self,
        constraints: GenerationConstraints | None = None,
        max_iterations: int = 50000,
    ):
        super().__init__(constraints)
        self.max_iterations = max_iterations
        self._total_courses = 0

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

        # Build course list with metadata, sorted by constraint level
        course_list: list[
            tuple[uuid.UUID, list[list[LogicalSection]], dict[str, Any]]
        ] = []
        for course_id, (combos, meta) in all_combos.items():
            course_list.append(
                (
                    course_id,
                    combos,
                    {
                        "subject": meta["subject"],
                        "catalog": meta["catalog"],
                        "title": meta["title"],
                        "units": meta["units"],
                    },
                )
            )

        # Initial sort by constraint level (MRV)
        course_list.sort(key=lambda x: len(x[1]))

        self._total_courses = len(course_list)

        # Run optimized backtracking
        best_sections: list[LogicalSection] = []
        best_units = 0.0
        best_courses: set[uuid.UUID] = set()
        best_masks: dict[str, int] = {}
        best_exams: dict[uuid.UUID, tuple] = {}
        best_count = [0]

        # Current state
        current_sections: list[LogicalSection] = []
        current_masks: dict[str, int] = {}
        current_exams: dict[uuid.UUID, tuple] = {}
        current_courses: set[uuid.UUID] = set()

        def count_valid_combos(combos: list[list[LogicalSection]]) -> int:
            """Count how many combos are still valid for the current schedule"""
            count = 0
            for combo in combos:
                if not self.combo_clashes_with_masks(
                    combo, current_masks, current_exams
                ):
                    count += 1
            return count

        def search(
            unassigned: list[tuple[uuid.UUID, list[list[LogicalSection]], dict]],
            units: float,
            count: int,
        ) -> bool:
            nonlocal best_sections, best_units, best_courses, best_masks, best_exams

            self._iterations += 1

            if self._iterations > self.max_iterations:
                return False

            # Update best if improved
            if count > best_count[0]:
                best_count[0] = count
                best_sections = current_sections[:]
                best_units = units
                best_courses = set(current_courses)
                best_masks = dict(current_masks)
                best_exams = dict(current_exams)

                # Perfect solution found
                if count == self._total_courses:
                    return True

            if not unassigned:
                return False

            # Pruning: can't possibly beat current best
            if count + len(unassigned) <= best_count[0]:
                return False

            # Dynamic MRV: re-sort by valid combo count for top few candidates
            if len(unassigned) > 3:
                # Only compute exact counts for top candidates
                top_candidates = unassigned[: min(5, len(unassigned))]
                top_candidates.sort(key=lambda x: count_valid_combos(x[1]))
                unassigned = top_candidates + unassigned[min(5, len(unassigned)) :]

            # Pick the most constrained course
            course_id, combos, meta = unassigned[0]
            rest = unassigned[1:]
            course_units = meta["units"]

            # Try each combo
            for combo in combos:
                # Unit limit check
                if units + course_units > self.constraints.max_units:
                    continue

                # Clash check
                if self.combo_clashes_with_masks(combo, current_masks, current_exams):
                    continue

                # Save state for undo
                old_mask_values = {}
                for section in combo:
                    for day in section.day_masks:
                        if day not in old_mask_values:
                            old_mask_values[day] = current_masks.get(day, 0)
                old_exam_keys = set(current_exams.keys())
                sections_len = len(current_sections)

                # Apply combo
                self.merge_combo_to_schedule(combo, current_masks, current_exams)
                current_sections.extend(combo)
                current_courses.add(course_id)

                # Recurse
                if search(rest, units + course_units, count + 1):
                    return True

                # Undo
                for day, old_val in old_mask_values.items():
                    if old_val == 0:
                        current_masks.pop(day, None)
                    else:
                        current_masks[day] = old_val
                for key in set(current_exams.keys()) - old_exam_keys:
                    del current_exams[key]
                del current_sections[sections_len:]
                current_courses.discard(course_id)

            # Option: skip this course
            return search(rest, units, count)

        search(course_list, 0.0, 0)

        execution_time = (time_module.perf_counter() - start_time) * 1000

        # Build conflicts
        conflicts = []
        scheduled_ids = {s.course_id for s in best_sections}

        for course_id, combos, meta in course_list:
            if course_id not in scheduled_ids:
                reason = self._analyze_failure(
                    course_id,
                    {"combos": combos, **meta},
                    {
                        "sections": best_sections,
                        "masks": best_masks,
                        "exams": best_exams,
                    },
                )
                conflicts.append(
                    {
                        "type": "scheduling_conflict",
                        "message": f"Could not schedule {meta['subject']} {meta['catalog']} - {reason}",
                        "course_id": str(course_id),
                    }
                )

        return AlgorithmResult(
            success=len(conflicts) == 0 and len(best_sections) > 0,
            partial=len(conflicts) > 0 and len(best_sections) > 0,
            selected_sections=best_sections,
            section_ids=self.get_section_ids(best_sections),
            conflicts=conflicts,
            total_units=best_units,
            course_count=len(best_courses),
            algorithm_name=self.name,
            execution_time_ms=execution_time,
            iterations=self._iterations,
            message=self._build_message(best_courses, conflicts),
        )

    def _analyze_failure(
        self,
        course_id: uuid.UUID,
        data: dict[str, Any],
        best: dict[str, Any],
    ) -> str:
        combos = data.get("combos", [])
        if not combos:
            return "No valid section combinations (all components have conflicting times or are blocked)"

        combo = combos[0]

        # Check unit limit
        if combo:
            course_units = data.get("units", 0)
            # Dedupe by course to get current units
            counted_courses = set()
            current_units = 0.0
            for s in best["sections"]:
                if s.course_id not in counted_courses:
                    current_units += s.max_units
                    counted_courses.add(s.course_id)

            if current_units + course_units > self.constraints.max_units:
                return f"Would exceed unit limit ({current_units} + {course_units} > {self.constraints.max_units})"

        for section in combo:
            for day, mask in section.day_masks.items():
                if mask & best["masks"].get(day, 0):
                    for scheduled in best["sections"]:
                        if day in scheduled.day_masks:
                            if mask & scheduled.day_masks[day]:
                                return f"Time conflict with {scheduled.subject} {scheduled.catalog} on {day}"

        return "Schedule conflict with other courses"

    def _build_message(self, scheduled: set[uuid.UUID], conflicts: list[dict]) -> str:
        if not conflicts:
            return f"Successfully scheduled {len(scheduled)} course(s)"
        elif scheduled:
            return f"Partial schedule: {len(scheduled)} course(s) scheduled, {len(conflicts)} could not fit"
        else:
            return "Could not schedule any courses"
