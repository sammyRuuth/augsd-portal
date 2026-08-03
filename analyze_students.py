#!/usr/bin/env python3
"""
Analyze student Excel file and create count.csv
"""

import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from app.core.branch_extractor import extract_branch_info

# Valid branches from commit_bulk_timetables.py
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
    "C1",
    "C2",
    "C3",
    "C4",
    "C5",
    "C6",
    "C7",
    "C8",
    "D2",
    "H1",
    "H7",
    "PS",
    "RM",
}

VALID_PROGRAMS = {
    "PS",
    "RM",
    "UB",
    "CS",
    "IS",
    "RP",
    "MM",
}


def parse_student_excel(file_path: str):
    """Parse Excel file to extract students with branch/program info."""
    students = []

    try:
        xl = pd.ExcelFile(file_path)
        sheet_names = xl.sheet_names

        print(f"Found {len(sheet_names)} sheets: {sheet_names}")

        for sheet_name in sheet_names:
            is_pcb = "PCB" in sheet_name.upper()
            df = pd.read_excel(file_path, sheet_name=sheet_name)

            print(f"\nProcessing sheet: {sheet_name} (PCB={is_pcb})")
            print(f"Columns: {list(df.columns)}")

            # Find campus ID column
            campus_col = None
            name_col = None

            for col in df.columns:
                col_lower = str(col).lower()
                if "campus" in col_lower and "id" in col_lower:
                    campus_col = col
                elif col_lower in ["name", "student name", "full name"]:
                    name_col = col

            if campus_col is None:
                # Try first column
                campus_col = df.columns[0] if len(df.columns) > 0 else None

            if campus_col is None:
                print("  No campus ID column found, skipping")
                continue

            print(f"  Using column '{campus_col}' for campus IDs")

            for idx, row in df.iterrows():
                campus_id = str(row.get(campus_col, "")).strip()
                if not campus_id or campus_id == "nan":
                    continue

                # Use branch_extractor
                info = extract_branch_info(campus_id)
                if not info:
                    print(f"  Could not parse: {campus_id}")
                    continue

                branches = info.get("branches", [])
                program = info.get("program")

                # Get primary branch (first one for dual degrees)
                branch = branches[0] if branches else ""

                name = str(row.get(name_col, "")).strip() if name_col else ""

                students.append(
                    {
                        "campus_id": campus_id,
                        "name": name,
                        "branch": branch,
                        "program": program,
                        "is_pcb": is_pcb,
                        "branches": branches,
                    }
                )

    except Exception as e:
        print(f"Error parsing Excel: {e}")
        import traceback

        traceback.print_exc()

    return students


def match_student_to_plan(student, available_plans=None):
    """
    Match a student to an appropriate plan based on branch/program.

    Following the logic from commit_bulk_timetables.py
    """
    branch = student["branch"]
    program = student["program"]
    is_pcb = student["is_pcb"]

    # Define potential plan categories
    # Based on count.csv example:
    # - A3, A4, A5, A7, A8, AA, AD, AJ (group 1)
    # - A1, A2, AB, B1, B2, B3, B4, B5, B7, D2 (group 2)
    # - A5_PCB (PCB variant)
    # - ALL_CS, ALL_IS, ALL_RM, ALL_RP, ALL_UB, ALL_MM (program-specific)

    # 1. Check for PCB variant first
    if is_pcb and branch == "A5":
        return "A5_PCB"

    # 2. Check for program-specific plans
    if program in ["CS", "IS", "RM", "RP", "UB", "MM"]:
        return f"ALL_{program}"

    # 3. Group 1: A3, A4, A5, A7, A8, AA, AD, AJ
    group1 = {"A3", "A4", "A5", "A7", "A8", "AA", "AD", "AJ"}
    if branch in group1:
        return "A3, A4, A5, A7, A8, AA, AD, AJ"

    # 4. Group 2: A1, A2, AB, B1, B2, B3, B4, B5, B7, D2
    group2 = {"A1", "A2", "AB", "B1", "B2", "B3", "B4", "B5", "B7", "D2"}
    if branch in group2:
        return "A1, A2, AB, B1, B2, B3, B4, B5, B7, D2"

    # 5. Unknown/unmatched
    return f"UNKNOWN_{branch}"


def main():
    excel_path = "data/2025-2/6-1-26/not-reg.xlsx"
    output_path = "data/2025-2/count.csv"

    print("=" * 60)
    print("ANALYZING STUDENT EXCEL FILE")
    print("=" * 60)
    print(f"Input: {excel_path}")
    print(f"Output: {output_path}")

    # Parse students
    students = parse_student_excel(excel_path)

    print("\n" + "=" * 60)
    print(f"FOUND {len(students)} STUDENTS")
    print("=" * 60)

    # Show first few students
    print("\nFirst 10 students:")
    for s in students[:10]:
        print(
            f"  {s['campus_id']} - Branch: {s['branch']}, Program: {s['program']}, PCB: {s['is_pcb']}"
        )

    # Group by plan
    plan_counts = defaultdict(int)

    for student in students:
        plan = match_student_to_plan(student)
        plan_counts[plan] += 1

    print("\n" + "=" * 60)
    print("PLAN DISTRIBUTION")
    print("=" * 60)

    # Sort plans for display
    # Order: grouped plans first, then ALL_ plans, then unknowns
    def plan_sort_key(plan):
        if plan.startswith("A3, A4"):
            return (0, plan)
        elif plan.startswith("A1, A2"):
            return (1, plan)
        elif "_PCB" in plan:
            return (2, plan)
        elif plan.startswith("ALL_"):
            return (3, plan)
        else:
            return (4, plan)

    sorted_plans = sorted(plan_counts.items(), key=lambda x: plan_sort_key(x[0]))

    total = 0
    for plan, count in sorted_plans:
        print(f"  {plan}: {count}")
        total += count

    print(f"\nTotal: {total}")

    # Write count.csv
    print("\n" + "=" * 60)
    print("WRITING count.csv")
    print("=" * 60)

    with open(output_path, "w") as f:
        f.write("Plan,Count\n")
        for plan, count in sorted_plans:
            # Quote plans with commas
            if "," in plan:
                f.write(f'"{plan}",{count}\n')
            else:
                f.write(f"{plan},{count}\n")

    print(f"Written to {output_path}")


if __name__ == "__main__":
    main()
