"""Parallel race algorithm - runs multiple algorithms truly in parallel"""

import time as time_module
import uuid
from concurrent.futures import (
    ProcessPoolExecutor,
    as_completed,
)
from concurrent.futures import (
    TimeoutError as FuturesTimeoutError,
)
from datetime import date, time
from multiprocessing import Manager
from typing import Any

from app.core.algorithms.base import (
    AlgorithmResult,
    GenerationConstraints,
    LogicalSection,
    SeatPreference,
    SectionData,
    TimetableAlgorithm,
)


def _serialize_section_data(section: SectionData) -> dict[str, Any]:
    """Serialize SectionData to dict for IPC (pickle-safe)"""
    return {
        "id": str(section.id),
        "course_id": str(section.course_id),
        "class_nbr": section.class_nbr,
        "section": section.section,
        "component": section.component,
        "day": section.day,
        "mtg_start": section.mtg_start.isoformat() if section.mtg_start else None,
        "mtg_end": section.mtg_end.isoformat() if section.mtg_end else None,
        "exam_date": section.exam_date.isoformat() if section.exam_date else None,
        "exam_start": section.exam_start.isoformat() if section.exam_start else None,
        "exam_end": section.exam_end.isoformat() if section.exam_end else None,
        "instructor": section.instructor,
        "room": section.room,
        "cap_enrl": section.cap_enrl,
        "tot_enrl": section.tot_enrl,
        "subject": section.subject,
        "catalog": section.catalog,
        "title": section.title,
        "max_units": section.max_units,
    }


def _deserialize_section_data(data: dict[str, Any]) -> SectionData:
    """Deserialize dict back to SectionData"""
    return SectionData(
        id=uuid.UUID(data["id"]),
        course_id=uuid.UUID(data["course_id"]),
        class_nbr=data["class_nbr"],
        section=data["section"],
        component=data["component"],
        day=data["day"],
        mtg_start=time.fromisoformat(data["mtg_start"]) if data["mtg_start"] else None,
        mtg_end=time.fromisoformat(data["mtg_end"]) if data["mtg_end"] else None,
        exam_date=date.fromisoformat(data["exam_date"]) if data["exam_date"] else None,
        exam_start=time.fromisoformat(data["exam_start"])
        if data["exam_start"]
        else None,
        exam_end=time.fromisoformat(data["exam_end"]) if data["exam_end"] else None,
        instructor=data["instructor"],
        room=data["room"],
        cap_enrl=data["cap_enrl"],
        tot_enrl=data["tot_enrl"],
        subject=data["subject"],
        catalog=data["catalog"],
        title=data["title"],
        max_units=data["max_units"],
    )


def _serialize_logical_section(section: LogicalSection) -> dict[str, Any]:
    """Serialize LogicalSection to dict for IPC"""
    return {
        "course_id": str(section.course_id),
        "class_nbr": section.class_nbr,
        "component": section.component,
        "section": section.section,
        "subject": section.subject,
        "catalog": section.catalog,
        "title": section.title,
        "max_units": section.max_units,
        "day_masks": dict(section.day_masks),
        "exam_date": section.exam_date.isoformat() if section.exam_date else None,
        "exam_start": section.exam_start.isoformat() if section.exam_start else None,
        "exam_end": section.exam_end.isoformat() if section.exam_end else None,
        "seat_score": section.seat_score,
        "instructor": section.instructor,
        "room": section.room,
        "meetings": [_serialize_section_data(m) for m in section.meetings],
    }


def _deserialize_logical_section(data: dict[str, Any]) -> LogicalSection:
    """Deserialize dict back to LogicalSection"""
    return LogicalSection(
        course_id=uuid.UUID(data["course_id"]),
        class_nbr=data["class_nbr"],
        component=data["component"],
        section=data["section"],
        subject=data["subject"],
        catalog=data["catalog"],
        title=data["title"],
        max_units=data["max_units"],
        day_masks=data["day_masks"],
        exam_date=date.fromisoformat(data["exam_date"]) if data["exam_date"] else None,
        exam_start=time.fromisoformat(data["exam_start"])
        if data["exam_start"]
        else None,
        exam_end=time.fromisoformat(data["exam_end"]) if data["exam_end"] else None,
        seat_score=data["seat_score"],
        instructor=data["instructor"],
        room=data["room"],
        meetings=[_deserialize_section_data(m) for m in data["meetings"]],
    )


def _serialize_constraints(constraints: GenerationConstraints) -> dict[str, Any]:
    """Serialize GenerationConstraints for IPC"""
    return {
        "max_units": constraints.max_units,
        "fixed_sections": {
            str(cid): comps for cid, comps in constraints.fixed_sections.items()
        },
        "blocked_slots": [
            (day, start.isoformat(), end.isoformat())
            for day, start, end in constraints.blocked_slots
        ],
        "seat_preferences": {
            "prefer_lab_seats": constraints.seat_preferences.prefer_lab_seats,
            "prefer_tut_seats": constraints.seat_preferences.prefer_tut_seats,
            "prefer_lec_seats": constraints.seat_preferences.prefer_lec_seats,
        }
        if constraints.seat_preferences
        else None,
    }


