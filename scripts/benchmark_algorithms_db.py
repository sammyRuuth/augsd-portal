#!/usr/bin/env python3
"""
Algorithm Benchmark Script (Database-backed)

Benchmarks all timetable generation algorithms using real data from the database.
Each algorithm runs with a configurable timeout (default 10 seconds).

Usage:
    uv run python scripts/benchmark_algorithms_db.py

Or with arguments:
    uv run python scripts/benchmark_algorithms_db.py --session <session_id> --year 2025 --branches A5,AJ --timeout 10
    uv run python scripts/benchmark_algorithms_db.py --excel data/2025-2/6-1-26/25-dtc.xlsx --year 2025
"""

import argparse
import asyncio
import re
import signal
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from sqlalchemy import select, text

from app.core.algorithms.base import (
    GenerationConstraints,
    SectionData,
)
from app.core.algorithms.registry import AlgorithmRegistry
from app.database import AsyncSessionLocal
from app.models.course import Course
from app.models.course_section import CourseSection
from app.models.default_package import DefaultPackage
from app.models.session import Session

# ==================== Data Classes ====================


@dataclass
class StudentInfo:
    """Student information from Excel"""

    campus_id: str
    name: str
    branch: str  # e.g., A5, A1, B2
    program: str  # e.g., PS, RM, UB, CS, IS, RP
    is_pcb: bool = False  # True if student is from PCB background sheet


@dataclass
class BenchmarkResult:
    """Result from a single algorithm benchmark run"""

    algorithm: str
    success: bool
    partial: bool
    courses_scheduled: int
    total_courses: int
    execution_time_ms: float
    iterations: int
    conflicts_count: int
    total_units: float
    timed_out: bool = False
    error: str | None = None

    @property
    def schedule_rate(self) -> float:
        return (
            self.courses_scheduled / self.total_courses
            if self.total_courses > 0
            else 0.0
        )


@dataclass
class AggregatedBenchmark:
    """Aggregated results across multiple runs"""

    algorithm: str
    runs: int
    successes: int
    timeouts: int
    errors: int
    avg_courses_scheduled: float
    avg_time_ms: float
    min_time_ms: float
    max_time_ms: float
    avg_schedule_rate: float

    @property
    def success_rate(self) -> float:
        return self.successes / self.runs if self.runs > 0 else 0.0


# ==================== Database Functions ====================


async def list_sessions() -> list[dict]:
    """List all available sessions from database"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Session)
            .where(Session.is_enabled)
            .order_by(Session.created_at.desc())
        )
        sessions = result.scalars().all()
        return [
            {
                "id": str(s.id),
                "name": s.name,
                "term_code": s.term_code,
                "career": s.career,
                "schema_name": s.schema_name,
            }
            for s in sessions
        ]


async def get_session_by_id(session_id: str) -> dict | None:
    """Get session details by ID"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Session).where(Session.id == uuid.UUID(session_id))
        )
        s = result.scalar_one_or_none()
        if s:
            return {
                "id": str(s.id),
                "name": s.name,
                "term_code": s.term_code,
                "career": s.career,
                "schema_name": s.schema_name,
            }
        return None


async def get_packages_by_year_and_branches(
    year: int, branches: list[str] | None = None
) -> list[dict]:
    """Get packages from default_packages table"""
    async with AsyncSessionLocal() as session:
        query = select(DefaultPackage).where(DefaultPackage.year == year)
        if branches:
            query = query.where(DefaultPackage.branch.in_(branches))

        result = await session.execute(query)
        packages = result.scalars().all()

        return [
            {
                "branch": p.branch,
                "year": p.year,
                "course_codes": p.course_codes,
            }
            for p in packages
        ]


async def get_course_id_map(course_codes: list[str]) -> dict[str, uuid.UUID]:
    """Get mapping of course codes to course IDs"""
    async with AsyncSessionLocal() as session:
        course_map = {}

        for code in course_codes:
            parts = code.strip().split()
            if len(parts) >= 2:
                subject = parts[0]
                catalog = " ".join(parts[1:])

                result = await session.execute(
                    select(Course).where(
                        Course.subject == subject, Course.catalog == catalog
                    )
                )
                course = result.scalar_one_or_none()
                if course:
                    course_map[code] = course.id

        return course_map


