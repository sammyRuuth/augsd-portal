"""
Parser for packages.json - course packages per academic plan.

The packages.json file maps academic plans (branch combinations) to
their required course lists.

Example format:
{
    "2025": {
        "A1, A2, AB": ["MATH F101", "CS F111", ...],
        "A5_PCB": ["BIO F101", "CHEM F101", ...]
    }
}
"""

import json
from pathlib import Path
from typing import Optional


def normalize_plan_name(plan: str) -> str:
    """
    Normalize a plan name by removing extra spaces around commas.

    Args:
        plan: Raw plan name from JSON

    Returns:
        Normalized plan name
    """
    return plan.replace(", ", ",").replace(" ,", ",").strip()


def canonicalize_course_list(courses: list[str]) -> tuple[str, ...]:
    """
    Create a canonical, order-independent representation of a course list.

    Args:
        courses: List of course codes

    Returns:
        Sorted tuple of unique course codes
    """
    cleaned = {c.strip() for c in courses if c and str(c).strip()}
    return tuple(sorted(cleaned))


def parse_packages(file_path: Path) -> dict[str, list[str]]:
    """
    Parse packages.json to get courses for each plan.

    The JSON can have nested year keys or be flat. We handle both:
    - Nested: {"2025": {"plan1": [...], "plan2": [...]}}
    - Flat: {"plan1": [...], "plan2": [...]}

    Args:
        file_path: Path to packages.json file

    Returns:
        Dictionary mapping plan names to course lists
    """
    with open(file_path, "r") as f:
        data = json.load(f)

    packages: dict[str, list[str]] = {}

    # Handle nested structure (with year keys)
    for key, value in data.items():
        if isinstance(value, dict):
            # Nested: key is a year, value contains plans
            for plan_key, courses in value.items():
                if isinstance(courses, list):
                    plan_name = normalize_plan_name(plan_key)
                    packages[plan_name] = courses
        elif isinstance(value, list):
            # Flat: key is a plan name, value is course list
            plan_name = normalize_plan_name(key)
            packages[plan_name] = value

    return packages


def group_plans_by_packages(
    packages: dict[str, list[str]],
    counts: dict[str, int],
) -> tuple[dict[str, list[str]], dict[str, int], dict[str, list[str]]]:
    """
    Combine plans that share identical course packages.

    This optimization reduces work when multiple plans have the same
    course requirements - we can generate one set of timetables and
    share them.

    Args:
        packages: Dictionary of plan -> course list
        counts: Dictionary of plan -> student count

    Returns:
        Tuple of:
        - grouped_packages: Combined plan name -> course list
        - grouped_counts: Combined plan name -> total student count
        - membership: Combined plan name -> list of original plan names
    """
    # Group by canonical course set
    groups: dict[tuple[str, ...], dict] = {}

    for plan, courses in packages.items():
        key = canonicalize_course_list(courses)
        if key not in groups:
            groups[key] = {"courses": courses, "plans": []}
        groups[key]["plans"].append(plan)

    # Build output dictionaries
    grouped_packages: dict[str, list[str]] = {}
    grouped_counts: dict[str, int] = {}
    membership: dict[str, list[str]] = {}

    for key, data in groups.items():
        plans = sorted(data["plans"])
        course_list = data["courses"]

        # Create group name
        if len(plans) == 1:
            group_name = plans[0]
        else:
            group_name = f"COMBINED:{'+'.join(plans)}"

        grouped_packages[group_name] = course_list
        grouped_counts[group_name] = sum(counts.get(p, 0) for p in plans)
        membership[group_name] = plans

    return grouped_packages, grouped_counts, membership


def find_course_match(
    course_code: str,
    available_courses: set[str],
) -> Optional[str]:
    """
    Find a matching course code, handling variations.

    Handles common variations like:
    - 'BITS F101' -> 'BITS F101-1' (suffix variation)
    - Partial prefix matches

    Args:
        course_code: Course code to find
        available_courses: Set of available course codes

    Returns:
        Matched course code or None if not found
    """
    # Direct match
    if course_code in available_courses:
        return course_code

    # Try with '-1' suffix
    with_suffix = f"{course_code}-1"
    if with_suffix in available_courses:
        return with_suffix

    # Try partial prefix match
    for available in available_courses:
        if available.startswith(course_code):
            return available

    return None
