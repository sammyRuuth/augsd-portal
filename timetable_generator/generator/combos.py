"""
Course combination generation.

Generates all valid section combinations for a course, where each
combo has one section per required component (LEC, TUT, LAB, etc.).
"""

from collections import defaultdict
from typing import Optional

from ..config import Config
from ..models import ComponentType, Section
from .constraints import CapacityTracker, sections_clash


def get_balance_score(
    section: Section,
    capacity_tracker: CapacityTracker,
    config: Config,
) -> float:
    """
    Calculate a balance score for section selection.

    Higher scores indicate sections we should prefer (more capacity,
    less overfilled). Lower scores for sections to avoid.

    Args:
        section: Section to score
        capacity_tracker: Current capacity state
        config: Configuration

    Returns:
        Balance score (higher is better)
    """
    remaining = capacity_tracker.get_remaining(section.class_nbr)
    original = capacity_tracker.get_original(section.class_nbr)
    max_overfill = config.get_max_overfill(
        section.course_code,
        section.component.value,
    )

    if config.capacity.allow_negative_capacity:
        # In negative capacity mode, spread load for strict components
        if section.component.is_strict or section.component.is_soft_strict:
            if remaining < -max_overfill:
                # Exceeded max overfill - completely unavailable
                return -999999
            elif remaining < 0:
                # Overfilled but within limit - heavy penalty
                return remaining * 1000
            elif original > 0 and remaining < original * 0.2:
                # Nearly full - moderate penalty
                return remaining - original
            else:
                return remaining
        else:
            # LEC - simple preference for higher capacity
            return remaining
    else:
        # Normal mode - stricter about overfill
        if remaining < 0:
            return remaining * 10  # Very negative score
        elif original > 0 and remaining < original * 0.1:
            return remaining - original  # Penalty for near-full
        else:
            return remaining


def generate_course_combos(
    sections: list[Section],
    capacity_tracker: CapacityTracker,
    config: Config,
    allow_overfill: bool = False,
    avoid_class_nbrs: Optional[set[int]] = None,
) -> list[list[Section]]:
    """
    Generate all valid section combinations for a course.

    Each combination has one section per component type (LEC, TUT, LAB, etc.).
    Combinations are sorted by desirability (balance score).

    Args:
        sections: Available sections for the course
        capacity_tracker: Current capacity state
        config: Configuration
        allow_overfill: Whether to allow TUT overfill
        avoid_class_nbrs: Section class numbers to avoid (for variants)

    Returns:
        List of valid section combinations, sorted by preference
    """
    if not sections:
        return []

    # Filter sections by availability
    available_sections = []
    for s in sections:
        if avoid_class_nbrs and s.class_nbr in avoid_class_nbrs:
            continue
        if capacity_tracker.is_available(s, allow_overfill):
            available_sections.append(s)

    if not available_sections:
        return []

    # Group by component type
    by_component: dict[ComponentType, list[Section]] = defaultdict(list)
    for s in available_sections:
        by_component[s.component].append(s)

    if not by_component:
        return []

    # Sort components by priority (LAB first, then TUT, then LEC)
    components = sorted(by_component.keys(), key=lambda c: c.priority)

    # Sort sections within each component by balance score (descending)
    for comp in components:
        by_component[comp].sort(
            key=lambda s: get_balance_score(s, capacity_tracker, config),
            reverse=True,
        )

    # Generate combinations using DFS
    combos: list[list[Section]] = []

    def dfs(idx: int, acc: list[Section]):
        if idx == len(components):
            combos.append(acc[:])
            return

        comp = components[idx]
        for section in by_component[comp]:
            # Check clash with already selected sections
            clash = False
            for chosen in acc:
                if sections_clash(chosen, section):
                    clash = True
                    break
            if clash:
                continue

            acc.append(section)
            dfs(idx + 1, acc)
            acc.pop()

    dfs(0, [])

    # Sort combos by total balance score
    def combo_score(combo: list[Section]) -> float:
        return sum(get_balance_score(s, capacity_tracker, config) for s in combo)

    combos.sort(key=combo_score, reverse=True)

    return combos


def get_required_components(
    sections: list[Section],
) -> set[ComponentType]:
    """
    Determine which components are required based on available sections.

    Args:
        sections: All sections for a course

    Returns:
        Set of required component types
    """
    return {s.component for s in sections}


def validate_combo_completeness(
    combo: list[Section],
    required_components: set[ComponentType],
) -> bool:
    """
    Check if a combo has all required components.

    Args:
        combo: Section combination to validate
        required_components: Set of required component types

    Returns:
        True if combo has all required components
    """
    combo_components = {s.component for s in combo}
    return required_components.issubset(combo_components)