async def get_sections_for_courses(
    schema_name: str, course_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[SectionData]]:
    """Get all sections for given courses from session schema"""
    sections_by_course: dict[uuid.UUID, list[SectionData]] = defaultdict(list)

    async with AsyncSessionLocal() as session:
        await session.execute(text(f'SET search_path TO "{schema_name}", public'))

        # Get course info
        course_info = {}
        for course_id in course_ids:
            result = await session.execute(select(Course).where(Course.id == course_id))
            course = result.scalar_one_or_none()
            if course:
                course_info[course_id] = {
                    "subject": course.subject,
                    "catalog": course.catalog,
                    "title": course.title,
                    "max_units": float(course.max_units) if course.max_units else 3.0,
                }

        # Query sections
        result = await session.execute(
            select(CourseSection).where(CourseSection.course_id.in_(course_ids))
        )
        sections = result.scalars().all()

        for sec in sections:
            if sec.course_id not in course_info:
                continue

            info = course_info[sec.course_id]
            section_data = SectionData(
                id=sec.id,
                course_id=sec.course_id,
                class_nbr=sec.class_nbr,
                section=sec.section,
                component=sec.component,
                day=sec.day,
                mtg_start=sec.mtg_start,
                mtg_end=sec.mtg_end,
                exam_date=sec.exam_date,
                exam_start=sec.exam_start,
                exam_end=sec.exam_end,
                instructor=sec.instructor,
                room=sec.room,
                cap_enrl=sec.cap_enrl or 0,
                tot_enrl=sec.tot_enrl or 0,
                subject=info["subject"],
                catalog=info["catalog"],
                title=info["title"],
                max_units=info["max_units"],
            )
            sections_by_course[sec.course_id].append(section_data)

    return dict(sections_by_course)


# ==================== Excel Parsing ====================