def _deserialize_constraints(data: dict[str, Any]) -> GenerationConstraints:
    """Deserialize dict back to GenerationConstraints"""
    seat_pref = None
    if data.get("seat_preferences"):
        seat_pref = SeatPreference(
            prefer_lab_seats=data["seat_preferences"]["prefer_lab_seats"],
            prefer_tut_seats=data["seat_preferences"]["prefer_tut_seats"],
            prefer_lec_seats=data["seat_preferences"]["prefer_lec_seats"],
        )

    fixed_sections = {}
    for cid_str, comps in data.get("fixed_sections", {}).items():
        fixed_sections[uuid.UUID(cid_str)] = comps

    blocked_slots = []
    for day, start_str, end_str in data.get("blocked_slots", []):
        blocked_slots.append(
            (day, time.fromisoformat(start_str), time.fromisoformat(end_str))
        )

    return GenerationConstraints(
        max_units=data.get("max_units", 25.0),
        fixed_sections=fixed_sections,
        blocked_slots=blocked_slots,
        seat_preferences=seat_pref,
    )


def _serialize_result(result: AlgorithmResult) -> dict[str, Any]:
    """Serialize AlgorithmResult for IPC"""
    return {
        "success": result.success,
        "partial": result.partial,
        "course_count": result.course_count,
        "total_units": result.total_units,
        "execution_time_ms": result.execution_time_ms,
        "iterations": result.iterations,
        "message": result.message,
        "algorithm_name": result.algorithm_name,
        "conflicts": result.conflicts,
        "selected_sections": [
            _serialize_logical_section(s) for s in result.selected_sections
        ],
        "section_ids": [str(sid) for sid in result.section_ids],
    }


def _worker_run_algorithm(
    algorithm_name: str,
    sections_data: dict[str, list[dict]],
    constraints_dict: dict[str, Any],
    total_courses: int,
    stop_event,  # multiprocessing.managers.EventProxy
) -> tuple[str, dict[str, Any] | None]:
    """
    Worker function that runs a single algorithm in a subprocess.

    Must be a top-level function for pickling.
    Returns (algorithm_name, result_dict or None).
    """
    # Check if another process already found perfect solution
    if stop_event.is_set():
        return (algorithm_name, None)

    try:
        # Import here to avoid pickling issues
        from app.core.algorithms.registry import AlgorithmRegistry

        # Reconstruct SectionData objects
        sections_by_course: dict[uuid.UUID, list[SectionData]] = {}
        for course_id_str, sections_list in sections_data.items():
            course_id = uuid.UUID(course_id_str)
            sections_by_course[course_id] = [
                _deserialize_section_data(s) for s in sections_list
            ]

        # Reconstruct constraints
        constraints = _deserialize_constraints(constraints_dict)

        # Get algorithm instance
        algo = AlgorithmRegistry.get(algorithm_name, constraints)

        # Run the algorithm
        result = algo.generate(sections_by_course)

        # Check for perfect solution
        if result.course_count == total_courses and result.success:
            stop_event.set()  # Signal other processes to stop

        # Serialize result for IPC
        return (algorithm_name, _serialize_result(result))

    except Exception as e:
        return (algorithm_name, {"error": str(e), "algorithm_name": algorithm_name})


