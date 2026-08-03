"""Genetic algorithm for timetable generation"""

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


class GeneticAlgorithm(TimetableAlgorithm):
    """
    Genetic algorithm for timetable generation.

    Strategy:
    1. Generate initial population of random valid timetables
    2. Evaluate fitness (courses scheduled, seat availability, etc.)
    3. Selection, crossover, mutation
    4. Repeat until convergence or max generations

    Pros: Can explore diverse solutions, good for complex constraints
    Cons: May not find optimal, results vary between runs
    """

    name = "genetic"
    description = "Genetic algorithm - explores diverse solutions"

    def __init__(
        self,
        constraints: GenerationConstraints | None = None,
        population_size: int = 50,
        generations: int = 100,
        mutation_rate: float = 0.1,
        elite_size: int = 5,
    ):
        super().__init__(constraints)
        self.population_size = population_size
        self.generations = generations
        self.mutation_rate = mutation_rate
        self.elite_size = elite_size

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
                message="No valid course combinations found",
                algorithm_name=self.name,
            )

        course_ids = list(course_combos.keys())

        # Generate initial population
        population = self._init_population(course_ids, course_combos, course_meta)

        # Evolution loop
        best_individual = None
        best_fitness = -1

        for gen in range(self.generations):
            self._iterations += 1

            # Evaluate fitness
            fitness_scores = [
                self._evaluate_fitness(ind, course_combos, course_meta)
                for ind in population
            ]

            # Track best
            for i, score in enumerate(fitness_scores):
                if score > best_fitness:
                    best_fitness = score
                    best_individual = population[i].copy()

            # Early termination if perfect solution found
            if best_fitness >= len(course_ids) * 1000:  # All courses scheduled
                break

            # Selection
            selected = self._selection(population, fitness_scores)

            # Create new population
            new_population = []

            # Elitism: keep best individuals
            sorted_pop = sorted(zip(fitness_scores, population), key=lambda x: -x[0])
            for _, ind in sorted_pop[: self.elite_size]:
                new_population.append(ind.copy())

            # Crossover and mutation
            while len(new_population) < self.population_size:
                parent1 = random.choice(selected)
                parent2 = random.choice(selected)
                child = self._crossover(parent1, parent2, course_ids)
                child = self._mutate(child, course_ids, course_combos)
                new_population.append(child)

            population = new_population

        # Decode best individual
        if best_individual is None:
            return AlgorithmResult(
                success=False,
                message="Genetic algorithm failed to find solution",
                algorithm_name=self.name,
            )

        result = self._decode_individual(best_individual, course_combos, course_meta)
        execution_time = (time_module.perf_counter() - start_time) * 1000

        return AlgorithmResult(
            success=len(result["conflicts"]) == 0 and len(result["sections"]) > 0,
            partial=len(result["conflicts"]) > 0 and len(result["sections"]) > 0,
            selected_sections=result["sections"],
            section_ids=self.get_section_ids(result["sections"]),
            conflicts=result["conflicts"],
            total_units=result["units"],
            course_count=len(result["courses"]),
            algorithm_name=self.name,
            execution_time_ms=execution_time,
            iterations=self._iterations,
            message=self._build_message(result["courses"], result["conflicts"]),
        )

    def _init_population(
        self,
        course_ids: list[uuid.UUID],
        course_combos: dict[uuid.UUID, list[list[LogicalSection]]],
        course_meta: dict[uuid.UUID, dict[str, Any]],
    ) -> list[dict[uuid.UUID, int]]:
        """Generate initial population (chromosome = dict of course -> combo index)"""
        population = []

        for _ in range(self.population_size):
            individual: dict[uuid.UUID, int] = {}

            for cid in course_ids:
                combos = course_combos[cid]
                if combos:
                    # Random combo index, or -1 to skip course
                    if random.random() < 0.9:  # 90% chance to include
                        individual[cid] = random.randint(0, len(combos) - 1)
                    else:
                        individual[cid] = -1
                else:
                    individual[cid] = -1

            population.append(individual)

        return population

    def _evaluate_fitness(
        self,
        individual: dict[uuid.UUID, int],
        course_combos: dict[uuid.UUID, list[list[LogicalSection]]],
        course_meta: dict[uuid.UUID, dict[str, Any]],
    ) -> float:
        """Evaluate fitness of an individual - optimized"""
        masks: dict[str, int] = {}
        exams: dict[uuid.UUID, tuple] = {}
        units = 0.0
        scheduled = 0
        seat_score = 0
        conflicts = 0

        # Sort by combo count (most constrained first) for better scheduling
        items = sorted(
            individual.items(), key=lambda x: len(course_combos.get(x[0], []))
        )

        for cid, combo_idx in items:
            if combo_idx < 0:
                continue

            combos = course_combos.get(cid, [])
            if combo_idx >= len(combos):
                continue

            combo = combos[combo_idx]
            meta = course_meta.get(cid, {})
            course_units = meta.get("units", 0)

            # Check unit limit
            if units + course_units > self.constraints.max_units:
                conflicts += 1
                continue

            # Check if combo fits using batch method
            if self.combo_clashes_with_masks(combo, masks, exams):
                conflicts += 1
                continue

            # Add to schedule
            self.merge_combo_to_schedule(combo, masks, exams)
            for section in combo:
                seat_score += section.seat_score
            units += course_units
            scheduled += 1

        # Fitness: prioritize # courses, then seat availability, penalize conflicts
        return scheduled * 1000 + seat_score - conflicts * 100

    def _selection(
        self,
        population: list[dict[uuid.UUID, int]],
        fitness_scores: list[float],
    ) -> list[dict[uuid.UUID, int]]:
        """Tournament selection"""
        selected = []
        tournament_size = 3

        for _ in range(self.population_size):
            tournament = random.sample(
                list(zip(fitness_scores, population)), tournament_size
            )
            winner = max(tournament, key=lambda x: x[0])
            selected.append(winner[1].copy())

        return selected

    def _crossover(
        self,
        parent1: dict[uuid.UUID, int],
        parent2: dict[uuid.UUID, int],
        course_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, int]:
        """Uniform crossover"""
        child = {}
        for cid in course_ids:
            if random.random() < 0.5:
                child[cid] = parent1.get(cid, -1)
            else:
                child[cid] = parent2.get(cid, -1)
        return child

    def _mutate(
        self,
        individual: dict[uuid.UUID, int],
        course_ids: list[uuid.UUID],
        course_combos: dict[uuid.UUID, list[list[LogicalSection]]],
    ) -> dict[uuid.UUID, int]:
        """Mutation: randomly change combo for some courses"""
        for cid in course_ids:
            if random.random() < self.mutation_rate:
                combos = course_combos.get(cid, [])
                if combos:
                    if random.random() < 0.1:  # Small chance to skip
                        individual[cid] = -1
                    else:
                        individual[cid] = random.randint(0, len(combos) - 1)

        return individual

    def _decode_individual(
        self,
        individual: dict[uuid.UUID, int],
        course_combos: dict[uuid.UUID, list[list[LogicalSection]]],
        course_meta: dict[uuid.UUID, dict[str, Any]],
    ) -> dict[str, Any]:
        """Decode individual into actual schedule - optimized"""
        masks: dict[str, int] = {}
        exams: dict[uuid.UUID, tuple] = {}
        units = 0.0
        sections: list[LogicalSection] = []
        scheduled: set[uuid.UUID] = set()
        conflicts: list[dict[str, Any]] = []

        # Sort by number of combos (most constrained first)
        sorted_items = sorted(
            individual.items(), key=lambda x: len(course_combos.get(x[0], []))
        )

        for cid, combo_idx in sorted_items:
            meta = course_meta.get(cid, {})

            if combo_idx < 0:
                conflicts.append(
                    {
                        "type": "skipped",
                        "message": f"Skipped {meta.get('subject', '')} {meta.get('catalog', '')}",
                        "course_id": str(cid),
                    }
                )
                continue

            combos = course_combos.get(cid, [])
            if combo_idx >= len(combos):
                continue

            combo = combos[combo_idx]
            course_units = meta.get("units", 0)

            # Check unit limit
            if units + course_units > self.constraints.max_units:
                conflicts.append(
                    {
                        "type": "unit_limit",
                        "message": f"Unit limit exceeded for {meta.get('subject', '')} {meta.get('catalog', '')}",
                        "course_id": str(cid),
                    }
                )
                continue

            # Check if combo fits using batch method
            if self.combo_clashes_with_masks(combo, masks, exams):
                conflicts.append(
                    {
                        "type": "time_conflict",
                        "message": f"Time conflict for {meta.get('subject', '')} {meta.get('catalog', '')}",
                        "course_id": str(cid),
                    }
                )
                continue

            # Add to schedule using batch method
            self.merge_combo_to_schedule(combo, masks, exams)
            sections.extend(combo)
            units += course_units
            scheduled.add(cid)

        return {
            "sections": sections,
            "units": units,
            "courses": scheduled,
            "conflicts": conflicts,
        }

    def _build_message(self, scheduled: set[uuid.UUID], conflicts: list[dict]) -> str:
        if not conflicts:
            return f"Successfully scheduled {len(scheduled)} course(s)"
        elif scheduled:
            return f"Partial schedule: {len(scheduled)} course(s) scheduled, {len(conflicts)} could not fit"
        else:
            return "Could not schedule any courses"
