"""Hybrid algorithm combining multiple strategies for best results"""

import time as time_module
import uuid

from app.core.algorithms.backtrack_optimized import BacktrackOptimizedAlgorithm
from app.core.algorithms.base import (
    AlgorithmResult,
    GenerationConstraints,
    SectionData,
    TimetableAlgorithm,
)
from app.core.algorithms.genetic import GeneticAlgorithm
from app.core.algorithms.greedy import GreedyAlgorithm
from app.core.algorithms.random_algorithms import RandomRestartAlgorithm


class HybridAlgorithm(TimetableAlgorithm):
    """
    Hybrid algorithm that combines multiple strategies.

    Strategy:
    1. Run greedy for fast initial solution
    2. Run optimized backtrack with time limit
    3. If backtrack didn't find optimal, run random restart
    4. Return best result found

    This approach balances speed and quality - fast for simple cases,
    thorough for complex cases.
    """

    name = "hybrid"
    description = "Hybrid approach - combines greedy, backtrack, and random restart"

    def __init__(
        self,
        constraints: GenerationConstraints | None = None,
        time_limit_ms: float = 2000.0,  # 2 second time limit
    ):
        super().__init__(constraints)
        self.time_limit_ms = time_limit_ms

    def generate(
        self,
        sections_by_course: dict[uuid.UUID, list[SectionData]],
    ) -> AlgorithmResult:
        start_time = time_module.perf_counter()
        total_iterations = 0

        if not sections_by_course:
            return AlgorithmResult(
                success=False,
                message="No courses provided",
                algorithm_name=self.name,
            )

        num_courses = len(sections_by_course)
        best_result: AlgorithmResult | None = None
        methods_tried = []

        # Phase 1: Greedy (always fast)
        greedy = GreedyAlgorithm(self.constraints)
        greedy_result = greedy.generate(sections_by_course)
        total_iterations += greedy_result.iterations
        methods_tried.append(f"greedy({greedy_result.course_count})")

        if greedy_result.success and greedy_result.course_count == num_courses:
            # Perfect solution found quickly
            greedy_result.algorithm_name = self.name
            greedy_result.message = f"Hybrid: greedy found optimal ({greedy_result.execution_time_ms:.1f}ms)"
            return greedy_result

        best_result = greedy_result

        # Check time
        elapsed = (time_module.perf_counter() - start_time) * 1000
        if elapsed > self.time_limit_ms:
            best_result.algorithm_name = self.name
            best_result.iterations = total_iterations
            return best_result

        # Phase 2: Optimized backtrack (with iteration limit based on remaining time)
        remaining_ms = self.time_limit_ms - elapsed
        # Estimate iterations: ~10000 per 100ms for backtrack
        max_iterations = int((remaining_ms / 100) * 5000)

        backtrack = BacktrackOptimizedAlgorithm(
            self.constraints, max_iterations=max_iterations
        )
        backtrack_result = backtrack.generate(sections_by_course)
        total_iterations += backtrack_result.iterations
        methods_tried.append(f"backtrack({backtrack_result.course_count})")

        if backtrack_result.course_count > best_result.course_count:
            best_result = backtrack_result
        elif backtrack_result.course_count == best_result.course_count:
            # Same course count, prefer higher seat score
            best_seat = sum(s.seat_score for s in best_result.selected_sections)
            backtrack_seat = sum(
                s.seat_score for s in backtrack_result.selected_sections
            )
            if backtrack_seat > best_seat:
                best_result = backtrack_result

        if best_result.success and best_result.course_count == num_courses:
            best_result.algorithm_name = self.name
            best_result.iterations = total_iterations
            elapsed = (time_module.perf_counter() - start_time) * 1000
            best_result.execution_time_ms = elapsed
            best_result.message = f"Hybrid: backtrack found optimal ({elapsed:.1f}ms)"
            return best_result

        # Check time
        elapsed = (time_module.perf_counter() - start_time) * 1000
        if elapsed > self.time_limit_ms:
            best_result.algorithm_name = self.name
            best_result.iterations = total_iterations
            best_result.execution_time_ms = elapsed
            return best_result

        # Phase 3: Random restart (for remaining time)
        remaining_ms = self.time_limit_ms - elapsed
        # Estimate: ~5 restarts per 100ms
        num_restarts = max(5, int((remaining_ms / 100) * 5))

        random_restart = RandomRestartAlgorithm(
            self.constraints,
            num_restarts=num_restarts,
            improvements_per_restart=30,
        )
        random_result = random_restart.generate(sections_by_course)
        total_iterations += random_result.iterations
        methods_tried.append(f"random({random_result.course_count})")

        if random_result.course_count > best_result.course_count:
            best_result = random_result
        elif random_result.course_count == best_result.course_count:
            best_seat = sum(s.seat_score for s in best_result.selected_sections)
            random_seat = sum(s.seat_score for s in random_result.selected_sections)
            if random_seat > best_seat:
                best_result = random_result

        # Finalize
        execution_time = (time_module.perf_counter() - start_time) * 1000

        best_result.algorithm_name = self.name
        best_result.iterations = total_iterations
        best_result.execution_time_ms = execution_time
        best_result.message = (
            f"Hybrid: best from {', '.join(methods_tried)} in {execution_time:.1f}ms"
        )

        return best_result


