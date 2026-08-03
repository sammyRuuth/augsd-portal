#!/usr/bin/env python3
"""
Comprehensive benchmark suite for timetable generation algorithms.

Tests edge cases including:
- High course counts (10, 20, 30+ courses)
- Many sections per course (10, 20+ sections)
- Dense scheduling (many time conflicts)
- Sparse scheduling (few conflicts)
- Component complexity (LEC + TUT + LAB)
- Capacity constraints (near-full sections)
- Impossible/near-impossible schedules
- Blocked time slots
- Fixed section constraints
"""

import argparse
import gc
import random
import statistics
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date
from datetime import time as dt_time
from pathlib import Path
from typing import Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.algorithms.backtrack import BacktrackAlgorithm
from app.core.algorithms.backtrack_optimized import BacktrackOptimizedAlgorithm
from app.core.algorithms.base import GenerationConstraints, SectionData
from app.core.algorithms.genetic import GeneticAlgorithm
from app.core.algorithms.greedy import GreedyAlgorithm
from app.core.algorithms.hybrid import HybridAlgorithm, ParallelBestAlgorithm
from app.core.algorithms.random_algorithms import (
    RandomAlgorithm,
    RandomRestartAlgorithm,
    SimulatedAnnealingAlgorithm,
)


@dataclass
class BenchmarkConfig:
    """Configuration for a benchmark test case"""

    name: str
    num_courses: int
    sections_per_component: int
    components_per_course: list[str]
    time_slot_density: float  # 0.0 = sparse, 1.0 = dense (many conflicts)
    capacity_fill_rate: float  # 0.0 = empty, 1.0 = full
    description: str = ""
    # Additional constraints
    blocked_slots: list[tuple[str, dt_time, dt_time]] = field(default_factory=list)
    max_units: float = 25.0
    fixed_sections: dict = field(
        default_factory=dict
    )  # course_idx -> {component -> section_idx}
    # Control exam conflicts
    exam_conflict_rate: float = 0.0  # 0.0 = no conflicts, 1.0 = all on same day
    # Variable time slots per day
    time_distribution: str = (
        "uniform"  # "uniform", "peak_hours", "morning", "afternoon"
    )
    # Whether this test is expected to be solvable
    expected_solvable: bool = True


@dataclass
class BenchmarkResult:
    """Result from a single benchmark run"""

    algorithm: str
    config_name: str
    success: bool
    partial: bool
    courses_scheduled: int
    total_courses: int
    execution_time_ms: float
    iterations: int
    conflicts_count: int
    total_units: float
    # Quality metrics
    schedule_rate: float  # courses_scheduled / total_courses
    seat_score: int = 0


@dataclass
class AggregatedResult:
    """Aggregated results from multiple runs"""

    algorithm: str
    config_name: str
    avg_scheduled: float
    std_scheduled: float
    avg_time_ms: float
    std_time_ms: float
    min_time_ms: float
    max_time_ms: float
    avg_schedule_rate: float
    success_rate: float
    num_runs: int


def get_time_slots(distribution: str, density: float) -> tuple[list[str], list[int]]:
    """Get time slots based on distribution and density"""
    all_days = ["M", "T", "W", "Th", "F"]

    if distribution == "peak_hours":
        hours = [9, 10, 11, 14, 15, 16]  # Common class times
    elif distribution == "morning":
        hours = [8, 9, 10, 11]
    elif distribution == "afternoon":
        hours = [13, 14, 15, 16, 17]
    else:  # uniform
        hours = list(range(8, 20))  # 8am to 8pm

    # Adjust based on density
    if density >= 0.9:
        # Very dense: limit to 3 time slots on 3 days
        return ["M", "W", "F"][:3], hours[:3]
    elif density >= 0.7:
        return all_days[:4], hours[:5]
    elif density >= 0.5:
        return all_days[:4], hours[:8]
    elif density >= 0.3:
        return all_days, hours[:10]
    else:
        return all_days, hours


