#!/usr/bin/env python
"""
Test 2: Branch Extractor Tests

Tests branch extraction from Campus IDs.
"""

import sys
from pathlib import Path

from rich import print as rprint
from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.branch_extractor import extract_branch_info

console = Console()


def test_branch_extraction():
    """Test branch extraction from various campus ID formats"""
    console.rule("[bold blue]Test: Branch Extraction")

    # Test cases - corrected for proper format
    test_cases = [
        # (campus_id, expected_year, expected_branches, expected_program)
        # Single degree with PS: YYYY + Branch(2) + PS + NNNN + Suffix
        ("2023A3PS0309P", 2023, ["A3"], "PS"),
        ("2024A7PS0123P", 2024, ["A7"], "PS"),
        ("2015A7PS0120P", 2015, ["A7"], "PS"),
        ("2018A3PS0403P", 2018, ["A3"], "PS"),
        ("2018ABPS0172P", 2018, ["AB"], "PS"),
        ("2019D2PS1289P", 2019, ["D2"], "PS"),
        ("2020A3PS0367P", 2020, ["A3"], "PS"),
        ("2024B4PS0993P", 2024, ["B4"], "PS"),
        ("2025A3PS1234P", 2025, ["A3"], "PS"),
        ("2025A5PS5678P", 2025, ["A5"], "PS"),
        # Single degree with other programs: RM, CS, RP, UB, IS, TS
        ("2023A3RM0309P", 2023, ["A3"], "RM"),
        ("2024A7CS0123P", 2024, ["A7"], "CS"),
        ("2025A4RP0456G", 2025, ["A4"], "RP"),
        ("2024A1UB0789H", 2024, ["A1"], "UB"),
        ("2023A5IS0111P", 2023, ["A5"], "IS"),
        ("2025AJTS0222P", 2025, ["AJ"], "TS"),
        # Without campus suffix
        ("2023A3RM0309", 2023, ["A3"], "RM"),
        ("2024A7TS1234", 2024, ["A7"], "TS"),
        # Dual degree: YYYY + Branch1(2) + Branch2(2) + NNNN + Suffix
        ("2023B2A30309P", 2023, ["B2", "A3"], None),
        ("2019B1A81048P", 2019, ["B1", "A8"], None),
        ("2024B2A30456G", 2024, ["B2", "A3"], None),
        ("2025B5A70123", 2025, ["B5", "A7"], None),
    ]

    results = []
    all_passed = True

    for campus_id, expected_year, expected_branches, expected_program in test_cases:
        try:
            result = extract_branch_info(campus_id)

            year_match = result.get("year") == expected_year
            branches_match = set(result.get("branches", [])) == set(expected_branches)
            program_match = result.get("program") == expected_program

            passed = year_match and branches_match and program_match
            all_passed = all_passed and passed

            results.append(
                {
                    "campus_id": campus_id,
                    "expected_year": expected_year,
                    "expected_branches": expected_branches,
                    "expected_program": expected_program,
                    "actual_year": result.get("year"),
                    "actual_branches": result.get("branches", []),
                    "actual_program": result.get("program"),
                    "passed": passed,
                }
            )

        except Exception as e:
            all_passed = False
            results.append(
                {
                    "campus_id": campus_id,
                    "expected_year": expected_year,
                    "expected_branches": expected_branches,
                    "expected_program": expected_program,
                    "actual_year": None,
                    "actual_branches": [],
                    "actual_program": None,
                    "passed": False,
                    "error": str(e),
                }
            )

    # Display results
    table = Table(title="Branch Extraction Results")
    table.add_column("Campus ID", style="cyan")
    table.add_column("Expected Year")
    table.add_column("Actual Year")
    table.add_column("Expected Branches")
    table.add_column("Actual Branches")
    table.add_column("Expected Program")
    table.add_column("Actual Program")
    table.add_column("Status")

    for r in results:
        status = "[green]✓" if r["passed"] else "[red]✗"
        if "error" in r:
            status += f" ({r['error']})"

        table.add_row(
            r["campus_id"],
            str(r["expected_year"]),
            str(r["actual_year"]),
            str(r["expected_branches"]),
            str(r["actual_branches"]),
            str(r["expected_program"]),
            str(r["actual_program"]),
            status,
        )

    console.print(table)

    if all_passed:
        rprint("[green]✓ All branch extraction tests passed")
    else:
        rprint("[red]✗ Some branch extraction tests failed")

    return all_passed


