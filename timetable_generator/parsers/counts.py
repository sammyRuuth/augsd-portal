"""
Parser for count.csv - student counts per academic plan.

The count.csv file maps academic plans to their student counts.

Example format:
Plan,Count
"A1,A2,AB",523
"A5_PCB",25
"""

import csv
from pathlib import Path


def normalize_plan_name(plan: str) -> str:
    """
    Normalize a plan name by removing extra spaces and quotes.

    Args:
        plan: Raw plan name from CSV

    Returns:
        Normalized plan name
    """
    return plan.strip().replace(", ", ",").replace(" ,", ",")


def parse_counts(file_path: Path) -> dict[str, int]:
    """
    Parse count.csv to get student counts per plan.

    Args:
        file_path: Path to count.csv file

    Returns:
        Dictionary mapping plan names to student counts
    """
    counts: dict[str, int] = {}

    with open(file_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Handle different column name variations
            plan = row.get("Plan", row.get("plan", "")).strip()
            count_str = row.get("Count", row.get("count", "0")).strip()

            if not plan:
                continue

            plan = normalize_plan_name(plan)

            try:
                count = int(count_str)
            except ValueError:
                count = 0

            if count > 0:
                counts[plan] = count

    return counts


def match_plan_to_count(
    plan: str,
    counts: dict[str, int],
) -> int:
    """
    Find the student count for a plan, using fuzzy matching if needed.

    Tries:
    1. Direct match
    2. Subset matching (plan contains all parts of a count key)
    3. Superset matching (count key contains all parts of plan)
    4. High overlap matching (80%+ parts in common)
    5. Prefix/suffix stripping (ALL_RM -> RM, ALL_CS -> CS/CSP)
    6. Contains matching (plan contains count_plan or vice versa)

    Args:
        plan: Plan name to find count for
        counts: Dictionary of plan -> count

    Returns:
        Student count (0 if not found)
    """
    # Direct match
    if plan in counts:
        return counts[plan]

    # Parse plan parts for fuzzy matching
    plan_parts = set(plan.split(","))

    for count_plan, count in counts.items():
        count_parts = set(count_plan.split(","))

        # Subset match
        if plan_parts.issubset(count_parts) or count_parts.issubset(plan_parts):
            return count

        # High overlap match (80%+ parts in common)
        overlap = len(plan_parts & count_parts)
        max_parts = max(len(plan_parts), len(count_parts))
        if overlap >= 0.8 * max_parts:
            return count

    # Try prefix/suffix stripping for single-part plans
    # Handle cases like ALL_RM -> RM, ALL_CS -> CS
    plan_normalized = plan.upper().replace("ALL_", "").replace("_ALL", "")

    for count_plan, count in counts.items():
        count_normalized = count_plan.upper()

        # Direct match after normalization
        if plan_normalized == count_normalized:
            return count

        # Check if normalized plan contains or is contained by count key
        if plan_normalized in count_normalized or count_normalized in plan_normalized:
            return count

        # Try matching first few characters (handle CS -> CSP, IS -> ISU)
        if len(plan_normalized) >= 2 and len(count_normalized) >= 2:
            if plan_normalized[:2] == count_normalized[:2]:
                return count

    return 0