class ParallelRaceAlgorithm(TimetableAlgorithm):
    """
    Parallel race algorithm - runs multiple algorithms truly in parallel.

    Strategy:
    1. Spawn multiple processes, each running a different algorithm
    2. First process to find a PERFECT solution (all courses, no conflicts) wins
    3. If no perfect solution within timeout, return the best partial solution
    4. Uses multiprocessing for true CPU parallelism (bypasses GIL)

    Benefits over sequential ParallelBestAlgorithm:
    - True parallelism on multi-core CPUs
    - Total time = max(algorithm times), not sum
    - Immediate termination on perfect solution

    Racing algorithms:
    - backtrack_optimized: Thorough search, finds optimal for medium cases
    - random_restart: Escapes local optima, good for hard cases
    - genetic: Diverse exploration, good for complex constraints
    - simulated_annealing: Probabilistic, complements deterministic approaches
    """

    name = "parallel_race"
    description = "Parallel racing - runs algorithms concurrently, first perfect wins"

    # Algorithms to race (curated subset for efficiency)
    RACING_ALGORITHMS = [
        "backtrack_optimized",
        "random_restart",
        "genetic",
        "simulated_annealing",
    ]

    def __init__(
        self,
        constraints: GenerationConstraints | None = None,
        timeout_seconds: float = 30.0,
        max_workers: int | None = None,  # None = number of algorithms (4)
    ):
        super().__init__(constraints)
        self.timeout_seconds = timeout_seconds
        self.max_workers = max_workers or len(self.RACING_ALGORITHMS)

    def generate(
        self,
        sections_by_course: dict[uuid.UUID, list[SectionData]],
    ) -> AlgorithmResult:
        start_time = time_module.perf_counter()

        if not sections_by_course:
            return AlgorithmResult(
                success=False,
                message="No courses provided",
                algorithm_name=self.name,
            )

        total_courses = len(sections_by_course)

        # Serialize data for IPC (convert to dicts for pickling)
        sections_data: dict[str, list[dict]] = {}
        for course_id, sections in sections_by_course.items():
            sections_data[str(course_id)] = [
                _serialize_section_data(s) for s in sections
            ]

        # Serialize constraints
        constraints_dict = _serialize_constraints(self.constraints)

        # Create shared stop event for early termination
        manager = Manager()
        stop_event = manager.Event()

        results: list[tuple[str, dict[str, Any] | None]] = []

        try:
            # Use ProcessPoolExecutor for parallel execution
            with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
                # Submit all algorithms
                futures = {
                    executor.submit(
                        _worker_run_algorithm,
                        algo_name,
                        sections_data,
                        constraints_dict,
                        total_courses,
                        stop_event,
                    ): algo_name
                    for algo_name in self.RACING_ALGORITHMS
                }

                # Collect results with timeout
                try:
                    for future in as_completed(futures, timeout=self.timeout_seconds):
                        algo_name = futures[future]
                        try:
                            result = future.result(timeout=1.0)
                            if result[1] is not None:
                                results.append(result)

                                # Check if this was a perfect solution
                                if (
                                    result[1]
                                    and "error" not in result[1]
                                    and result[1].get("course_count") == total_courses
                                    and result[1].get("success")
                                ):
                                    # Cancel remaining futures
                                    for f in futures:
                                        f.cancel()
                                    break

                        except Exception as e:
                            results.append(
                                (
                                    algo_name,
                                    {"error": str(e), "algorithm_name": algo_name},
                                )
                            )

                except FuturesTimeoutError:
                    # Timeout reached, cancel remaining
                    for f in futures:
                        f.cancel()

        except Exception as e:
            # Handle any manager or executor errors
            return AlgorithmResult(
                success=False,
                message=f"Parallel execution failed: {str(e)}",
                algorithm_name=self.name,
                execution_time_ms=(time_module.perf_counter() - start_time) * 1000,
            )

        finally:
            # Clean up manager
            try:
                manager.shutdown()
            except Exception:
                pass

        execution_time = (time_module.perf_counter() - start_time) * 1000

        # Find best result
        best_result = self._select_best_result(results, total_courses)

        if best_result is None:
            return AlgorithmResult(
                success=False,
                message="All algorithms failed or timed out",
                algorithm_name=self.name,
                execution_time_ms=execution_time,
            )

        # Reconstruct AlgorithmResult from serialized data
        return self._reconstruct_result(
            best_result,
            results,
            execution_time,
            total_courses,
        )

    def _select_best_result(
        self,
        results: list[tuple[str, dict[str, Any] | None]],
        total_courses: int,
    ) -> dict[str, Any] | None:
        """Select the best result based on scoring criteria"""
        valid_results = [
            (name, r)
            for name, r in results
            if r and "error" not in r and r.get("course_count", 0) > 0
        ]

        if not valid_results:
            return None

        def score(item: tuple[str, dict]) -> tuple:
            name, r = item
            return (
                r.get("course_count", 0),
                sum(s.get("seat_score", 0) for s in r.get("selected_sections", [])),
                -r.get("execution_time_ms", float("inf")),
            )

        best_name, best = max(valid_results, key=score)
        best["_winner_algorithm"] = best_name
        return best

    def _reconstruct_result(
        self,
        best: dict[str, Any],
        all_results: list[tuple[str, dict[str, Any] | None]],
        execution_time: float,
        total_courses: int,
    ) -> AlgorithmResult:
        """Reconstruct AlgorithmResult from serialized dict"""
        # Reconstruct LogicalSections
        selected_sections = [
            _deserialize_logical_section(s_dict)
            for s_dict in best.get("selected_sections", [])
        ]

        # Build summary of all algorithm results
        summary_parts = []
        for name, r in all_results:
            if r and "error" not in r:
                summary_parts.append(f"{name}:{r.get('course_count', 0)}")
            elif r and "error" in r:
                summary_parts.append(f"{name}:err")
            else:
                summary_parts.append(f"{name}:timeout")

        winner = best.get("_winner_algorithm", "unknown")

        return AlgorithmResult(
            success=best.get("success", False),
            partial=best.get("partial", False),
            selected_sections=selected_sections,
            section_ids=[uuid.UUID(sid) for sid in best.get("section_ids", [])],
            conflicts=best.get("conflicts", []),
            total_units=best.get("total_units", 0.0),
            course_count=best.get("course_count", 0),
            algorithm_name=self.name,
            execution_time_ms=execution_time,
            iterations=sum(
                r.get("iterations", 0) for _, r in all_results if r and "error" not in r
            ),
            message=f"Parallel race winner: {winner} ({', '.join(summary_parts)})",
        )
