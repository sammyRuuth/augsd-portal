"""Random and randomized algorithms for timetable generation"""

import random
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


class RandomAlgorithm(TimetableAlgorithm):
    """
    Pure random algorithm - generates random valid timetables.

    Strategy:
    1. Shuffle course order randomly
    2. For each course, pick a random valid combo
    3. Repeat N times and keep the best

    Pros: Very fast, can find unexpected solutions
    Cons: No guarantee of optimality
    """

    name = "random"
    description = "Random sampling - fast exploration of solution space"

    def __init__(
        self,
        constraints: GenerationConstraints | None = None,
        num_samples: int = 100,
    ):
        super().__init__(constraints)
        self.num_samples = num_samples

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

        # Build course_combos and course_meta from the result
        course_combos: dict[uuid.UUID, list[list[LogicalSection]]] = {}
        course_meta: dict[uuid.UUID, dict[str, Any]] = {}

        for course_id, (combos, meta) in all_combos.items():
            course_combos[course_id] = combos
            course_meta[course_id] = meta

        if not course_combos:
            return AlgorithmResult(
                success=False,
                message="No valid course combinations",
                algorithm_name=self.name,
            )

        course_ids = list(course_combos.keys())
        best_solution: dict[str, Any] | None = None
        best_score = -1

        # Random sampling
        for _ in range(self.num_samples):
            self._iterations += 1
            solution = self._generate_random_solution(
                course_ids, course_combos, course_meta
            )
            score = len(solution["courses"]) * 1000 + solution.get("seat_score", 0)

            if score > best_score:
                best_score = score
                best_solution = solution

            # Early termination if we scheduled all courses
            if len(solution["courses"]) == len(course_ids):
                break

        execution_time = (time_module.perf_counter() - start_time) * 1000

        if not best_solution:
            return AlgorithmResult(
                success=False,
                message="Could not find any valid solution",
                algorithm_name=self.name,
                execution_time_ms=execution_time,
                iterations=self._iterations,
            )

        # Build conflicts
        conflicts = []
        for course_id in course_ids:
            if course_id not in best_solution["courses"]:
                meta = course_meta.get(course_id, {})
                conflicts.append(
                    {
                        "type": "not_scheduled",
                        "message": f"Could not schedule {meta.get('subject', '')} {meta.get('catalog', '')}",
                        "course_id": str(course_id),
                    }
                )

        return AlgorithmResult(
            success=len(conflicts) == 0,
            partial=len(conflicts) > 0 and len(best_solution["sections"]) > 0,
            selected_sections=best_solution["sections"],
            section_ids=self.get_section_ids(best_solution["sections"]),
            conflicts=conflicts,
            total_units=best_solution["units"],
            course_count=len(best_solution["courses"]),
            algorithm_name=self.name,
            execution_time_ms=execution_time,
            iterations=self._iterations,
            message=f"Best of {self._iterations} random samples",
        )

    def _generate_random_solution(
        self,
        course_ids: list[uuid.UUID],
        course_combos: dict[uuid.UUID, list[list[LogicalSection]]],
        course_meta: dict[uuid.UUID, dict[str, Any]],
    ) -> dict[str, Any]:
        """Generate one random solution - optimized"""
        # Shuffle course order
        shuffled = course_ids[:]
        random.shuffle(shuffled)

        masks: dict[str, int] = {}
        exams: dict[uuid.UUID, tuple] = {}
        units = 0.0
        sections: list[LogicalSection] = []
        scheduled: set[uuid.UUID] = set()
        seat_score = 0

        for course_id in shuffled:
            combos = course_combos.get(course_id, [])
            if not combos:
                continue

            meta = course_meta.get(course_id, {})
            course_units = meta.get("units", 0)

            if units + course_units > self.constraints.max_units:
                continue

            # Try random combo indices
            indices = list(range(len(combos)))
            random.shuffle(indices)

            for idx in indices:
                combo = combos[idx]
                if not self.combo_clashes_with_masks(combo, masks, exams):
                    self.merge_combo_to_schedule(combo, masks, exams)
                    sections.extend(combo)
                    for section in combo:
                        seat_score += section.seat_score
                    units += course_units
                    scheduled.add(course_id)
                    break

        return {
            "sections": sections,
            "units": units,
            "courses": scheduled,
            "seat_score": seat_score,
        }


