"""Optimized backtracking algorithm for timetable generation"""

import time as time_module
import uuid
from datetime import time
from typing import Any

from app.core.algorithms.base import (
    AlgorithmResult,
    GenerationConstraints,
    LogicalSection,
    SectionData,
    TimetableAlgorithm,
)


def minutes_to_time_str(minutes: int) -> str:
    """Convert minutes since midnight to HH:MM format"""
    if minutes < 0:
        return "??:??"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours:02d}:{mins:02d}"


def time_to_str(t: time | None) -> str:
    """Convert time object to HH:MM string"""
    if t is None:
        return "??:??"
    return f"{t.hour:02d}:{t.minute:02d}"


def calculate_overlap(
    start1: int, end1: int, start2: int, end2: int
) -> tuple[int, int] | None:
    """Calculate the overlap between two time ranges. Returns (start, end) of overlap or None."""
    overlap_start = max(start1, start2)
    overlap_end = min(end1, end2)
    if overlap_start < overlap_end:
        return (overlap_start, overlap_end)
    return None


class BacktrackAlgorithm(TimetableAlgorithm):
    """
    Optimized backtracking algorithm with aggressive pruning.

    Strategy:
    1. Order courses by constraint level (MRV heuristic)
    2. Use backtracking with efficient state management
    3. Prune branches that can't improve on best solution
    4. Early termination when perfect solution found

    Pros: Finds optimal solution (most courses scheduled)
    Cons: Exponential worst case, but pruning makes it practical
    """

    name = "backtrack"
    description = "Full backtracking search - finds optimal solution"

    def __init__(
        self,
        constraints: GenerationConstraints | None = None,
        max_iterations: int = 100000,
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

        # Precompute valid combos for each course using shared method
        all_combos = self.enumerate_all_course_combos(logical_by_course)

        # Convert to list for ordered processing
        course_combos: list[tuple[uuid.UUID, list[list[LogicalSection]], dict]] = [
            (course_id, combos, meta)
            for course_id, (combos, meta) in all_combos.items()
        ]

        # Sort by constraint (fewer combos first - most constrained, MRV heuristic)
        course_combos.sort(key=lambda x: len(x[1]))

        self._total_courses = len(course_combos)

        # Backtracking state - use simple structures for speed
        best_sections: list[LogicalSection] = []
        best_units = 0.0
        best_courses: set[uuid.UUID] = set()
        best_masks: dict[str, int] = {}
        best_exams: dict[uuid.UUID, tuple] = {}

        current_sections: list[LogicalSection] = []
        current_courses: set[uuid.UUID] = set()
        current_masks: dict[str, int] = {}
        current_exams: dict[uuid.UUID, tuple] = {}

        # Run backtracking with inline state management
        best = {"count": 0}

        def backtrack(idx: int, units: float, count: int) -> bool:
            """Inline backtracking for performance"""
            nonlocal best_sections, best_units, best_courses, best_masks, best_exams

            self._iterations += 1

            if self._iterations > self.max_iterations:
                return False

            # Update best if improved
            if count > best["count"]:
                best["count"] = count
                best_sections = current_sections[:]
                best_units = units
                best_courses = set(current_courses)
                best_masks = dict(current_masks)
                best_exams = dict(current_exams)

                # Perfect solution found
                if count == self._total_courses:
                    return True

            # Base case
            if idx >= len(course_combos):
                return False

            # Pruning: can't possibly beat current best
            remaining = len(course_combos) - idx
            if count + remaining <= best["count"]:
                return False

            course_id, combos, meta = course_combos[idx]
            course_units = meta["units"]

            # Try each combo for this course
            for combo in combos:
                # Unit limit check
                if units + course_units > self.constraints.max_units:
                    continue

                # Clash check using optimized batch method
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
                if backtrack(idx + 1, units + course_units, count + 1):
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
            return backtrack(idx + 1, units, count)

        backtrack(0, 0.0, 0)

        execution_time = (time_module.perf_counter() - start_time) * 1000

        # Build conflicts for unscheduled courses
        conflicts = []
        scheduled_ids = {s.course_id for s in best_sections}

        for course_id, combos, meta in course_combos:
            if course_id not in scheduled_ids:
                analysis = self._analyze_failure(
                    course_id,
                    combos,
                    {
                        "sections": best_sections,
                        "masks": best_masks,
                        "exams": best_exams,
                    },
                )
                conflicts.append(
                    {
                        "type": "scheduling_conflict",
                        "message": f"Could not schedule {meta['subject']} {meta['catalog']} - {analysis['reason']}",
                        "course_id": str(course_id),
                        "course_info": {
                            "subject": meta["subject"],
                            "catalog": meta["catalog"],
                            "title": meta["title"],
                            "units": meta["units"],
                        },
                        "time_clashes": analysis.get("time_clashes", []),
                        "exam_clashes": analysis.get("exam_clashes", []),
                        "unit_conflict": analysis.get("unit_conflict"),
                        "reason": analysis["reason"],
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
        combos: list[list[LogicalSection]],
        best: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Analyze why a course couldn't be scheduled.
        Returns a dict with detailed clash information.
        """
        result: dict[str, Any] = {
            "reason": "",
            "time_clashes": [],
            "exam_clashes": [],
            "unit_conflict": None,
        }

        if not combos:
            result["reason"] = (
                "No valid section combinations available (internal component conflicts or blocked times)"
            )
            return result

        # Check first combo against best schedule
        combo = combos[0]

        # Check unit limit
        if combo:
            course_units = combo[0].max_units
            # Calculate current units from unique courses
            counted_courses: set[uuid.UUID] = set()
            current_units = 0.0
            for s in best["sections"]:
                if s.course_id not in counted_courses:
                    current_units += s.max_units
                    counted_courses.add(s.course_id)

            if current_units + course_units > self.constraints.max_units:
                result["reason"] = (
                    f"Would exceed unit limit ({current_units:.1f} + {course_units:.1f} > {self.constraints.max_units:.1f})"
                )
                result["unit_conflict"] = {
                    "current_units": current_units,
                    "course_units": course_units,
                    "max_units": self.constraints.max_units,
                }
                return result

        # Collect all time clashes
        time_clashes = []
        for section in combo:
            for day, mask in section.day_masks.items():
                if mask & best["masks"].get(day, 0):
                    # Find what it conflicts with
                    for scheduled in best["sections"]:
                        if day in scheduled.day_masks:
                            if mask & scheduled.day_masks[day]:
                                # Get time details from the meetings
                                sec_times = self._get_section_times_for_day(
                                    section, day
                                )
                                sched_times = self._get_section_times_for_day(
                                    scheduled, day
                                )

                                # Calculate overlap
                                overlap_str = ""
                                if sec_times and sched_times:
                                    overlap = calculate_overlap(
                                        sec_times[0],
                                        sec_times[1],
                                        sched_times[0],
                                        sched_times[1],
                                    )
                                    if overlap:
                                        overlap_str = f"{minutes_to_time_str(overlap[0])} - {minutes_to_time_str(overlap[1])}"

                                time_clashes.append(
                                    {
                                        "day": day,
                                        "course1": {
                                            "course_id": str(section.course_id),
                                            "subject": section.subject,
                                            "catalog": section.catalog,
                                            "title": section.title,
                                            "component": section.component,
                                            "section": section.section,
                                            "class_nbr": section.class_nbr,
                                        },
                                        "course1_time": f"{minutes_to_time_str(sec_times[0])} - {minutes_to_time_str(sec_times[1])}"
                                        if sec_times
                                        else "Unknown",
                                        "course2": {
                                            "course_id": str(scheduled.course_id),
                                            "subject": scheduled.subject,
                                            "catalog": scheduled.catalog,
                                            "title": scheduled.title,
                                            "component": scheduled.component,
                                            "section": scheduled.section,
                                            "class_nbr": scheduled.class_nbr,
                                        },
                                        "course2_time": f"{minutes_to_time_str(sched_times[0])} - {minutes_to_time_str(sched_times[1])}"
                                        if sched_times
                                        else "Unknown",
                                        "overlap_time": overlap_str,
                                    }
                                )

        if time_clashes:
            result["time_clashes"] = time_clashes
            # Build summary message
            clash_summaries = []
            for clash in time_clashes:
                clash_summaries.append(
                    f"{clash['course2']['subject']} {clash['course2']['catalog']} on {clash['day']} "
                    f"({clash['overlap_time']})"
                )
            result["reason"] = f"Time conflict with: {'; '.join(clash_summaries)}"
            return result

        # Check exam conflicts
        exam_clashes = []
        for section in combo:
            if section.exam_date and section.exam_start and section.exam_end:
                sec_exam_start = self.time_obj_to_minutes(section.exam_start)
                sec_exam_end = self.time_obj_to_minutes(section.exam_end)

                for sched_course_id, (ex_date, ex_start, ex_end) in best[
                    "exams"
                ].items():
                    if section.exam_date == ex_date:
                        # Find the scheduled section for more info
                        sched_section = None
                        for s in best["sections"]:
                            if s.course_id == sched_course_id:
                                sched_section = s
                                break

                        # Calculate overlap if times overlap
                        overlap_str = None
                        if max(sec_exam_start, ex_start) < min(sec_exam_end, ex_end):
                            overlap = calculate_overlap(
                                sec_exam_start, sec_exam_end, ex_start, ex_end
                            )
                            if overlap:
                                overlap_str = f"{minutes_to_time_str(overlap[0])} - {minutes_to_time_str(overlap[1])}"

                        exam_clashes.append(
                            {
                                "exam_date": str(section.exam_date),
                                "course1": {
                                    "course_id": str(section.course_id),
                                    "subject": section.subject,
                                    "catalog": section.catalog,
                                    "title": section.title,
                                },
                                "course1_exam_time": f"{time_to_str(section.exam_start)} - {time_to_str(section.exam_end)}",
                                "course2": {
                                    "course_id": str(sched_course_id),
                                    "subject": sched_section.subject
                                    if sched_section
                                    else "Unknown",
                                    "catalog": sched_section.catalog
                                    if sched_section
                                    else "",
                                    "title": sched_section.title
                                    if sched_section
                                    else "",
                                },
                                "course2_exam_time": f"{minutes_to_time_str(ex_start)} - {minutes_to_time_str(ex_end)}",
                                "overlap_time": overlap_str,
                            }
                        )

        if exam_clashes:
            result["exam_clashes"] = exam_clashes
            clash_summaries = []
            for clash in exam_clashes:
                clash_summaries.append(
                    f"{clash['course2']['subject']} {clash['course2']['catalog']} on {clash['exam_date']}"
                )
            result["reason"] = f"Exam conflict with: {'; '.join(clash_summaries)}"
            return result

        result["reason"] = (
            "Schedule conflict with other courses (no compatible section combination found)"
        )
        return result

    def _get_section_times_for_day(
        self, section: LogicalSection, day: str
    ) -> tuple[int, int] | None:
        """Get the start and end time (in minutes) for a section on a specific day"""
        for meeting in section.meetings:
            if meeting.day == day and meeting.mtg_start and meeting.mtg_end:
                start = self.time_obj_to_minutes(meeting.mtg_start)
                end = self.time_obj_to_minutes(meeting.mtg_end)
                if start >= 0 and end > start:
                    return (start, end)
        return None

    def _build_message(self, scheduled: set[uuid.UUID], conflicts: list[dict]) -> str:
        if not conflicts:
            return f"Successfully scheduled {len(scheduled)} course(s)"
        elif scheduled:
            return f"Partial schedule: {len(scheduled)} course(s) scheduled, {len(conflicts)} could not fit"
        else:
            return "Could not schedule any courses"