# Valid branches based on BITS system
VALID_BRANCHES = {
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


def parse_campus_id(campus_id: str) -> tuple[str, str] | None:
    """
    Parse campus ID to extract branch and program.

    Format: YYYYBBPPnnnnC or YYYYBBBPPnnnnC
    Examples:
        - 2025A5PS1518P -> branch=A5, program=PS
        - 2025A1PS0954P -> branch=A1, program=PS
        - 2025B2A30309P -> branch=B2 (dual degree with A3), program=None
        - 2025A7RM1234P -> branch=A7, program=RM

    Returns: (branch, program) or None if invalid
    """
    campus_id = campus_id.strip().upper()

    # Pattern: 4-digit year + branch (2-3 chars) + program (2 chars) + number + campus
    # Single degree: 2025A5PS1518P
    pattern = r"^(\d{4})([A-Z]\d|[A-Z]{2})([A-Z]{2})(\d+)([A-Z])$"
    match = re.match(pattern, campus_id)

    if match:
        year, branch, program, num, campus = match.groups()
        if branch in VALID_BRANCHES:
            return branch, program

    # Try dual degree pattern: 2025B2A30309P
    dual_pattern = r"^(\d{4})([A-Z]\d)([A-Z]\d)(\d+)([A-Z])$"
    match = re.match(dual_pattern, campus_id)

    if match:
        year, branch1, branch2, num, campus = match.groups()
        if branch1 in VALID_BRANCHES:
            return branch1, ""  # Dual degree, no specific program

    return None


def parse_student_excel(file_path: str) -> list[StudentInfo]:
    """
    Parse Excel file to extract ALL students with their branch/program info.

    Handles:
    - Multiple sheets
    - PCB-marked sheets (e.g., A5-PCB, AJ_PCB)
    - Regular sheets with mixed branches

    Returns: List of StudentInfo with branch, program, and PCB status
    """
    xl = pd.ExcelFile(file_path)
    all_students: list[StudentInfo] = []
    pcb_campus_ids: set[str] = set()

    print(f"\nFound sheets: {xl.sheet_names}")

    # First pass: identify PCB students from PCB-marked sheets
    for sheet_name in xl.sheet_names:
        sheet_upper = sheet_name.upper()
        is_pcb_sheet = any(marker in sheet_upper for marker in ["_PCB", "-PCB", " PCB"])

        if is_pcb_sheet:
            df = pd.read_excel(file_path, sheet_name=sheet_name, header=None)

            # Find header row
            header_row = 0
            for idx in range(min(5, len(df))):
                row_values = df.iloc[idx].astype(str).str.upper().tolist()
                if any("ID" in val for val in row_values):
                    header_row = idx
                    break

            df = pd.read_excel(file_path, sheet_name=sheet_name, header=header_row)
            df.columns = df.columns.str.strip()

            # Find ID column
            id_col = None
            for col in df.columns:
                if "ID" in str(col).upper():
                    id_col = col
                    break

            if id_col:
                for _, row in df.iterrows():
                    campus_id = str(row.get(id_col, "")).strip().upper()
                    if campus_id and campus_id != "NAN" and len(campus_id) > 5:
                        pcb_campus_ids.add(campus_id)

            print(
                f"  Sheet '{sheet_name}' (PCB): {len(pcb_campus_ids)} PCB students identified"
            )

    # Second pass: process all sheets and extract students
    seen_campus_ids: set[str] = set()

    for sheet_name in xl.sheet_names:
        df = pd.read_excel(file_path, sheet_name=sheet_name, header=None)

        # Find header row
        header_row = 0
        for idx in range(min(5, len(df))):
            row_values = df.iloc[idx].astype(str).str.upper().tolist()
            if any("ID" in val for val in row_values):
                header_row = idx
                break

        df = pd.read_excel(file_path, sheet_name=sheet_name, header=header_row)
        df.columns = df.columns.str.strip()

        # Find ID and Name columns
        id_col = None
        name_col = None
        for col in df.columns:
            col_upper = str(col).upper()
            if "ID" in col_upper and id_col is None:
                id_col = col
            elif "NAME" in col_upper and name_col is None:
                name_col = col

        if not id_col or not name_col:
            print(f"  Sheet '{sheet_name}': Could not find ID/Name columns, skipping")
            continue

        sheet_count = 0
        for _, row in df.iterrows():
            campus_id = str(row.get(id_col, "")).strip().upper()
            name = str(row.get(name_col, "")).strip()

            if not campus_id or campus_id == "NAN" or len(campus_id) < 5:
                continue

            # Skip duplicates
            if campus_id in seen_campus_ids:
                continue
            seen_campus_ids.add(campus_id)

            # Parse campus ID
            parsed = parse_campus_id(campus_id)
            if not parsed:
                continue

            branch, program = parsed
            is_pcb = campus_id in pcb_campus_ids

            all_students.append(
                StudentInfo(
                    campus_id=campus_id,
                    name=name,
                    branch=branch,
                    program=program,
                    is_pcb=is_pcb,
                )
            )
            sheet_count += 1

        print(f"  Sheet '{sheet_name}': {sheet_count} students extracted")

    return all_students


# ==================== Algorithm Execution ====================


def run_algorithm_with_timeout(
    algorithm_name: str,
    sections_by_course: dict[uuid.UUID, list[SectionData]],
    timeout_sec: float,
) -> BenchmarkResult:
    """
    Run a single algorithm with timeout.

    Uses signal-based timeout for Unix systems.
    """
    total_courses = len(sections_by_course)

    def timeout_handler(signum, frame):
        raise TimeoutError("Algorithm timed out")

    try:
        # Set up timeout (Unix only)
        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.setitimer(signal.ITIMER_REAL, timeout_sec)

        start_time = time.perf_counter()

        try:
            constraints = GenerationConstraints()
            algorithm = AlgorithmRegistry.get(algorithm_name, constraints)
            result = algorithm.generate(sections_by_course)

            elapsed_ms = (time.perf_counter() - start_time) * 1000

            return BenchmarkResult(
                algorithm=algorithm_name,
                success=result.success,
                partial=result.partial,
                courses_scheduled=result.course_count,
                total_courses=total_courses,
                execution_time_ms=elapsed_ms,
                iterations=result.iterations,
                conflicts_count=len(result.conflicts),
                total_units=result.total_units,
                timed_out=False,
            )

        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)

    except TimeoutError:
        return BenchmarkResult(
            algorithm=algorithm_name,
            success=False,
            partial=False,
            courses_scheduled=0,
            total_courses=total_courses,
            execution_time_ms=timeout_sec * 1000,
            iterations=0,
            conflicts_count=0,
            total_units=0,
            timed_out=True,
        )

    except Exception as e:
        return BenchmarkResult(
            algorithm=algorithm_name,
            success=False,
            partial=False,
            courses_scheduled=0,
            total_courses=total_courses,
            execution_time_ms=0,
            iterations=0,
            conflicts_count=0,
            total_units=0,
            error=str(e),
        )


