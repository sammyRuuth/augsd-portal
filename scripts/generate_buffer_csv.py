#!/usr/bin/env python3
"""
Generate Buffer Timetables CSV for Portal Upload

This script converts the timetables_summary.csv from the timetable generator
into a format suitable for uploading to the portal as buffer timetables.

The output CSV removes the class_nbr column since the portal will match
sections by course_code + section + component.

Usage:
    uv run python scripts/generate_buffer_csv.py exports/t7
    uv run python scripts/generate_buffer_csv.py exports/t7 --output buffer_timetables.csv
"""

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path


def generate_buffer_csv(
    export_dir: Path,
    output_path: Path | None = None,
) -> Path:
    """
    Generate buffer timetables CSV from export directory.

    Args:
        export_dir: Path to export directory containing timetables_summary.csv
        output_path: Optional output path (defaults to export_dir/buffer_timetables.csv)

    Returns:
        Path to generated CSV file
    """
    summary_path = export_dir / "timetables_summary.csv"

    if not summary_path.exists():
        raise FileNotFoundError(f"timetables_summary.csv not found in {export_dir}")

    if output_path is None:
        output_path = export_dir / "buffer_timetables.csv"

    # Read the summary CSV
    with open(summary_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        raise ValueError("timetables_summary.csv is empty")

    # Group rows by (Plan, Timetable ID) to deduplicate meeting times
    # and get unique section entries
    timetable_sections: dict[tuple[str, int], dict[str, dict]] = defaultdict(dict)
    timetable_meta: dict[tuple[str, int], dict] = {}

    for row in rows:
        plan = row["Plan"]
        tt_id = int(row["Timetable ID"])
        key = (plan, tt_id)

        # Store metadata (only need once per timetable)
        if key not in timetable_meta:
            timetable_meta[key] = {
                "batch_size": int(row["Batch Size"]),
                "capacity_ceiling": int(row["Capacity Ceiling"]),
                "is_variant": row["Variant"].lower() == "yes",
            }

        # Create unique key for each section (course + section + component)
        course_code = row["Course Code"]
        section = row["Section"]
        component = row["Component"]
        section_key = f"{course_code}|{section}|{component}"

        # Store section info (may have multiple rows for different days/times)
        if section_key not in timetable_sections[key]:
            timetable_sections[key][section_key] = {
                "course_code": course_code,
                "component": component,
                "section": section,
                "days": [],
                "times": [],
                "room": row.get("Room", ""),
                "instructor": row.get("Instructor", ""),
            }

        # Add day/time info
        day = row.get("Day", "")
        start = row.get("Start", "")
        end = row.get("End", "")
        if day and day != "Online/Self-Study":
            timetable_sections[key][section_key]["days"].append(day)
            if start and end:
                timetable_sections[key][section_key]["times"].append(f"{start}-{end}")

    # Write output CSV
    output_rows = []
    fieldnames = [
        "Plan",
        "Timetable ID",
        "Batch Size",
        "Capacity Ceiling",
        "Variant",
        "Course Code",
        "Component",
        "Section",
        "Days",
        "Times",
        "Room",
        "Instructor",
    ]

    for (plan, tt_id), sections in sorted(timetable_sections.items()):
        meta = timetable_meta[(plan, tt_id)]

        for section_key, section_info in sorted(sections.items()):
            output_rows.append(
                {
                    "Plan": plan,
                    "Timetable ID": tt_id,
                    "Batch Size": meta["batch_size"],
                    "Capacity Ceiling": meta["capacity_ceiling"],
                    "Variant": "yes" if meta["is_variant"] else "no",
                    "Course Code": section_info["course_code"],
                    "Component": section_info["component"],
                    "Section": section_info["section"],
                    "Days": ",".join(sorted(set(section_info["days"]))),
                    "Times": ",".join(sorted(set(section_info["times"]))),
                    "Room": section_info["room"],
                    "Instructor": section_info["instructor"],
                }
            )

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    # Print summary
    unique_timetables = len(timetable_meta)
    total_sections = len(output_rows)
    total_capacity = sum(m["batch_size"] for m in timetable_meta.values())

    print(f"\n{'='*60}")
    print("Buffer Timetables CSV Generated")
    print(f"{'='*60}")
    print(f"Source: {summary_path}")
    print(f"Output: {output_path}")
    print(f"\nSummary:")
    print(f"  - Unique timetables: {unique_timetables}")
    print(f"  - Total section rows: {total_sections}")
    print(f"  - Total capacity (batch_size sum): {total_capacity}")
    print()

    # Print per-plan breakdown
    plan_stats: dict[str, dict] = defaultdict(lambda: {"count": 0, "capacity": 0})
    for (plan, _), meta in timetable_meta.items():
        plan_stats[plan]["count"] += 1
        plan_stats[plan]["capacity"] += meta["batch_size"]

    print("Per-Plan Breakdown:")
    for plan, stats in sorted(plan_stats.items()):
        print(f"  {plan[:50]:<50} {stats['count']:>3} timetables, {stats['capacity']:>4} capacity")

    print(f"\n{'='*60}")

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Generate buffer timetables CSV for portal upload",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run python scripts/generate_buffer_csv.py exports/t7
  uv run python scripts/generate_buffer_csv.py exports/t7 --output my_buffer.csv
        """,
    )
    parser.add_argument(
        "export_dir",
        type=Path,
        help="Path to export directory containing timetables_summary.csv",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Output CSV path (default: <export_dir>/buffer_timetables.csv)",
    )

    args = parser.parse_args()

    if not args.export_dir.exists():
        print(f"Error: Export directory not found: {args.export_dir}", file=sys.stderr)
        sys.exit(1)

    try:
        output_path = generate_buffer_csv(args.export_dir, args.output)
        print(f"\nSuccess! Upload this file to the portal: {output_path}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