class RandomRestartAlgorithm(TimetableAlgorithm):
    """
    Random restart hill climbing algorithm.

    Strategy:
    1. Generate random initial solution
    2. Try local improvements (swap sections)
    3. Restart from new random solution
    4. Keep track of best overall

    Pros: Escapes local optima, finds good solutions
    Cons: May not find global optimum
    """

    name = "random_restart"
    description = "Random restart hill climbing - escapes local optima"

    def __init__(
        self,
        constraints: GenerationConstraints | None = None,
        num_restarts: int = 20,
        improvements_per_restart: int = 50,
    ):
        super().__init__(constraints)
        self.num_restarts = num_restarts
        self.improvements_per_restart = improvements_per_restart

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

        # Build course_combos and course_meta from the result
        course_combos: dict[uuid.UUID, list[list[LogicalSection]]] = {}
        course_meta: dict[uuid.UUID, dict[str, Any]] = {}

        for course_id, (combos, meta) in all_combos.items():
            course_combos[course_id] = combos
            course_meta[course_id] = meta

        if not course_combos:
            return AlgorithmResult(
                success=False,
                message="No valid course combinations",
                algorithm_name=self.name,
            )

        course_ids = list(course_combos.keys())
        best_solution: dict[str, Any] | None = None
        best_score = -1

        # Random restarts with local search
        for _ in range(self.num_restarts):
            # Generate random initial solution
            solution = self._generate_random_solution(
                course_ids, course_combos, course_meta
            )

            # Local improvement phase
            for _ in range(self.improvements_per_restart):
                self._iterations += 1
                improved = self._try_improve(
                    solution, course_ids, course_combos, course_meta
                )
                if not improved:
                    break

            score = len(solution["courses"]) * 1000 + solution.get("seat_score", 0)
            if score > best_score:
                best_score = score
                best_solution = solution

            # Early termination
            if len(solution["courses"]) == len(course_ids):
                break

        execution_time = (time_module.perf_counter() - start_time) * 1000

        if not best_solution:
            return AlgorithmResult(
                success=False,
                message="Could not find any valid solution",
                algorithm_name=self.name,
                execution_time_ms=execution_time,
                iterations=self._iterations,
            )

        # Build conflicts
        conflicts = []
        for course_id in course_ids:
            if course_id not in best_solution["courses"]:
                meta = course_meta.get(course_id, {})
                conflicts.append(
                    {
                        "type": "not_scheduled",
                        "message": f"Could not schedule {meta.get('subject', '')} {meta.get('catalog', '')}",
                        "course_id": str(course_id),
                    }
                )

        return AlgorithmResult(
            success=len(conflicts) == 0,
            partial=len(conflicts) > 0 and len(best_solution["sections"]) > 0,
            selected_sections=best_solution["sections"],
            section_ids=self.get_section_ids(best_solution["sections"]),
            conflicts=conflicts,
            total_units=best_solution["units"],
            course_count=len(best_solution["courses"]),
            algorithm_name=self.name,
            execution_time_ms=execution_time,
            iterations=self._iterations,
            message=f"Best of {self.num_restarts} restarts",
        )

    def _generate_random_solution(
        self,
        course_ids: list[uuid.UUID],
        course_combos: dict[uuid.UUID, list[list[LogicalSection]]],
        course_meta: dict[uuid.UUID, dict[str, Any]],
    ) -> dict[str, Any]:
        shuffled = course_ids[:]
        random.shuffle(shuffled)

        masks: dict[str, int] = {}
        exams: dict[uuid.UUID, tuple] = {}
        units = 0.0
        sections: list[LogicalSection] = []
        scheduled: set[uuid.UUID] = set()
        seat_score = 0
        combo_choices: dict[uuid.UUID, int] = {}

        for course_id in shuffled:
            combos = course_combos.get(course_id, [])
            if not combos:
                continue

            meta = course_meta.get(course_id, {})
            course_units = meta.get("units", 0)

            if units + course_units > self.constraints.max_units:
                continue

            indices = list(range(len(combos)))
            random.shuffle(indices)

            for idx in indices:
                combo = combos[idx]
                if not self.combo_clashes_with_masks(combo, masks, exams):
                    self.merge_combo_to_schedule(combo, masks, exams)
                    sections.extend(combo)
                    for section in combo:
                        seat_score += section.seat_score
                    units += course_units
                    scheduled.add(course_id)
                    combo_choices[course_id] = idx
                    break

        return {
            "sections": sections,
            "units": units,
            "courses": scheduled,
            "seat_score": seat_score,
            "combo_choices": combo_choices,
            "masks": masks,
            "exams": exams,
        }

    def _try_improve(
        self,
        solution: dict[str, Any],
        course_ids: list[uuid.UUID],
        course_combos: dict[uuid.UUID, list[list[LogicalSection]]],
        course_meta: dict[uuid.UUID, dict[str, Any]],
    ) -> bool:
        """Try to add an unscheduled course - optimized"""
        unscheduled = [cid for cid in course_ids if cid not in solution["courses"]]
        if not unscheduled:
            return False

        # Try to add an unscheduled course
        random.shuffle(unscheduled)

        for target_course in unscheduled:
            combos = course_combos.get(target_course, [])
            if not combos:
                continue

            meta = course_meta.get(target_course, {})
            target_units = meta.get("units", 0)

            if solution["units"] + target_units > self.constraints.max_units:
                continue

            # Try each combo
            for combo in combos:
                if not self.combo_clashes_with_masks(
                    combo, solution["masks"], solution["exams"]
                ):
                    # Add it using batch method
                    self.merge_combo_to_schedule(
                        combo, solution["masks"], solution["exams"]
                    )
                    solution["sections"].extend(combo)
                    for section in combo:
                        solution["seat_score"] = (
                            solution.get("seat_score", 0) + section.seat_score
                        )
                    solution["units"] += target_units
                    solution["courses"].add(target_course)
                    return True

        return False