# ==================== Benchmark Runner ====================


ALL_ALGORITHMS = [
    "greedy",
    "backtrack",
    "backtrack_optimized",
    "genetic",
    "random",
    "random_restart",
    "simulated_annealing",
    "hybrid",
    "parallel_best",
    "parallel_race",
]


def run_benchmark_suite(
    sections_by_course: dict[uuid.UUID, list[SectionData]],
    algorithms: list[str],
    timeout_sec: float = 10.0,
    num_runs: int = 3,
    verbose: bool = True,
) -> dict[str, AggregatedBenchmark]:
    """
    Run benchmark suite across all algorithms.

    Args:
        sections_by_course: Course sections to schedule
        algorithms: List of algorithm names to benchmark
        timeout_sec: Timeout per algorithm run in seconds
        num_runs: Number of runs per algorithm
        verbose: Print progress

    Returns:
        Dict mapping algorithm name to aggregated results
    """
    total_courses = len(sections_by_course)
    results: dict[str, list[BenchmarkResult]] = defaultdict(list)

    if verbose:
        print(f"\nBenchmarking {len(algorithms)} algorithms")
        print(f"Courses: {total_courses}")
        print(f"Timeout: {timeout_sec}s per run")
        print(f"Runs per algorithm: {num_runs}")
        print("-" * 70)

    for algo in algorithms:
        if verbose:
            print(f"\n  Testing: {algo}")

        for run in range(num_runs):
            result = run_algorithm_with_timeout(algo, sections_by_course, timeout_sec)
            results[algo].append(result)

            if verbose:
                if result.timed_out:
                    status = "TIMEOUT"
                elif result.error:
                    status = f"ERROR: {result.error[:30]}"
                elif result.success:
                    status = "OK"
                elif result.partial:
                    status = "PARTIAL"
                else:
                    status = "FAIL"

                print(
                    f"    Run {run + 1}: {result.courses_scheduled}/{total_courses} "
                    f"in {result.execution_time_ms:.1f}ms [{status}]"
                )

    # Aggregate results
    aggregated: dict[str, AggregatedBenchmark] = {}

    for algo, algo_results in results.items():
        valid_results = [r for r in algo_results if not r.error]
        times = [r.execution_time_ms for r in valid_results if not r.timed_out]
        scheduled = [r.courses_scheduled for r in valid_results]

        aggregated[algo] = AggregatedBenchmark(
            algorithm=algo,
            runs=len(algo_results),
            successes=sum(1 for r in algo_results if r.success),
            timeouts=sum(1 for r in algo_results if r.timed_out),
            errors=sum(1 for r in algo_results if r.error),
            avg_courses_scheduled=sum(scheduled) / len(scheduled) if scheduled else 0,
            avg_time_ms=sum(times) / len(times) if times else 0,
            min_time_ms=min(times) if times else 0,
            max_time_ms=max(times) if times else 0,
            avg_schedule_rate=sum(r.schedule_rate for r in valid_results)
            / len(valid_results)
            if valid_results
            else 0,
        )

    return aggregated


