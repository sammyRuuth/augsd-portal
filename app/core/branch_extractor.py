"""Branch and year extraction from Campus ID"""

import re
from typing import NamedTuple

# Valid branch codes (18 total)
VALID_BRANCHES = frozenset(
    {
        "A1",
        "A2",
        "A3",
        "A4",
        "A5",
        "A7",
        "A8",
        "AA",
        "AB",
        "AD",
        "AJ",
        "B1",
        "B2",
        "B3",
        "B4",
        "B5",
        "B7",
        "D2",
    }
)

# Valid program suffixes (7 total)
VALID_PROGRAMS = frozenset({"PS", "RM", "CS", "RP", "UB", "IS", "TS"})


class CampusIDInfo(NamedTuple):
    """Parsed campus ID information"""

    year: int
    branches: str  # Can be single (A3) or dual (B2A3)
    program: str | None  # Program suffix (PS, RM, CS, etc.) - None for dual degrees
    student_number: str
    campus_suffix: str | None


def extract_campus_id_info(campus_id: str) -> CampusIDInfo | None:
    r"""
    Extract year, branch, and program from Campus ID.

    Two formats:
    - Single degree: 2023A3PS0309P -> year=2023, branch=A3, program=PS
    - Single degree: 2023A3RM0309P -> year=2023, branch=A3, program=RM
    - Dual degree: 2023B2A30309P -> year=2023, branch=B2A3, program=None

    Campus ID format: YYYY[BRANCH]([PROGRAM|DUAL_BRANCH])NNNN[SUFFIX]
    - YYYY: 4-digit year
    - BRANCH: Branch code (2 chars from VALID_BRANCHES)
    - PROGRAM: Program suffix (PS, RM, CS, RP, UB, IS, TS) for single degree
    - DUAL_BRANCH: Second branch code for dual degree
    - NNNN: 4-digit student number
    - SUFFIX: Optional campus suffix (P, G, H, D)
    """
    # Try single degree pattern: YYYY + Branch(2) + Program(2) + NNNN + Suffix
    # ^(\d{4})                    - Year (group 1)
    # ([A-Z0-9]{2})               - Branch (2 chars, group 2)
    # (PS|RM|CS|RP|UB|IS|TS)      - Program suffix (group 3)
    # (\d{4})                     - Student number (group 4)
    # ([PGHD])?$                  - Optional campus suffix (group 5)
    single_pattern = r"^(\d{4})([A-Z0-9]{2})(PS|RM|CS|RP|UB|IS|TS)(\d{4})([PGHD])?$"
    match = re.match(single_pattern, campus_id)

    if match:
        branch = match.group(2)
        # Validate branch is in allowed set
        if branch not in VALID_BRANCHES:
            return None
        return CampusIDInfo(
            year=int(match.group(1)),
            branches=branch,  # Single branch like "A3"
            program=match.group(3),  # Program like "PS", "RM", etc.
            student_number=match.group(4),
            campus_suffix=match.group(5),
        )

    # Try dual degree pattern: YYYY + Branch1(2) + Branch2(2) + NNNN + Suffix
    # ^(\d{4})           - Year (group 1)
    # ([A-Z0-9]{2})      - First branch (2 chars, group 2)
    # ([A-Z0-9]{2})      - Second branch (2 chars, group 3)
    # (\d{4})            - Student number (group 4)
    # ([PGHD])?$         - Optional campus suffix (group 5)
    dual_pattern = r"^(\d{4})([A-Z0-9]{2})([A-Z0-9]{2})(\d{4})([PGHD])?$"
    match = re.match(dual_pattern, campus_id)

    if match:
        branch1 = match.group(2)
        branch2 = match.group(3)
        # Validate both branches are in allowed set
        if branch1 not in VALID_BRANCHES or branch2 not in VALID_BRANCHES:
            return None
        # Combine both branches into single string like "B2A3"
        branches = branch1 + branch2
        return CampusIDInfo(
            year=int(match.group(1)),
            branches=branches,  # Combined branches like "B2A3"
            program=None,  # Dual degrees don't have a program suffix
            student_number=match.group(4),
            campus_suffix=match.group(5),
        )

    return None


def get_branch_list(branches: str) -> list[str]:
    """
    Parse branch string into list of branches.

    Examples:
    - "A3" -> ["A3"]
    - "B2A3" -> ["B2", "A3"]
    - "B1A8" -> ["B1", "A8"]
    """
    # For single branches (2 chars), return as is
    if len(branches) <= 2:
        return [branches]

    # For dual degrees, split into 2-character chunks
    branch_list = [branches[i : i + 2] for i in range(0, len(branches), 2)]
    return branch_list


def extract_branch_info(campus_id: str) -> dict:
    """
    Extract branch info from campus ID and return as dict.

    Returns dict with:
    - year: int (e.g., 2023)
    - branches: list[str] (e.g., ["A3"] or ["B2", "A3"])
    - program: str | None (e.g., "PS", "RM", None for dual)
    - student_number: str (e.g., "0309")
    - campus_suffix: str | None (e.g., "P")

    Returns empty dict if campus ID cannot be parsed.
    """
    info = extract_campus_id_info(campus_id)
    if not info:
        return {}

    return {
        "year": info.year,
        "branches": get_branch_list(info.branches),
        "program": info.program,
        "student_number": info.student_number,
        "campus_suffix": info.campus_suffix,
    }