class SimulatedAnnealingAlgorithm(TimetableAlgorithm):
    """
    Simulated annealing algorithm for timetable generation.

    Strategy:
    1. Start with a random solution
    2. Make random changes (swap combos)
    3. Accept worse solutions with decreasing probability
    4. Gradually "cool down" to converge

    Pros: Can escape local optima, finds good solutions
    Cons: Requires tuning of temperature schedule
    """

    name = "simulated_annealing"
    description = "Simulated annealing - probabilistic optimization"

    def __init__(
        self,
        constraints: GenerationConstraints | None = None,
        initial_temp: float = 100.0,
        cooling_rate: float = 0.95,
        min_temp: float = 0.1,
        iterations_per_temp: int = 20,
    ):
        super().__init__(constraints)
        self.initial_temp = initial_temp
        self.cooling_rate = cooling_rate
        self.min_temp = min_temp
        self.iterations_per_temp = iterations_per_temp

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

        # Build course_combos and course_meta from the result
        course_combos: dict[uuid.UUID, list[list[LogicalSection]]] = {}
        course_meta: dict[uuid.UUID, dict[str, Any]] = {}

        for course_id, (combos, meta) in all_combos.items():
            course_combos[course_id] = combos
            course_meta[course_id] = meta

        if not course_combos:
            return AlgorithmResult(
                success=False,
                message="No valid course combinations",
                algorithm_name=self.name,
            )

        course_ids = list(course_combos.keys())

        # Generate initial solution
        current = self._generate_greedy_solution(course_ids, course_combos, course_meta)
        current_score = self._score(current, len(course_ids))

        best = current.copy()
        best["sections"] = current["sections"][:]
        best["courses"] = set(current["courses"])
        best_score = current_score

        # Simulated annealing
        temp = self.initial_temp

        while temp > self.min_temp:
            for _ in range(self.iterations_per_temp):
                self._iterations += 1

                # Generate neighbor
                neighbor = self._get_neighbor(
                    current, course_ids, course_combos, course_meta
                )
                if neighbor is None:
                    continue

                neighbor_score = self._score(neighbor, len(course_ids))

                # Accept or reject
                delta = neighbor_score - current_score
                if delta > 0 or random.random() < self._acceptance_prob(delta, temp):
                    current = neighbor
                    current_score = neighbor_score

                    if current_score > best_score:
                        best = current.copy()
                        best["sections"] = current["sections"][:]
                        best["courses"] = set(current["courses"])
                        best_score = current_score

            temp *= self.cooling_rate

        execution_time = (time_module.perf_counter() - start_time) * 1000

        # Build conflicts
        conflicts = []
        for course_id in course_ids:
            if course_id not in best["courses"]:
                meta = course_meta.get(course_id, {})
                conflicts.append(
                    {
                        "type": "not_scheduled",
                        "message": f"Could not schedule {meta.get('subject', '')} {meta.get('catalog', '')}",
                        "course_id": str(course_id),
                    }
                )

        return AlgorithmResult(
            success=len(conflicts) == 0,
            partial=len(conflicts) > 0 and len(best["sections"]) > 0,
            selected_sections=best["sections"],
            section_ids=self.get_section_ids(best["sections"]),
            conflicts=conflicts,
            total_units=best["units"],
            course_count=len(best["courses"]),
            algorithm_name=self.name,
            execution_time_ms=execution_time,
            iterations=self._iterations,
            message=f"Annealing complete after {self._iterations} iterations",
        )

    def _generate_greedy_solution(
        self,
        course_ids: list[uuid.UUID],
        course_combos: dict[uuid.UUID, list[list[LogicalSection]]],
        course_meta: dict[uuid.UUID, dict[str, Any]],
    ) -> dict[str, Any]:
        """Generate a greedy initial solution - optimized"""
        # Sort by constraint (fewer combos first)
        sorted_ids = sorted(course_ids, key=lambda cid: len(course_combos.get(cid, [])))

        masks: dict[str, int] = {}
        exams: dict[uuid.UUID, tuple] = {}
        units = 0.0
        sections: list[LogicalSection] = []
        scheduled: set[uuid.UUID] = set()
        combo_choices: dict[uuid.UUID, int] = {}

        for course_id in sorted_ids:
            combos = course_combos.get(course_id, [])
            if not combos:
                continue

            meta = course_meta.get(course_id, {})
            course_units = meta.get("units", 0)

            if units + course_units > self.constraints.max_units:
                continue

            for idx, combo in enumerate(combos):
                if not self.combo_clashes_with_masks(combo, masks, exams):
                    self.merge_combo_to_schedule(combo, masks, exams)
                    sections.extend(combo)
                    units += course_units
                    scheduled.add(course_id)
                    combo_choices[course_id] = idx
                    break

        return {
            "sections": sections,
            "units": units,
            "courses": scheduled,
            "combo_choices": combo_choices,
        }

    def _score(self, solution: dict[str, Any], total_courses: int) -> float:
        """Score a solution (higher is better)"""
        courses_scheduled = len(solution["courses"])
        seat_score = sum(s.seat_score for s in solution["sections"])
        return courses_scheduled * 10000 + seat_score

    def _acceptance_prob(self, delta: float, temp: float) -> float:
        """Calculate acceptance probability for worse solution"""
        import math

        try:
            return math.exp(delta / temp)
        except OverflowError:
            return 0.0

    def _get_neighbor(
        self,
        current: dict[str, Any],
        course_ids: list[uuid.UUID],
        course_combos: dict[uuid.UUID, list[list[LogicalSection]]],
        course_meta: dict[uuid.UUID, dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Generate a neighbor solution by random modification"""
        # Randomly choose a modification type
        mod_type = random.choice(["swap", "add", "remove"])

        if mod_type == "swap" and current["courses"]:
            return self._neighbor_swap(current, course_combos)
        elif mod_type == "add":
            return self._neighbor_add(current, course_ids, course_combos, course_meta)
        elif mod_type == "remove" and current["courses"]:
            return self._neighbor_remove(current, course_combos, course_meta)

        return None

    def _neighbor_swap(
        self,
        current: dict[str, Any],
        course_combos: dict[uuid.UUID, list[list[LogicalSection]]],
    ) -> dict[str, Any] | None:
        """Swap combo for a scheduled course - optimized"""
        if not current["courses"]:
            return None

        course_id = random.choice(list(current["courses"]))
        combos = course_combos.get(course_id, [])
        if len(combos) <= 1:
            return None

        current_idx = current.get("combo_choices", {}).get(course_id, 0)
        new_idx = random.randint(0, len(combos) - 1)
        if new_idx == current_idx:
            return None

        # Build new solution without this course
        new_sections = [s for s in current["sections"] if s.course_id != course_id]
        new_courses = set(current["courses"]) - {course_id}

        # Rebuild masks
        masks: dict[str, int] = {}
        exams: dict[uuid.UUID, tuple] = {}
        for section in new_sections:
            for day, mask in section.day_masks.items():
                masks[day] = masks.get(day, 0) | mask
            if (
                section.course_id not in exams
                and section.exam_date
                and section.exam_start
                and section.exam_end
            ):
                exams[section.course_id] = (
                    section.exam_date,
                    self.time_obj_to_minutes(section.exam_start),
                    self.time_obj_to_minutes(section.exam_end),
                )

        # Try to add new combo
        new_combo = combos[new_idx]
        if self.combo_clashes_with_masks(new_combo, masks, exams):
            return None

        self.merge_combo_to_schedule(new_combo, masks, exams)
        new_sections.extend(new_combo)
        new_courses.add(course_id)

        new_choices = current.get("combo_choices", {}).copy()
        new_choices[course_id] = new_idx

        return {
            "sections": new_sections,
            "units": current["units"],
            "courses": new_courses,
            "combo_choices": new_choices,
        }

    def _neighbor_add(
        self,
        current: dict[str, Any],
        course_ids: list[uuid.UUID],
        course_combos: dict[uuid.UUID, list[list[LogicalSection]]],
        course_meta: dict[uuid.UUID, dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Try to add an unscheduled course - optimized"""
        unscheduled = [cid for cid in course_ids if cid not in current["courses"]]
        if not unscheduled:
            return None

        course_id = random.choice(unscheduled)
        combos = course_combos.get(course_id, [])
        if not combos:
            return None

        meta = course_meta.get(course_id, {})
        course_units = meta.get("units", 0)

        if current["units"] + course_units > self.constraints.max_units:
            return None

        # Rebuild masks more efficiently
        masks: dict[str, int] = {}
        exams: dict[uuid.UUID, tuple] = {}
        for section in current["sections"]:
            for day, mask in section.day_masks.items():
                masks[day] = masks.get(day, 0) | mask
            if (
                section.course_id not in exams
                and section.exam_date
                and section.exam_start
                and section.exam_end
            ):
                exams[section.course_id] = (
                    section.exam_date,
                    self.time_obj_to_minutes(section.exam_start),
                    self.time_obj_to_minutes(section.exam_end),
                )

        # Try combos
        indices = list(range(len(combos)))
        random.shuffle(indices)
        for idx in indices:
            combo = combos[idx]
            if not self.combo_clashes_with_masks(combo, masks, exams):
                new_sections = current["sections"][:]
                self.merge_combo_to_schedule(combo, masks, exams)
                new_sections.extend(combo)

                new_courses = set(current["courses"])
                new_courses.add(course_id)

                new_choices = current.get("combo_choices", {}).copy()
                new_choices[course_id] = idx

                return {
                    "sections": new_sections,
                    "units": current["units"] + course_units,
                    "courses": new_courses,
                    "combo_choices": new_choices,
                }

        return None

    def _neighbor_remove(
        self,
        current: dict[str, Any],
        course_combos: dict[uuid.UUID, list[list[LogicalSection]]],
        course_meta: dict[uuid.UUID, dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Remove a course from the schedule"""
        if not current["courses"]:
            return None

        course_id = random.choice(list(current["courses"]))
        meta = course_meta.get(course_id, {})
        course_units = meta.get("units", 0)

        new_sections = [s for s in current["sections"] if s.course_id != course_id]
        new_courses = set(current["courses"]) - {course_id}

        new_choices = current.get("combo_choices", {}).copy()
        new_choices.pop(course_id, None)

        return {
            "sections": new_sections,
            "units": current["units"] - course_units,
            "courses": new_courses,
            "combo_choices": new_choices,
        }
