"""
Shift a section's timing for a specific day.

Modifies the timetable Excel file to change a section's meeting time.

Usage:
    uv run python -m timetable_generator.tools.shift_section \
        --timetable data/2025-2/6-1-26/BITS_TIME_TABLE_ADJUSTED.xlsx \
        --course "MATH F113" --section "L1" --day "Thursday" \
        --new-start "17:00" --new-end "17:50" \
        --output data/2025-2/6-1-26/BITS_TIME_TABLE_ADJUSTED_v2.xlsx
"""

import argparse
from datetime import datetime, time
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table


def parse_time_str(time_str: str) -> time:
    """Parse HH:MM string to time object."""
    h, m = map(int, time_str.split(":"))
    return time(h, m)


def time_to_excel_time(t: time) -> float:
    """Convert time object to Excel time fraction."""
    return (t.hour * 3600 + t.minute * 60) / 86400


def get_day_pattern_char(day: str) -> str:
    """Get the class pattern character for a day."""
    day_map = {
        "Monday": "M",
        "Tuesday": "T",
        "Wednesday": "W",
        "Thursday": "Th",
        "Friday": "F",
        "Saturday": "S",
        "Sunday": "Su",
    }
    return day_map.get(day, "")


def expand_class_pattern(pattern: str) -> list[str]:
    """Expand class pattern like 'TTHF' into list of days."""
    day_patterns = {
        "M": "Monday",
        "T": "Tuesday",
        "W": "Wednesday",
        "TH": "Thursday",
        "F": "Friday",
        "S": "Saturday",
        "SU": "Sunday",
    }

    pattern = pattern.upper().strip()
    days = []
    i = 0

    while i < len(pattern):
        # Check for two-character patterns first (TH, SU)
        if i + 1 < len(pattern):
            two_char = pattern[i : i + 2]
            if two_char in day_patterns:
                days.append(day_patterns[two_char])
                i += 2
                continue

        # Single character pattern
        char = pattern[i]
        if char in day_patterns:
            days.append(day_patterns[char])
        i += 1

    return days


def find_matching_rows(
    df: pd.DataFrame,
    course_code: str,
    section_name: str,
    day: str,
) -> list[int]:
    """Find row indices matching the course, section, and day."""
    matching_rows = []

    # Parse course code
    parts = course_code.split()
    if len(parts) >= 2:
        subject = parts[0]
        catalog = parts[1]
    else:
        subject = course_code
        catalog = ""

    for idx, row in df.iterrows():
        row_subject = str(row.get("Subject", "")).strip()
        row_catalog = str(row.get("Catalog", "")).strip()
        row_section = str(row.get("Section", "")).strip()
        row_pattern = str(row.get("Class Pattern", "")).strip()

        # Check if this row matches
        if row_subject == subject and row_catalog == catalog and row_section == section_name:
            # Expand the class pattern and check if our day is in it
            days_in_pattern = expand_class_pattern(row_pattern)
            if day in days_in_pattern:
                matching_rows.append(idx)

    return matching_rows