class ParallelBestAlgorithm(TimetableAlgorithm):
    """
    Runs multiple algorithms and returns the best result.

    Unlike compare endpoint, this is a single algorithm that internally
    runs multiple strategies and picks the best.

    Strategies:
    - Greedy (fast baseline)
    - Backtrack optimized (thorough search)
    - Random restart (escape local optima)
    - Genetic (diverse exploration)

    Returns the solution with most courses scheduled, breaking ties by seat score.
    """

    name = "parallel_best"
    description = "Runs all algorithms and returns best result"

    def __init__(
        self,
        constraints: GenerationConstraints | None = None,
    ):
        super().__init__(constraints)

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

        num_courses = len(sections_by_course)
        results: list[tuple[str, AlgorithmResult]] = []
        total_iterations = 0

        # Run all algorithms
        algorithms = [
            ("greedy", GreedyAlgorithm(self.constraints)),
            (
                "backtrack",
                BacktrackOptimizedAlgorithm(self.constraints, max_iterations=30000),
            ),
            ("random", RandomRestartAlgorithm(self.constraints, num_restarts=30)),
            (
                "genetic",
                GeneticAlgorithm(self.constraints, population_size=30, generations=50),
            ),
        ]

        for name, algo in algorithms:
            try:
                result = algo.generate(sections_by_course)
                results.append((name, result))
                total_iterations += result.iterations

                # Early termination if we found perfect solution
                if result.success and result.course_count == num_courses:
                    break
            except Exception:
                # Skip failed algorithms
                pass

        if not results:
            return AlgorithmResult(
                success=False,
                message="All algorithms failed",
                algorithm_name=self.name,
            )

        # Find best result
        def score(r: AlgorithmResult) -> tuple:
            return (
                r.course_count,
                sum(s.seat_score for s in r.selected_sections),
                -r.execution_time_ms,  # Prefer faster for same quality
            )

        best_name, best_result = max(results, key=lambda x: score(x[1]))

        execution_time = (time_module.perf_counter() - start_time) * 1000

        # Build summary
        summary = ", ".join(f"{name}:{r.course_count}" for name, r in results)

        return AlgorithmResult(
            success=best_result.success,
            partial=best_result.partial,
            selected_sections=best_result.selected_sections,
            section_ids=best_result.section_ids,
            conflicts=best_result.conflicts,
            total_units=best_result.total_units,
            course_count=best_result.course_count,
            algorithm_name=self.name,
            execution_time_ms=execution_time,
            iterations=total_iterations,
            message=f"Best: {best_name} ({summary})",
        )
