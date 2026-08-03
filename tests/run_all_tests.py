#!/usr/bin/env python
"""
Master Test Runner

Runs all test suites for the AUGSD Portal.

Usage:
    uv run python tests/run_all_tests.py
"""

import subprocess
import sys
from pathlib import Path

from rich import print as rprint
from rich.console import Console

console = Console()

TESTS_DIR = Path(__file__).parent


def run_test(name: str, script: str) -> bool:
    """Run a test script and return success status"""
    console.rule(f"[bold cyan]Running: {name}")

    result = subprocess.run(
        [sys.executable, script],
        cwd=TESTS_DIR.parent,
        capture_output=False,
    )

    return result.returncode == 0


def main():
    console.rule("[bold magenta]AUGSD PORTAL - COMPLETE TEST SUITE")
    console.print()

    tests = [
        ("Parser Tests", TESTS_DIR / "test_parsers.py"),
        ("Branch Extractor Tests", TESTS_DIR / "test_branch_extractor.py"),
        ("Timetable Generator Tests", TESTS_DIR / "test_timetable_generator.py"),
        ("Integration Tests", TESTS_DIR / "test_integration.py"),
    ]

    results = {}

    for name, script in tests:
        if script.exists():
            results[name] = run_test(name, str(script))
        else:
            console.print(f"[yellow]⚠ Test script not found: {script}")
            results[name] = False

    # Summary
    console.rule("[bold magenta]FINAL SUMMARY")
    console.print()

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for name, result in results.items():
        status = "[green]✓ PASS" if result else "[red]✗ FAIL"
        rprint(f"  {status}[/] {name}")

    console.print()

    if passed == total:
        rprint(f"[bold green]ALL TESTS PASSED ({passed}/{total})")
    else:
        rprint(f"[bold red]SOME TESTS FAILED ({passed}/{total} passed)")

    return passed == total


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