def generate_test_data(
    config: BenchmarkConfig, seed: int = 42
) -> tuple[dict[uuid.UUID, list[SectionData]], GenerationConstraints]:
    """Generate test data based on configuration"""
    random.seed(seed)

    sections_by_course: dict[uuid.UUID, list[SectionData]] = {}
    course_ids: list[uuid.UUID] = []

    # Get time slots based on distribution
    all_days, all_hours = get_time_slots(
        config.time_distribution, config.time_slot_density
    )

    # Determine exam dates based on conflict rate
    base_exam_date = date(2025, 12, 10)
    if config.exam_conflict_rate >= 0.9:
        # All same day - guaranteed conflicts
        exam_dates = [base_exam_date]
    elif config.exam_conflict_rate >= 0.5:
        # Few days - likely conflicts
        exam_dates = [date(2025, 12, d) for d in [10, 11, 12]]
    else:
        # Many days - few conflicts
        exam_dates = [date(2025, 12, d) for d in range(10, 25)]

    for i in range(config.num_courses):
        course_id = uuid.uuid4()
        course_ids.append(course_id)
        sections = []

        # Determine components for this course (vary based on course index)
        num_components = (i % len(config.components_per_course)) + 1
        components = config.components_per_course[:num_components]

        # Units based on components
        if "LAB" in components:
            course_units = 4.0
        elif "TUT" in components:
            course_units = 3.0
        else:
            course_units = 3.0

        # Exam date for this course
        exam_date = random.choice(exam_dates)
        exam_start = dt_time(9, 0)
        exam_end = dt_time(12, 0)

        for comp in components:
            for j in range(config.sections_per_component):
                # Generate meeting times - potentially multiple per section
                num_meetings = 2 if comp == "LEC" else 1

                # Try to create varied meeting patterns
                days_for_section = []
                hours_for_section = []

                for _ in range(num_meetings):
                    day = random.choice(all_days)
                    start_hour = random.choice(all_hours)
                    days_for_section.append(day)
                    hours_for_section.append(start_hour)

                # Duration based on component
                duration = (
                    1 if comp in ("TUT", "LAB") else (1 if random.random() < 0.5 else 2)
                )

                for day, start_hour in zip(days_for_section, hours_for_section):
                    # Capacity based on fill rate
                    cap = 40
                    if config.capacity_fill_rate >= 0.95:
                        tot = random.randint(39, 40)  # Nearly/fully full
                    elif config.capacity_fill_rate >= 0.7:
                        tot = random.randint(30, 38)
                    elif config.capacity_fill_rate >= 0.5:
                        tot = random.randint(20, 35)
                    else:
                        tot = random.randint(5, 20)

                    section = SectionData(
                        id=uuid.uuid4(),
                        course_id=course_id,
                        class_nbr=i * 1000 + j * 10 + len(sections) + 1,
                        section=f"{comp[0]}{j + 1}",
                        component=comp,
                        day=day,
                        mtg_start=dt_time(start_hour, 0),
                        mtg_end=dt_time(min(start_hour + duration, 23), 0),
                        exam_date=exam_date,
                        exam_start=exam_start,
                        exam_end=exam_end,
                        instructor=f"Prof {i}",
                        room=f"Room {j}",
                        cap_enrl=cap,
                        tot_enrl=tot,
                        subject=f"SUBJ{i // 10}",
                        catalog=f"{100 + i}",
                        title=f"Course {i}",
                        max_units=course_units
                        if comp == "LEC"
                        else (1.5 if comp == "TUT" else 1.0),
                    )
                    sections.append(section)

        sections_by_course[course_id] = sections

    # Build constraints
    constraints = GenerationConstraints(
        max_units=config.max_units,
        blocked_slots=config.blocked_slots,
    )

    # Apply fixed sections if specified
    for course_idx, fixed in config.fixed_sections.items():
        if course_idx < len(course_ids):
            cid = course_ids[course_idx]
            constraints.fixed_sections[cid] = {
                comp: course_idx * 1000 + sec_idx * 10 + 1
                for comp, sec_idx in fixed.items()
            }

    return sections_by_course, constraints