def shift_section(
    input_path: Path,
    output_path: Path,
    course_code: str,
    section_name: str,
    day: str,
    new_start: str,
    new_end: str,
    console: Console,
) -> bool:
    """
    Shift a section's timing for a specific day.

    Returns True if successful, False otherwise.
    """
    # Load the Excel file
    console.print(f"[cyan]Loading {input_path}...[/cyan]")

    # Read with header detection
    df_raw = pd.read_excel(input_path, header=None)

    # Find header row
    header_row = 0
    expected_cols = ["SUBJECT", "CATALOG", "SECTION", "CLASS PATTERN"]
    for idx in range(min(10, len(df_raw))):
        row_values = df_raw.iloc[idx].astype(str).str.strip().str.upper().tolist()
        matches = sum(1 for exp in expected_cols if any(exp in val for val in row_values))
        if matches >= 3:
            header_row = idx
            break

    # Reload with correct header
    df = pd.read_excel(input_path, header=header_row)
    df.columns = df.columns.str.strip()

    # Find matching rows
    matching_rows = find_matching_rows(df, course_code, section_name, day)

    if not matching_rows:
        console.print(f"[red]No matching rows found for {course_code} {section_name} on {day}[/red]")
        console.print("[dim]Available sections:[/dim]")

        # Show available sections for this course
        parts = course_code.split()
        if len(parts) >= 2:
            subject, catalog = parts[0], parts[1]
            for idx, row in df.iterrows():
                if str(row.get("Subject", "")).strip() == subject and str(row.get("Catalog", "")).strip() == catalog:
                    console.print(f"  - {row.get('Section', '')} on {row.get('Class Pattern', '')}")
        return False

    # Show current values
    console.print()
    console.print("[bold]Found matching rows:[/bold]")

    table = Table(title="Rows to Modify")
    table.add_column("Row", style="dim")
    table.add_column("Section")
    table.add_column("Class Pattern")
    table.add_column("Current Start")
    table.add_column("Current End")

    for idx in matching_rows:
        row = df.iloc[idx]
        start_val = row.get("Mtg Start", row.get("MTG START", ""))
        end_val = row.get("End Time", row.get("END TIME", ""))

        # Format time for display
        if pd.notna(start_val):
            if hasattr(start_val, "strftime"):
                start_str = start_val.strftime("%H:%M")
            else:
                try:
                    start_str = pd.to_datetime(start_val).strftime("%H:%M")
                except:
                    start_str = str(start_val)
        else:
            start_str = "N/A"

        if pd.notna(end_val):
            if hasattr(end_val, "strftime"):
                end_str = end_val.strftime("%H:%M")
            else:
                try:
                    end_str = pd.to_datetime(end_val).strftime("%H:%M")
                except:
                    end_str = str(end_val)
        else:
            end_str = "N/A"

        table.add_row(
            str(idx + header_row + 2),  # Excel row number (1-indexed + header)
            str(row.get("Section", "")),
            str(row.get("Class Pattern", "")),
            start_str,
            end_str,
        )

    console.print(table)
    console.print()

    # Parse new times
    new_start_time = parse_time_str(new_start)
    new_end_time = parse_time_str(new_end)

    console.print(f"[yellow]Changing times to: {new_start} - {new_end}[/yellow]")
    console.print()

    # Determine time column names
    start_col = "Mtg Start" if "Mtg Start" in df.columns else "MTG START"
    end_col = "End Time" if "End Time" in df.columns else "END TIME"

    # Apply changes
    for idx in matching_rows:
        # Convert to datetime for Excel compatibility
        base_date = datetime(1900, 1, 1)
        new_start_dt = datetime.combine(base_date.date(), new_start_time)
        new_end_dt = datetime.combine(base_date.date(), new_end_time)

        df.at[idx, start_col] = new_start_dt
        df.at[idx, end_col] = new_end_dt

    # Save to output file
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write with xlsxwriter for better formatting
    with pd.ExcelWriter(output_path, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Sheet1")

        # Get workbook and worksheet
        workbook = writer.book
        worksheet = writer.sheets["Sheet1"]

        # Format time columns
        time_format = workbook.add_format({"num_format": "hh:mm"})

        # Find column indices
        start_col_idx = df.columns.get_loc(start_col)
        end_col_idx = df.columns.get_loc(end_col)

        # Apply time format
        worksheet.set_column(start_col_idx, start_col_idx, 12, time_format)
        worksheet.set_column(end_col_idx, end_col_idx, 12, time_format)

    console.print(f"[green]✓ Saved modified timetable to {output_path}[/green]")

    # Show summary
    console.print()
    console.print(Panel.fit(
        f"[bold green]Successfully shifted {course_code} {section_name}[/bold green]\n"
        f"Day: {day}\n"
        f"New time: {new_start} - {new_end}\n"
        f"Rows modified: {len(matching_rows)}",
        title="Summary",
        border_style="green",
    ))

    return True


def create_parser() -> argparse.ArgumentParser:
    """Create argument parser."""
    parser = argparse.ArgumentParser(
        description="Shift a section's timing for a specific day.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--timetable",
        type=Path,
        required=True,
        help="Path to input timetable Excel file",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Path to output Excel file (default: adds _modified suffix)",
    )
    parser.add_argument(
        "--course",
        type=str,
        required=True,
        help="Course code (e.g., 'MATH F113')",
    )
    parser.add_argument(
        "--section",
        type=str,
        required=True,
        help="Section name (e.g., 'L1')",
    )
    parser.add_argument(
        "--day",
        type=str,
        required=True,
        choices=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        help="Day to modify",
    )
    parser.add_argument(
        "--new-start",
        type=str,
        required=True,
        help="New start time in HH:MM format (e.g., '17:00')",
    )
    parser.add_argument(
        "--new-end",
        type=str,
        required=True,
        help="New end time in HH:MM format (e.g., '17:50')",
    )

    return parser


def main(args: list[str] = None) -> int:
    """Main entry point."""
    parser = create_parser()
    opts = parser.parse_args(args)

    console = Console()

    # Validate input file
    if not opts.timetable.exists():
        console.print(f"[red]Error: Timetable file not found: {opts.timetable}[/red]")
        return 1

    # Set output path
    if opts.output:
        output_path = opts.output
    else:
        stem = opts.timetable.stem
        suffix = opts.timetable.suffix
        output_path = opts.timetable.parent / f"{stem}_modified{suffix}"

    # Validate time format
    try:
        parse_time_str(opts.new_start)
        parse_time_str(opts.new_end)
    except (ValueError, IndexError):
        console.print("[red]Error: Invalid time format. Use HH:MM (e.g., '17:00')[/red]")
        return 1

    console.print(Panel.fit(
        f"[bold blue]Shifting Section Timing[/bold blue]\n"
        f"Course: {opts.course}\n"
        f"Section: {opts.section}\n"
        f"Day: {opts.day}\n"
        f"New time: {opts.new_start} - {opts.new_end}",
        border_style="blue",
    ))

    success = shift_section(
        input_path=opts.timetable,
        output_path=output_path,
        course_code=opts.course,
        section_name=opts.section,
        day=opts.day,
        new_start=opts.new_start,
        new_end=opts.new_end,
        console=console,
    )

    return 0 if success else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
