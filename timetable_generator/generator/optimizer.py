"""
Multi-strategy optimization for timetable generation.

Tests multiple different plan ordering strategies and selects
the best result based on assignment rate and balance metrics.
"""

import random
from dataclasses import dataclass, field
from typing import Optional

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from ..config import Config
from ..models import GenerationResult, Section, TimetableAssignment
from ..parsers.packages import find_course_match
from .allocator import TimetableAllocator
from .constraints import CapacityTracker


@dataclass
class StrategyResult:
    """Result from a single strategy run."""

    name: str
    assignments: dict[str, TimetableAssignment]
    capacity_snapshot: dict[int, int]
    fitness: float
    metrics: dict = field(default_factory=dict)


class TimetableOptimizer:
    """
    Multi-strategy optimizer for timetable generation.

    Tests multiple plan ordering strategies to find the best
    allocation that maximizes assignment rate while balancing
    section utilization.
    """

    def __init__(
        self,
        config: Config,
        packages: dict[str, list[str]],
        counts: dict[str, int],
        sections_by_course: dict[str, list[Section]],
        console: Optional[Console] = None,
    ):
        """
        Initialize optimizer.

        Args:
            config: Configuration object
            packages: Plan -> course list mapping
            counts: Plan -> student count mapping
            sections_by_course: Available sections by course
            console: Rich console for output (optional)
        """
        self.config = config
        self.packages = packages
        self.counts = counts
        self.sections_by_course = sections_by_course
        self.console = console or Console()

    def optimize(self) -> GenerationResult:
        """
        Run multi-strategy optimization.

        Returns:
            Best GenerationResult found
        """
        plans = list(self.packages.keys())
        strategies = self._build_strategies(plans)

        self.console.print()
        self.console.rule("[bold blue]Multi-Strategy Optimization")
        self.console.print(
            f"Testing {len(strategies)} strategies to find optimal allocation...\n"
        )

        best_result: Optional[StrategyResult] = None
        all_results: list[StrategyResult] = []

        # Create progress display
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=self.console,
        ) as progress:
            task = progress.add_task(
                "[cyan]Testing strategies...",
                total=len(strategies),
            )

            for name, plan_order in strategies:
                # Run this strategy
                result = self._run_strategy(name, plan_order)
                all_results.append(result)

                # Update best
                if best_result is None or result.fitness > best_result.fitness:
                    best_result = result

                # Show allocation for this strategy
                assigned = result.metrics.get("total_assigned", 0)
                needed = result.metrics.get("total_needed", 0)
                rate = result.metrics.get("assignment_ratio", 0) * 100

                progress.update(
                    task,
                    advance=1,
                    description=f"[cyan]{name}[/cyan] → {assigned}/{needed} ({rate:.1f}%)",
                )

        # Display results summary
        self._display_results(all_results, best_result)

        # Build final result
        assert best_result is not None

        # Rebuild capacity tracker to get final usage
        capacity_tracker = CapacityTracker(self.sections_by_course, self.config)
        capacity_tracker.restore(best_result.capacity_snapshot)

        capacity_usage = {}
        for class_nbr in capacity_tracker.remaining:
            capacity_usage[class_nbr] = capacity_tracker.get_usage(class_nbr)

        return GenerationResult(
            assignments=best_result.assignments,
            capacity_usage=capacity_usage,
            strategy_used=best_result.name,
            fitness_score=best_result.fitness,
        )

    def _build_strategies(
        self,
        plans: list[str],
    ) -> list[tuple[str, list[str]]]:
        """
        Build list of strategies to test.

        Args:
            plans: List of plan names

        Returns:
            List of (strategy_name, plan_order) tuples
        """
        strategies = []

        # Strategy 1: Original order
        strategies.append(("Original", plans[:]))

        # Strategy 2: Smallest plans first
        strategies.append(
            (
                "Smallest first",
                sorted(plans, key=lambda p: self.counts.get(p, 0)),
            )
        )

        # Strategy 3: Largest plans first
        strategies.append(
            (
                "Largest first",
                sorted(plans, key=lambda p: self.counts.get(p, 0), reverse=True),
            )
        )

        # Strategy 4: Most constrained (most courses) first
        strategies.append(
            (
                "Most courses",
                sorted(
                    plans, key=lambda p: len(self.packages.get(p, [])), reverse=True
                ),
            )
        )

        # Strategy 5: Least constrained first
        strategies.append(
            (
                "Least courses",
                sorted(plans, key=lambda p: len(self.packages.get(p, []))),
            )
        )

        # Strategy 6: Interleaved (alternate small and large)
        small_first = sorted(plans, key=lambda p: self.counts.get(p, 0))
        large_first = sorted(plans, key=lambda p: self.counts.get(p, 0), reverse=True)
        interleaved = []
        for i in range(max(len(small_first), len(large_first))):
            if i < len(small_first) and small_first[i] not in interleaved:
                interleaved.append(small_first[i])
            if i < len(large_first) and large_first[i] not in interleaved:
                interleaved.append(large_first[i])
        strategies.append(("Interleaved", interleaved))

        # Strategy 7: Reverse order
        strategies.append(("Reverse", plans[::-1]))

        # Strategy 8-9: Capacity ratio based
        def capacity_ratio(plan: str) -> float:
            total_cap = self._estimate_plan_capacity(plan)
            need = self.counts.get(plan, 0)
            return total_cap / need if need > 0 else float("inf")

        strategies.append(
            (
                "Tight capacity",
                sorted(plans, key=capacity_ratio),
            )
        )
        strategies.append(
            (
                "Loose capacity",
                sorted(plans, key=capacity_ratio, reverse=True),
            )
        )

        # Strategy 10-11: Bottleneck based
        def bottleneck(plan: str) -> int:
            return self._estimate_bottleneck(plan)

        strategies.append(
            (
                "Bottleneck first",
                sorted(plans, key=bottleneck),
            )
        )
        strategies.append(
            (
                "Bottleneck last",
                sorted(plans, key=bottleneck, reverse=True),
            )
        )

        # Random strategies
        random.seed(42)  # Reproducible
        base_count = len(strategies)
        random_count = max(0, self.config.generator.num_strategies - base_count)

        for i in range(random_count):
            shuffled = plans[:]
            random.shuffle(shuffled)
            strategies.append((f"Random #{i + 1}", shuffled))

        random.seed()  # Reset

        return strategies

    def _estimate_plan_capacity(self, plan: str) -> int:
        """Estimate total LEC capacity for a plan."""
        courses = self.packages.get(plan, [])
        total = 0

        for code in courses:
            matched = find_course_match(code, set(self.sections_by_course.keys()))
            if not matched:
                continue

            sections = self.sections_by_course.get(matched, [])
            lec_cap = max(
                (s.capacity for s in sections if s.component.value == "LEC"),
                default=0,
            )
            total += lec_cap

        return total

    def _estimate_bottleneck(self, plan: str) -> int:
        """Estimate bottleneck (smallest LEC capacity) for a plan."""
        courses = self.packages.get(plan, [])
        bottlenecks = []

        for code in courses:
            matched = find_course_match(code, set(self.sections_by_course.keys()))
            if not matched:
                continue

            sections = self.sections_by_course.get(matched, [])
            lec_cap = max(
                (s.capacity for s in sections if s.component.value == "LEC"),
                default=0,
            )
            if lec_cap > 0:
                bottlenecks.append(lec_cap)

        return min(bottlenecks) if bottlenecks else 0

    def _run_strategy(
        self,
        name: str,
        plan_order: list[str],
    ) -> StrategyResult:
        """
        Run a single strategy.

        Args:
            name: Strategy name
            plan_order: Order to process plans

        Returns:
            StrategyResult with assignments and metrics
        """
        # Create fresh capacity tracker
        capacity_tracker = CapacityTracker(self.sections_by_course, self.config)
        allocator = TimetableAllocator(
            self.config, self.sections_by_course, capacity_tracker
        )

        assignments: dict[str, TimetableAssignment] = {}

        for plan in plan_order:
            if plan not in self.packages:
                continue

            courses = self.packages[plan]
            count = self.counts.get(plan, 0)

            if not courses or count == 0:
                continue

            assignment = allocator.allocate_plan(plan, courses, count)
            assignments[plan] = assignment

        # Calculate fitness
        fitness, metrics = self._calculate_fitness(assignments, capacity_tracker)

        return StrategyResult(
            name=name,
            assignments=assignments,
            capacity_snapshot=capacity_tracker.snapshot(),
            fitness=fitness,
            metrics=metrics,
        )

    def _calculate_fitness(
        self,
        assignments: dict[str, TimetableAssignment],
        capacity_tracker: CapacityTracker,
    ) -> tuple[float, dict]:
        """
        Calculate fitness score for a generation result.

        Higher is better. Prioritizes:
        1. 100% assignment (huge bonus)
        2. Minimize overfilled sections
        3. Balance section utilization

        Args:
            assignments: Generated assignments
            capacity_tracker: Current capacity state

        Returns:
            Tuple of (fitness_score, metrics_dict)
        """
        total_needed = sum(self.counts.values())
        total_assigned = sum(a.students_assigned for a in assignments.values())
        assignment_ratio = total_assigned / total_needed if total_needed > 0 else 0

        # Calculate balance score
        fill_ratios = []
        overfill_penalty = 0.0
        overfilled_count = 0

        for sections in self.sections_by_course.values():
            for s in sections:
                if s.component.value == "LEC":
                    ratio = capacity_tracker.get_fill_ratio(s.class_nbr)
                    if ratio > 0:
                        fill_ratios.append(ratio)
                        if ratio > 1.0:
                            overfill_penalty += (ratio - 1.0) ** 2
                            overfilled_count += 1

        # Calculate variance of fill ratios
        if fill_ratios:
            mean_fill = sum(fill_ratios) / len(fill_ratios)
            variance = sum((r - mean_fill) ** 2 for r in fill_ratios) / len(fill_ratios)
            balance_score = 1.0 / (1.0 + variance)
        else:
            balance_score = 1.0

        # Tiered scoring
        if assignment_ratio >= 1.0:
            assignment_bonus = 1000
        else:
            assignment_bonus = assignment_ratio * 100

        fitness = (
            assignment_bonus
            + balance_score * 10
            - overfilled_count * 200
            - overfill_penalty * 50
        )

        metrics = {
            "total_needed": total_needed,
            "total_assigned": total_assigned,
            "assignment_ratio": assignment_ratio,
            "balance_score": balance_score,
            "overfilled_sections": overfilled_count,
            "overfill_penalty": overfill_penalty,
        }

        return fitness, metrics

    def _display_results(
        self,
        all_results: list[StrategyResult],
        best: StrategyResult,
    ) -> None:
        """Display optimization results."""
        self.console.print()

        # Show top strategies table
        table = Table(title="Strategy Results (Top 10)")
        table.add_column("Strategy", style="cyan")
        table.add_column("Assigned", justify="right")
        table.add_column("Rate", justify="right")
        table.add_column("Overfilled", justify="right")
        table.add_column("Fitness", justify="right")

        # Sort by fitness
        sorted_results = sorted(all_results, key=lambda r: r.fitness, reverse=True)

        for result in sorted_results[:10]:
            rate = result.metrics.get("assignment_ratio", 0) * 100
            assigned = result.metrics.get("total_assigned", 0)
            needed = result.metrics.get("total_needed", 0)
            overfilled = result.metrics.get("overfilled_sections", 0)

            rate_style = "green" if rate >= 100 else "yellow" if rate >= 90 else "red"
            is_best = result.name == best.name

            table.add_row(
                f"[bold]{result.name}[/bold]" if is_best else result.name,
                f"{assigned}/{needed}",
                f"[{rate_style}]{rate:.1f}%[/{rate_style}]",
                str(overfilled),
                f"{result.fitness:.0f}",
            )

        self.console.print(table)
        self.console.print()

        # Show best result summary
        self.console.print(f"[bold green]Best Strategy:[/bold green] {best.name}")
        rate = best.metrics.get("assignment_ratio", 0) * 100
        self.console.print(
            f"  Assigned: {best.metrics.get('total_assigned', 0)}/{best.metrics.get('total_needed', 0)} "
            f"({rate:.1f}%)"
        )
        self.console.print()