def create_algorithm(
    name: str, constraints: GenerationConstraints, config: BenchmarkConfig
) -> Any:
    """Create algorithm instance with appropriate parameters"""
    # Scale parameters based on problem size
    num_courses = config.num_courses
    sections_per = config.sections_per_component
    complexity = num_courses * sections_per * len(config.components_per_course)

    if name == "Greedy":
        return GreedyAlgorithm(constraints)
    elif name == "Backtrack":
        # Scale iterations with complexity
        max_iter = min(200000, max(50000, complexity * 100))
        return BacktrackAlgorithm(constraints, max_iterations=max_iter)
    elif name == "Backtrack Opt":
        max_iter = min(200000, max(50000, complexity * 100))
        return BacktrackOptimizedAlgorithm(constraints, max_iterations=max_iter)
    elif name == "Genetic":
        # Scale population and generations with complexity
        pop_size = min(100, max(30, complexity // 10))
        generations = min(200, max(50, complexity // 5))
        return GeneticAlgorithm(
            constraints, population_size=pop_size, generations=generations
        )
    elif name == "Random":
        samples = min(500, max(100, complexity))
        return RandomAlgorithm(constraints, num_samples=samples)
    elif name == "Random Restart":
        restarts = min(50, max(20, complexity // 20))
        return RandomRestartAlgorithm(
            constraints, num_restarts=restarts, improvements_per_restart=50
        )
    elif name == "Simulated Annealing":
        iters = min(50, max(20, complexity // 20))
        return SimulatedAnnealingAlgorithm(constraints, iterations_per_temp=iters)
    elif name == "Hybrid":
        time_limit = min(5000.0, max(1000.0, complexity * 2.0))
        return HybridAlgorithm(constraints, time_limit_ms=time_limit)
    elif name == "Parallel Best":
        return ParallelBestAlgorithm(constraints)
    else:
        raise ValueError(f"Unknown algorithm: {name}")


def run_single_benchmark(
    config: BenchmarkConfig,
    algorithm_name: str,
    seed: int,
    constraints_override: GenerationConstraints | None = None,
) -> BenchmarkResult:
    """Run a single benchmark iteration"""
    data, constraints = generate_test_data(config, seed=seed)
    if constraints_override:
        constraints = constraints_override

    total_courses = len(data)

    algo = create_algorithm(algorithm_name, constraints, config)

    # Force garbage collection before timing
    gc.collect()

    # Run algorithm
    result = algo.generate(data)

    # Calculate seat score
    seat_score = sum(s.seat_score for s in result.selected_sections)

    schedule_rate = result.course_count / total_courses if total_courses > 0 else 0.0

    return BenchmarkResult(
        algorithm=algorithm_name,
        config_name=config.name,
        success=result.success,
        partial=result.partial,
        courses_scheduled=result.course_count,
        total_courses=total_courses,
        execution_time_ms=result.execution_time_ms,
        iterations=result.iterations,
        conflicts_count=len(result.conflicts),
        total_units=result.total_units,
        schedule_rate=schedule_rate,
        seat_score=seat_score,
    )


def run_benchmark(
    config: BenchmarkConfig,
    algorithms: list[str],
    num_runs: int = 5,
    verbose: bool = False,
) -> list[AggregatedResult]:
    """Run benchmark for all algorithms with multiple iterations"""
    results: list[AggregatedResult] = []

    for algo_name in algorithms:
        run_results: list[BenchmarkResult] = []

        for run in range(num_runs):
            try:
                result = run_single_benchmark(config, algo_name, seed=42 + run)
                run_results.append(result)

                if verbose:
                    status = (
                        "OK"
                        if result.success
                        else ("partial" if result.partial else "FAIL")
                    )
                    print(
                        f"    {algo_name} run {run + 1}: {result.courses_scheduled}/{result.total_courses} "
                        f"({result.execution_time_ms:.2f}ms) [{status}]"
                    )
            except Exception as e:
                if verbose:
                    print(f"    {algo_name} run {run + 1}: ERROR - {e}")
                continue

        if not run_results:
            continue

        # Aggregate results
        times = [r.execution_time_ms for r in run_results]
        scheduled = [r.courses_scheduled for r in run_results]
        rates = [r.schedule_rate for r in run_results]

        results.append(
            AggregatedResult(
                algorithm=algo_name,
                config_name=config.name,
                avg_scheduled=statistics.mean(scheduled),
                std_scheduled=statistics.stdev(scheduled)
                if len(scheduled) > 1
                else 0.0,
                avg_time_ms=statistics.mean(times),
                std_time_ms=statistics.stdev(times) if len(times) > 1 else 0.0,
                min_time_ms=min(times),
                max_time_ms=max(times),
                avg_schedule_rate=statistics.mean(rates),
                success_rate=sum(1 for r in run_results if r.success)
                / len(run_results),
                num_runs=len(run_results),
            )
        )

    return results


def get_benchmark_configs() -> list[BenchmarkConfig]:
    """Get all benchmark configurations"""
    return [
        # === Basic cases ===
        BenchmarkConfig(
            name="tiny_trivial",
            num_courses=3,
            sections_per_component=2,
            components_per_course=["LEC"],
            time_slot_density=0.1,
            capacity_fill_rate=0.2,
            description="3 courses, LEC only - trivial case",
        ),
        BenchmarkConfig(
            name="small_simple",
            num_courses=5,
            sections_per_component=3,
            components_per_course=["LEC"],
            time_slot_density=0.3,
            capacity_fill_rate=0.3,
            description="5 courses, LEC only, sparse scheduling",
        ),
        BenchmarkConfig(
            name="medium_standard",
            num_courses=8,
            sections_per_component=4,
            components_per_course=["LEC", "TUT"],
            time_slot_density=0.5,
            capacity_fill_rate=0.5,
            description="8 courses, LEC+TUT, medium density",
        ),
        # === Scale tests ===
        BenchmarkConfig(
            name="many_courses_sparse",
            num_courses=15,
            sections_per_component=3,
            components_per_course=["LEC"],
            time_slot_density=0.2,
            capacity_fill_rate=0.3,
            description="15 courses, sparse - tests course scalability",
        ),
        BenchmarkConfig(
            name="many_courses_dense",
            num_courses=15,
            sections_per_component=3,
            components_per_course=["LEC", "TUT"],
            time_slot_density=0.7,
            capacity_fill_rate=0.5,
            description="15 courses, dense - hard scalability test",
        ),
        BenchmarkConfig(
            name="large_scale",
            num_courses=25,
            sections_per_component=4,
            components_per_course=["LEC"],
            time_slot_density=0.3,
            capacity_fill_rate=0.4,
            description="25 courses - large scale test",
        ),
        # === Combinatorial explosion ===
        BenchmarkConfig(
            name="many_sections",
            num_courses=6,
            sections_per_component=12,
            components_per_course=["LEC", "TUT"],
            time_slot_density=0.3,
            capacity_fill_rate=0.3,
            description="6 courses, 12 sections each - combo explosion",
        ),
        BenchmarkConfig(
            name="full_components",
            num_courses=6,
            sections_per_component=5,
            components_per_course=["LEC", "TUT", "LAB"],
            time_slot_density=0.5,
            capacity_fill_rate=0.5,
            description="6 courses with LEC+TUT+LAB - 3-component combos",
        ),
        BenchmarkConfig(
            name="extreme_sections",
            num_courses=4,
            sections_per_component=20,
            components_per_course=["LEC", "TUT"],
            time_slot_density=0.4,
            capacity_fill_rate=0.4,
            description="4 courses, 20 sections each - extreme combo count",
        ),
        # === Constraint tests ===
        BenchmarkConfig(
            name="dense_schedule",
            num_courses=8,
            sections_per_component=4,
            components_per_course=["LEC", "TUT"],
            time_slot_density=0.9,
            capacity_fill_rate=0.5,
            time_distribution="peak_hours",
            description="8 courses, peak hours only - very tight",
        ),
        BenchmarkConfig(
            name="capacity_constrained",
            num_courses=8,
            sections_per_component=4,
            components_per_course=["LEC", "TUT"],
            time_slot_density=0.5,
            capacity_fill_rate=0.98,
            description="8 courses, nearly full sections - capacity test",
        ),
        BenchmarkConfig(
            name="unit_limited",
            num_courses=12,
            sections_per_component=4,
            components_per_course=["LEC", "TUT"],
            time_slot_density=0.4,
            capacity_fill_rate=0.4,
            max_units=15.0,  # Can only fit ~5 courses
            description="12 courses but only 15 units allowed",
        ),
        BenchmarkConfig(
            name="blocked_morning",
            num_courses=8,
            sections_per_component=5,
            components_per_course=["LEC", "TUT"],
            time_slot_density=0.5,
            capacity_fill_rate=0.4,
            blocked_slots=[
                ("M", dt_time(8, 0), dt_time(12, 0)),
                ("T", dt_time(8, 0), dt_time(12, 0)),
                ("W", dt_time(8, 0), dt_time(12, 0)),
                ("Th", dt_time(8, 0), dt_time(12, 0)),
                ("F", dt_time(8, 0), dt_time(12, 0)),
            ],
            time_distribution="uniform",
            description="8 courses, mornings blocked - afternoon only",
        ),
        BenchmarkConfig(
            name="exam_conflicts",
            num_courses=10,
            sections_per_component=4,
            components_per_course=["LEC"],
            time_slot_density=0.4,
            capacity_fill_rate=0.4,
            exam_conflict_rate=0.8,
            description="10 courses with high exam conflict rate",
        ),
        # === Near-impossible / Stress tests ===
        BenchmarkConfig(
            name="near_impossible",
            num_courses=12,
            sections_per_component=3,
            components_per_course=["LEC", "TUT", "LAB"],
            time_slot_density=0.95,
            capacity_fill_rate=0.7,
            time_distribution="peak_hours",
            expected_solvable=False,
            description="12 courses, 3 components, very dense - likely partial",
        ),
        BenchmarkConfig(
            name="stress_medium",
            num_courses=20,
            sections_per_component=6,
            components_per_course=["LEC", "TUT"],
            time_slot_density=0.6,
            capacity_fill_rate=0.5,
            description="20 courses, 6 sections each - medium stress",
        ),
        BenchmarkConfig(
            name="stress_heavy",
            num_courses=30,
            sections_per_component=5,
            components_per_course=["LEC", "TUT"],
            time_slot_density=0.5,
            capacity_fill_rate=0.5,
            description="30 courses - heavy stress test",
        ),
        # === Realistic university scenarios ===
        BenchmarkConfig(
            name="freshman_typical",
            num_courses=5,
            sections_per_component=8,
            components_per_course=["LEC", "TUT"],
            time_slot_density=0.4,
            capacity_fill_rate=0.7,
            max_units=20.0,
            description="Typical freshman: 5 courses, high enrollment",
        ),
        BenchmarkConfig(
            name="senior_overload",
            num_courses=8,
            sections_per_component=4,
            components_per_course=["LEC", "LAB"],
            time_slot_density=0.5,
            capacity_fill_rate=0.3,
            max_units=28.0,
            description="Senior overload: 8 courses with labs",
        ),
        BenchmarkConfig(
            name="grad_seminar",
            num_courses=4,
            sections_per_component=2,
            components_per_course=["LEC"],
            time_slot_density=0.7,
            capacity_fill_rate=0.5,
            time_distribution="afternoon",
            description="Graduate: 4 courses, limited sections, afternoons",
        ),
    ]


def print_results_table(results: list[AggregatedResult], config: BenchmarkConfig):
    """Print results in a formatted table"""
    print(
        f"\n{'Algorithm':<20} {'Scheduled':>12} {'Time (ms)':>14} {'Rate':>8} {'Success':>8}"
    )
    print("-" * 66)

    # Sort by schedule rate, then by time
    sorted_results = sorted(
        results, key=lambda r: (-r.avg_schedule_rate, r.avg_time_ms)
    )

    for r in sorted_results:
        scheduled_str = f"{r.avg_scheduled:.1f}/{config.num_courses}"
        time_str = f"{r.avg_time_ms:.1f} +/- {r.std_time_ms:.1f}"
        rate_str = f"{r.avg_schedule_rate * 100:.1f}%"
        success_str = f"{r.success_rate * 100:.0f}%"
        print(
            f"{r.algorithm:<20} {scheduled_str:>12} {time_str:>14} {rate_str:>8} {success_str:>8}"
        )


def print_summary(all_results: list[AggregatedResult], algorithms: list[str]):
    """Print summary statistics across all benchmarks"""
    print("\n" + "=" * 80)
    print("SUMMARY BY ALGORITHM")
    print("=" * 80)

    for algo in algorithms:
        algo_results = [r for r in all_results if r.algorithm == algo]
        if not algo_results:
            continue

        avg_time = statistics.mean(r.avg_time_ms for r in algo_results)
        avg_rate = statistics.mean(r.avg_schedule_rate for r in algo_results)
        success_count = sum(1 for r in algo_results if r.success_rate > 0.5)

        print(
            f"{algo:<20} "
            f"Avg time: {avg_time:>8.1f}ms | "
            f"Avg schedule: {avg_rate * 100:>5.1f}% | "
            f"Good results: {success_count}/{len(algo_results)}"
        )

    # Find best algorithm for different metrics
    print("\n" + "-" * 80)
    print("BEST PERFORMERS:")

    # Fastest on average
    by_speed = {}
    by_quality = {}
    for algo in algorithms:
        algo_results = [r for r in all_results if r.algorithm == algo]
        if algo_results:
            by_speed[algo] = statistics.mean(r.avg_time_ms for r in algo_results)
            by_quality[algo] = statistics.mean(
                r.avg_schedule_rate for r in algo_results
            )

    if by_speed:
        fastest = min(by_speed.items(), key=lambda x: x[1])
        print(f"  Fastest: {fastest[0]} ({fastest[1]:.1f}ms avg)")

    if by_quality:
        best = max(by_quality.items(), key=lambda x: x[1])
        print(f"  Best schedule rate: {best[0]} ({best[1] * 100:.1f}% avg)")


def main():
    """Run full benchmark suite"""
    parser = argparse.ArgumentParser(description="Timetable Algorithm Benchmark Suite")
    parser.add_argument("--runs", type=int, default=3, help="Number of runs per config")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument(
        "--quick", action="store_true", help="Run quick subset of tests"
    )
    parser.add_argument("--stress", action="store_true", help="Include stress tests")
    parser.add_argument("--config", type=str, help="Run specific config by name")
    parser.add_argument("--algo", type=str, help="Run specific algorithm only")
    args = parser.parse_args()

    print("=" * 80)
    print("TIMETABLE ALGORITHM BENCHMARK SUITE")
    print("=" * 80)

    # All algorithms to test
    all_algorithms = [
        "Greedy",
        "Backtrack",
        "Backtrack Opt",
        "Genetic",
        "Random",
        "Random Restart",
        "Simulated Annealing",
        "Hybrid",
        "Parallel Best",
    ]

    # Filter algorithms if specified
    if args.algo:
        algorithms = [a for a in all_algorithms if args.algo.lower() in a.lower()]
        if not algorithms:
            print(f"No algorithm matching '{args.algo}' found")
            print(f"Available: {', '.join(all_algorithms)}")
            return
    else:
        algorithms = all_algorithms

    # Get test configurations
    configs = get_benchmark_configs()

    # Filter configs based on args
    if args.config:
        configs = [c for c in configs if args.config.lower() in c.name.lower()]
        if not configs:
            print(f"No config matching '{args.config}' found")
            return
    elif args.quick:
        # Quick mode: only basic and realistic tests
        quick_names = [
            "tiny_trivial",
            "small_simple",
            "medium_standard",
            "freshman_typical",
            "many_sections",
        ]
        configs = [c for c in configs if c.name in quick_names]
    elif not args.stress:
        # Normal mode: exclude heavy stress tests
        configs = [c for c in configs if "stress_heavy" not in c.name]

    print(f"\nRunning {len(configs)} configurations with {len(algorithms)} algorithms")
    print(f"Runs per config: {args.runs}")
    print(f"Algorithms: {', '.join(algorithms)}")

    # Run benchmarks
    all_results: list[AggregatedResult] = []

    for config in configs:
        print(f"\n{'=' * 80}")
        print(f"Test: {config.name}")
        print(f"  {config.description}")
        print(
            f"  Courses: {config.num_courses}, Sections/component: {config.sections_per_component}"
        )
        print(f"  Components: {config.components_per_course}")
        print(
            f"  Density: {config.time_slot_density}, Capacity: {config.capacity_fill_rate}"
        )
        if config.blocked_slots:
            print(f"  Blocked slots: {len(config.blocked_slots)}")
        if config.max_units != 25.0:
            print(f"  Max units: {config.max_units}")
        print(f"{'=' * 80}")

        results = run_benchmark(
            config, algorithms, num_runs=args.runs, verbose=args.verbose
        )
        all_results.extend(results)

        print_results_table(results, config)

    # Summary
    print_summary(all_results, algorithms)

    print("\n" + "=" * 80)
    print("Benchmark complete!")


if __name__ == "__main__":
    main()