def print_benchmark_results(
    aggregated: dict[str, AggregatedBenchmark],
    total_courses: int,
):
    """Print formatted benchmark results"""
    print("\n" + "=" * 90)
    print("BENCHMARK RESULTS")
    print("=" * 90)

    # Header
    print(
        f"\n{'Algorithm':<22} {'Scheduled':>12} {'Time (ms)':>14} {'Rate':>8} "
        f"{'Success':>8} {'Timeout':>8}"
    )
    print("-" * 90)

    # Sort by schedule rate, then by time
    sorted_results = sorted(
        aggregated.values(), key=lambda r: (-r.avg_schedule_rate, r.avg_time_ms)
    )

    for r in sorted_results:
        scheduled_str = f"{r.avg_courses_scheduled:.1f}/{total_courses}"
        time_str = f"{r.avg_time_ms:.1f}" if r.avg_time_ms > 0 else "N/A"
        if r.min_time_ms != r.max_time_ms:
            time_str = f"{r.avg_time_ms:.1f} ({r.min_time_ms:.0f}-{r.max_time_ms:.0f})"
        rate_str = f"{r.avg_schedule_rate * 100:.1f}%"
        success_str = f"{r.success_rate * 100:.0f}%"
        timeout_str = f"{r.timeouts}/{r.runs}"

        print(
            f"{r.algorithm:<22} {scheduled_str:>12} {time_str:>14} {rate_str:>8} "
            f"{success_str:>8} {timeout_str:>8}"
        )

    # Summary
    print("\n" + "-" * 90)
    print("SUMMARY:")

    # Best performers
    best_by_rate = max(sorted_results, key=lambda r: r.avg_schedule_rate)
    fastest = min(
        [r for r in sorted_results if r.avg_time_ms > 0],
        key=lambda r: r.avg_time_ms,
        default=None,
    )

    print(
        f"  Best schedule rate: {best_by_rate.algorithm} ({best_by_rate.avg_schedule_rate * 100:.1f}%)"
    )
    if fastest:
        print(f"  Fastest: {fastest.algorithm} ({fastest.avg_time_ms:.1f}ms avg)")

    # Reliability (fewest timeouts)
    most_reliable = min(sorted_results, key=lambda r: r.timeouts)
    if most_reliable.timeouts == 0:
        print(f"  Most reliable: {most_reliable.algorithm} (0 timeouts)")

    print("=" * 90)


# ==================== Main ====================


async def interactive_session_select() -> dict | None:
    """Interactive session selection"""
    sessions = await list_sessions()

    if not sessions:
        print("No sessions found in database!")
        return None

    print("\n" + "=" * 60)
    print("AVAILABLE SESSIONS")
    print("=" * 60)

    for i, s in enumerate(sessions, 1):
        print(f"  [{i}] {s['name']} ({s['term_code']}) - {s['career']}")
        print(f"      Schema: {s['schema_name']}")

    while True:
        try:
            choice = input("\nSelect session number (or 'q' to quit): ").strip()
            if choice.lower() == "q":
                return None

            idx = int(choice) - 1
            if 0 <= idx < len(sessions):
                return sessions[idx]
            else:
                print(f"Invalid choice. Please enter 1-{len(sessions)}")
        except ValueError:
            print("Please enter a valid number")