def test_default_package_matching():
    """Test matching campus IDs to default packages"""
    console.rule("[bold blue]Test: Default Package Matching")

    # Sample default packages with ALL_{program} keys
    default_packages = {
        "2025": {
            "A3, A4, A5, A7, A8, AA, AD, AJ": [
                "BIO F101",
                "BITS F111",
                "BITS K101",
                "CS F111",
                "MATH F101",
            ],
            "A1, A2, AB, B1, B2, B3, B4, B5, B7, D2": [
                "BITS F111",
                "BITS K101",
                "CHEM F101",
                "EEE F111",
                "MATH F101",
                "PHY F101",
            ],
            "A5_PCB, AJ_PCB": [
                "BIO F101",
                "BITS F101",
                "BITS F113",
                "BITS K101",
                "CHEM F101",
                "PHY F102",
            ],
            # Program-wide packages using ALL_{program} format
            "ALL_RM": [
                "BIO F101",
                "BITS F234",
                "CHEM F101",
                "MATH F101",
            ],
            "ALL_CS": [
                "CS F111",
                "CS F222",
                "MATH F101",
            ],
        }
    }

    def get_courses_for_student(campus_id: str, packages: dict) -> list[str]:
        """Get default courses for a student based on their campus ID."""
        result = extract_branch_info(campus_id)
        year = result.get("year")
        branches = result.get("branches", [])
        program = result.get("program")

        if not year or not branches:
            return []

        year_str = str(year)
        if year_str not in packages:
            return []

        year_packages = packages[year_str]

        # Build specific keys (branch+program, ALL_{program}) and general keys (just branch)
        specific_keys = set()
        general_keys = set(branches)
        if program and program != "PS":
            for branch in branches:
                specific_keys.add(f"{branch}_{program}")
            # Also add ALL_{program} key for program-wide packages
            specific_keys.add(f"ALL_{program}")

        # First try to find a specific branch+program or ALL_{program} match
        for branch_group, courses in year_packages.items():
            group_branches = [b.strip() for b in branch_group.split(",")]
            if any(key in group_branches for key in specific_keys):
                return courses

        # Fall back to general branch match
        for branch_group, courses in year_packages.items():
            group_branches = [b.strip() for b in branch_group.split(",")]
            if any(key in group_branches for key in general_keys):
                return courses

        return []

    # Test cases
    test_cases = [
        # Standard PS students match branch group
        (
            "2025A3PS1234P",
            ["BIO F101", "BITS F111", "BITS K101", "CS F111", "MATH F101"],
        ),
        (
            "2025B4PS0993P",
            [
                "BITS F111",
                "BITS K101",
                "CHEM F101",
                "EEE F111",
                "MATH F101",
                "PHY F101",
            ],
        ),
        # RM students match branch+program key
        (
            "2025A3RM1234P",
            ["BIO F101", "BITS F234", "CHEM F101", "MATH F101"],
        ),
        # CS students match branch+program key
        (
            "2025A4CS5678P",
            ["CS F111", "CS F222", "MATH F101"],
        ),
        ("2024A7PS0123P", []),  # Year 2024 not in packages
    ]

    all_passed = True

    table = Table(title="Default Package Matching Results")
    table.add_column("Campus ID", style="cyan")
    table.add_column("Expected Courses")
    table.add_column("Actual Courses")
    table.add_column("Status")

    for campus_id, expected_courses in test_cases:
        actual_courses = get_courses_for_student(campus_id, default_packages)
        passed = set(actual_courses) == set(expected_courses)
        all_passed = all_passed and passed

        table.add_row(
            campus_id,
            str(expected_courses[:3]) + "..."
            if len(expected_courses) > 3
            else str(expected_courses),
            str(actual_courses[:3]) + "..."
            if len(actual_courses) > 3
            else str(actual_courses),
            "[green]✓" if passed else "[red]✗",
        )

    console.print(table)

    if all_passed:
        rprint("[green]✓ All default package matching tests passed")
    else:
        rprint("[red]✗ Some default package matching tests failed")

    return all_passed


def main():
    """Run all branch extractor tests"""
    console.rule("[bold magenta]BRANCH EXTRACTOR TESTS")

    results = {
        "Branch Extraction": test_branch_extraction(),
        "Default Package Matching": test_default_package_matching(),
    }

    console.rule("[bold magenta]RESULTS")

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for name, result in results.items():
        status = "[green]✓ PASS" if result else "[red]✗ FAIL"
        rprint(f"  {status}[/] {name}")

    rprint(f"\n[bold]Total: {passed}/{total} passed")

    return passed == total


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
