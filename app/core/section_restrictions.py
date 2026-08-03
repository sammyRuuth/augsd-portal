"""
Section restrictions module - modular system for branch-based section filtering.

This module allows defining which sections (labs, lectures, tutorials) are available
to students based on their branch. Section names ending with specific suffixes
are restricted to certain branches.

Usage:
    from app.core.section_restrictions import is_section_allowed_for_branch

    # Check if a section is allowed for a student's branch
    allowed = is_section_allowed_for_branch(section_name="L1C", branches=["A7"])

Configuration:
    Add new restrictions to SECTION_SUFFIX_RESTRICTIONS dict below.
    Format: {"suffix": {"allowed_branches": ["B1", "B2", ...]}}
"""

from typing import Sequence

# ============================================================================
# SECTION RESTRICTION CONFIGURATION
# ============================================================================
# Section suffix -> allowed branches mapping
# If a section name ends with the suffix, only the listed branches can enroll.
# Sections not matching any suffix have no restrictions (open to all branches).
#
# To add a new restriction:
#   "SUFFIX": {"allowed_branches": ["A1", "A2", ...], "description": "..."}
# ============================================================================

SECTION_SUFFIX_RESTRICTIONS: dict[str, dict] = {
    # Sections ending with "C" are for A7 branch only
    "C": {
        "allowed_branches": ["A7"],
        "description": "Computer Science branch sections",
    },
    # Sections ending with "E" are for A3, A8, AA branches
    "E": {
        "allowed_branches": ["A3", "A8", "AA"],
        "description": "EEE, ENI, and ECE branch sections",
    },
}

# Components to which restrictions apply (LAB, LEC, TUT)
# Set to None to apply to all components
RESTRICTED_COMPONENTS: set[str] | None = {"LAB", "LEC", "TUT"}


def get_restricted_suffix(section_name: str) -> str | None:
    """
    Get the restriction suffix that applies to a section name.

    Args:
        section_name: The section name (e.g., "L1", "L1C", "P2E")

    Returns:
        The matching suffix if restricted, None otherwise.
    """
    if not section_name:
        return None

    for suffix in SECTION_SUFFIX_RESTRICTIONS:
        if section_name.endswith(suffix):
            return suffix
    return None


def get_allowed_branches_for_suffix(suffix: str) -> list[str]:
    """
    Get the list of branches allowed for a section suffix.

    Args:
        suffix: The section name suffix (e.g., "C", "E")

    Returns:
        List of allowed branch codes, or empty list if suffix not found.
    """
    restriction = SECTION_SUFFIX_RESTRICTIONS.get(suffix)
    if restriction:
        return restriction.get("allowed_branches", [])
    return []


def is_section_allowed_for_branch(
    section_name: str,
    branches: Sequence[str],
    component: str | None = None,
) -> bool:
    """
    Check if a section is allowed for the given branch(es).

    For dual-degree students, a section is allowed if ANY of their branches
    is in the allowed list for that section suffix.

    Args:
        section_name: The section name (e.g., "L1", "L1C", "P2E")
        branches: List of branch codes for the student (e.g., ["A7"] or ["B2", "A3"])
        component: Optional component type (LAB, LEC, TUT). If provided and
                   RESTRICTED_COMPONENTS is set, restrictions only apply to
                   matching components.

    Returns:
        True if the student can enroll in this section, False otherwise.
    """
    if not section_name or not branches:
        return True  # Allow if missing data

    # Check if component restrictions apply
    if RESTRICTED_COMPONENTS is not None and component:
        if component.upper() not in RESTRICTED_COMPONENTS:
            return True  # No restrictions for this component type

    # Check if section name has a restricted suffix
    suffix = get_restricted_suffix(section_name)
    if not suffix:
        return True  # No restriction, open to all branches

    # Get allowed branches for this suffix
    allowed_branches = get_allowed_branches_for_suffix(suffix)
    if not allowed_branches:
        return True  # No branches defined, allow all

    # Check if any of student's branches is allowed
    for branch in branches:
        if branch in allowed_branches:
            return True

    return False


def get_restriction_info(section_name: str) -> dict | None:
    """
    Get restriction information for a section name.

    Args:
        section_name: The section name (e.g., "L1C")

    Returns:
        Dict with restriction info if restricted, None otherwise.
        Example: {"suffix": "C", "allowed_branches": ["A7"], "description": "..."}
    """
    suffix = get_restricted_suffix(section_name)
    if not suffix:
        return None

    restriction = SECTION_SUFFIX_RESTRICTIONS.get(suffix)
    if not restriction:
        return None

    return {
        "suffix": suffix,
        "allowed_branches": restriction.get("allowed_branches", []),
        "description": restriction.get("description", ""),
    }


def filter_sections_for_branch(
    sections: list,
    branches: Sequence[str],
    section_name_getter=lambda s: s.section,
    component_getter=lambda s: getattr(s, "component", None),
) -> list:
    """
    Filter a list of sections to only those allowed for the given branch(es).

    Args:
        sections: List of section objects
        branches: List of branch codes for the student
        section_name_getter: Function to get section name from section object
        component_getter: Function to get component from section object

    Returns:
        Filtered list of sections allowed for the branch(es).
    """
    if not branches:
        return sections  # No branch info, return all

    return [
        s
        for s in sections
        if is_section_allowed_for_branch(
            section_name=section_name_getter(s),
            branches=branches,
            component=component_getter(s),
        )
    ]


def get_all_restrictions() -> dict[str, dict]:
    """
    Get all configured section restrictions.

    Returns:
        Copy of the SECTION_SUFFIX_RESTRICTIONS configuration.
    """
    return dict(SECTION_SUFFIX_RESTRICTIONS)


def add_restriction(
    suffix: str,
    allowed_branches: list[str],
    description: str = "",
) -> None:
    """
    Add or update a section restriction at runtime.

    Note: This only affects the current process. For persistent changes,
    modify SECTION_SUFFIX_RESTRICTIONS directly in this file.

    Args:
        suffix: The section name suffix (e.g., "M")
        allowed_branches: List of allowed branch codes
        description: Optional description of the restriction
    """
    SECTION_SUFFIX_RESTRICTIONS[suffix] = {
        "allowed_branches": allowed_branches,
        "description": description,
    }


def remove_restriction(suffix: str) -> bool:
    """
    Remove a section restriction at runtime.

    Note: This only affects the current process. For persistent changes,
    modify SECTION_SUFFIX_RESTRICTIONS directly in this file.

    Args:
        suffix: The section name suffix to remove

    Returns:
        True if restriction was removed, False if it didn't exist.
    """
    if suffix in SECTION_SUFFIX_RESTRICTIONS:
        del SECTION_SUFFIX_RESTRICTIONS[suffix]
        return True
    return False