async def main():
    parser = argparse.ArgumentParser(description="Algorithm Benchmark (DB-backed)")
    parser.add_argument(
        "--session",
        type=str,
        help="Session ID (if not provided, interactive selection)",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=2025,
        help="Year for package filtering (default: 2025)",
    )
    parser.add_argument(
        "--branches",
        type=str,
        help="Comma-separated branch list (e.g., A5,AJ). Ignored if --excel is provided.",
    )
    parser.add_argument(
        "--excel",
        type=str,
        help="Path to Excel file with student list. If provided, branches will be extracted from student data.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Timeout per algorithm in seconds (default: 10)",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Number of runs per algorithm (default: 3)",
    )
    parser.add_argument(
        "--algorithms",
        type=str,
        help="Comma-separated algorithm list (default: all)",
    )
    parser.add_argument(
        "--list-sessions",
        action="store_true",
        help="List available sessions and exit",
    )
    parser.add_argument(
        "--list-algorithms",
        action="store_true",
        help="List available algorithms and exit",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("ALGORITHM BENCHMARK (Database-backed)")
    print("=" * 60)

    # List algorithms
    if args.list_algorithms:
        print("\nAvailable Algorithms:")
        for algo in ALL_ALGORITHMS:
            print(f"  - {algo}")
        return

    # List sessions
    if args.list_sessions:
        sessions = await list_sessions()
        print("\nAvailable Sessions:")
        for s in sessions:
            print(f"  ID: {s['id']}")
            print(f"  Name: {s['name']}")
            print(f"  Schema: {s['schema_name']}")
            print()
        return

    # Get session
    if args.session:
        session_info = await get_session_by_id(args.session)
        if not session_info:
            print(f"Session not found: {args.session}")
            return
    else:
        session_info = await interactive_session_select()
        if not session_info:
            print("No session selected. Exiting.")
            return

    schema_name = session_info["schema_name"]

    # Parse branches from Excel or command line
    branches = None
    if args.excel:
        # Parse Excel file to extract students and their branches
        excel_path = args.excel
        if not Path(excel_path).exists():
            print(f"Excel file not found: {excel_path}")
            return

        print(f"\nParsing Excel file: {excel_path}")
        students = parse_student_excel(excel_path)

        if not students:
            print("No students found in Excel file!")
            return

        # Extract unique branches from students
        branches = sorted(set(s.branch for s in students))
        print(f"\nExtracted {len(students)} students")
        print(f"Unique branches found: {', '.join(branches)}")

    elif args.branches:
        branches = [b.strip().upper() for b in args.branches.split(",")]

    # Parse algorithms
    algorithms = ALL_ALGORITHMS
    if args.algorithms:
        algorithms = [a.strip().lower() for a in args.algorithms.split(",")]
        invalid = [a for a in algorithms if a not in ALL_ALGORITHMS]
        if invalid:
            print(f"Invalid algorithms: {invalid}")
            print(f"Available: {ALL_ALGORITHMS}")
            return

    print(f"\nSession: {session_info['name']}")
    print(f"Schema: {schema_name}")
    print(f"Year: {args.year}")
    if args.excel:
        print(f"Excel: {args.excel}")
    print(f"Branches: {branches or 'All'}")
    print(f"Timeout: {args.timeout}s")
    print(f"Runs: {args.runs}")

    # Get packages
    print("\nFetching packages...")
    packages = await get_packages_by_year_and_branches(args.year, branches)

    if not packages:
        print(f"No packages found for year {args.year}")
        return

    print(f"Found {len(packages)} packages:")
    for pkg in packages:
        print(f"  {pkg['branch']}: {len(pkg['course_codes'])} courses")

    # Get all course codes
    all_course_codes = set()
    for pkg in packages:
        all_course_codes.update(pkg["course_codes"])

    print(f"\nTotal unique courses: {len(all_course_codes)}")

    # Get course ID map
    print("Mapping courses...")
    course_id_map = await get_course_id_map(list(all_course_codes))
    print(f"Mapped {len(course_id_map)}/{len(all_course_codes)} courses")

    # Get sections
    print("Fetching sections...")
    all_course_ids = list(course_id_map.values())
    sections_by_course = await get_sections_for_courses(schema_name, all_course_ids)
    print(f"Got sections for {len(sections_by_course)} courses")

    if not sections_by_course:
        print("No sections found! Cannot run benchmark.")
        return

    # Show section summary
    total_sections = sum(len(secs) for secs in sections_by_course.values())
    print(f"Total section records: {total_sections}")

    # Run benchmark
    print("\n" + "=" * 60)
    print("RUNNING BENCHMARK")
    print("=" * 60)

    aggregated = run_benchmark_suite(
        sections_by_course,
        algorithms,
        timeout_sec=args.timeout,
        num_runs=args.runs,
        verbose=True,
    )

    # Print results
    print_benchmark_results(aggregated, len(sections_by_course))


if __name__ == "__main__":
    asyncio.run(main())
