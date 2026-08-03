"""
Parser for Excel timetable files.

Parses the institution's timetable Excel file containing section details
including schedules, capacities, instructors, and room assignments.
"""

from collections import defaultdict
from pathlib import Path
from typing import Optional

import pandas as pd

from ..config import Config
from ..models import ComponentType, Meeting, Section


def expand_class_pattern(pattern: str, day_patterns: dict[str, str]) -> list[str]:
    """
    Expand class patterns like 'MWF' or 'TTH' into list of days.

    Args:
        pattern: Class pattern string (e.g., "MWF", "TTH")
        day_patterns: Mapping of pattern chars to day names

    Returns:
        List of day names
    """
    if not pattern:
        return []

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


def normalize_course_id(course_id: str) -> str:
    """
    Normalize course ID to 6-digit zero-padded format.

    Args:
        course_id: Raw course ID

    Returns:
        Normalized 6-digit course ID
    """
    if not course_id:
        return ""
    numeric = "".join(c for c in str(course_id) if c.isdigit())
    return numeric.zfill(6) if numeric else ""


def parse_time(value) -> Optional[str]:
    """
    Parse a time value from Excel into HH:MM format.

    Args:
        value: Time value from Excel (could be datetime, string, etc.)

    Returns:
        Time string in HH:MM format, or None
    """
    if pd.isna(value):
        return None

    try:
        if hasattr(value, "hour"):
            # datetime.time object
            return value.strftime("%H:%M")
        else:
            # Try parsing as datetime
            parsed = pd.to_datetime(value)
            return parsed.strftime("%H:%M")
    except Exception:
        return None


def parse_component(component_str: str) -> ComponentType:
    """
    Parse a component string to ComponentType enum.

    Args:
        component_str: Component string from Excel

    Returns:
        ComponentType enum value
    """
    component_str = component_str.upper().strip()
    try:
        return ComponentType(component_str)
    except ValueError:
        # Default to LEC for unknown components
        return ComponentType.LEC


def detect_header_row(df: pd.DataFrame) -> int:
    """
    Auto-detect the header row in the Excel file.

    Args:
        df: DataFrame loaded without header

    Returns:
        Row index of header
    """
    expected_cols = ["COURSE ID", "SUBJECT", "CATALOG", "CLASS NBR", "SECTION"]

    for idx in range(min(10, len(df))):
        row_values = df.iloc[idx].astype(str).str.strip().str.upper().tolist()
        matches = sum(
            1 for exp in expected_cols if any(exp in val for val in row_values)
        )
        if matches >= 3:
            return idx

    return 0  # Default to first row


def parse_excel_timetable(
    file_path: Path,
    config: Config,
) -> tuple[dict[str, list[Section]], dict[str, str]]:
    """
    Parse timetable Excel file into Section objects.

    Args:
        file_path: Path to Excel file
        config: Configuration object

    Returns:
        Tuple of:
        - sections_by_course: Dictionary of course_code -> list of Sections
        - course_id_map: Dictionary of course_code -> course_id
    """
    # Load without header to detect it
    df = pd.read_excel(file_path, header=None)

    # Find and set header row
    header_row = detect_header_row(df)
    df = pd.read_excel(file_path, header=header_row)
    df.columns = df.columns.str.strip()

    # Group rows by logical section key
    logical_sections: dict[tuple, dict] = {}
    course_id_map: dict[str, str] = {}

    for idx, row in df.iterrows():
        try:
            section_data = _parse_row(row, config, idx)
            if section_data is None:
                continue

            course_id_map[section_data["course_code"]] = section_data["course_id"]

            # Build logical section key
            logical_key = (
                section_data["course_code"],
                section_data["component"],
                section_data["section"],
                section_data["class_nbr"],
            )

            # Expand days from class pattern
            days = expand_class_pattern(
                section_data["class_pattern"],
                config.day_patterns,
            )
            if not days or not (section_data["mtg_start"] and section_data["mtg_end"]):
                days = [""]  # Placeholder for sections without meeting times

            for day in days:
                if logical_key not in logical_sections:
                    logical_sections[logical_key] = {
                        "course_code": section_data["course_code"],
                        "course_id": section_data["course_id"],
                        "class_nbr": section_data["class_nbr"],
                        "section": section_data["section"],
                        "component": section_data["component"],
                        "title": section_data["title"],
                        "capacity": section_data["capacity"],
                        "enrolled": section_data["enrolled"],
                        "instructor": section_data["instructor"],
                        "exam_date": section_data["exam_date"],
                        "meetings": [],
                    }

                # Add meeting time
                if day and section_data["mtg_start"] and section_data["mtg_end"]:
                    meeting = Meeting(
                        day=day,
                        start=section_data["mtg_start"],
                        end=section_data["mtg_end"],
                        room=section_data["room"],
                    )
                    if meeting not in logical_sections[logical_key]["meetings"]:
                        logical_sections[logical_key]["meetings"].append(meeting)

                # Update capacity (take max if multiple rows)
                if section_data["capacity"] > logical_sections[logical_key]["capacity"]:
                    logical_sections[logical_key]["capacity"] = section_data["capacity"]

        except Exception as e:
            # Log error but continue processing
            print(f"Warning: Error parsing row {idx}: {e}")
            continue

    # Convert to Section objects
    sections_by_course: dict[str, list[Section]] = defaultdict(list)

    for key, data in logical_sections.items():
        # Apply capacity override if configured
        capacity = data["capacity"]
        override = config.get_section_capacity_override(
            data["course_code"],
            data["component"],
        )
        if override is not None:
            capacity = override

        section = Section(
            course_code=data["course_code"],
            course_id=data["course_id"],
            class_nbr=data["class_nbr"],
            section=data["section"],
            component=parse_component(data["component"]),
            title=data["title"],
            capacity=capacity,
            enrolled=data["enrolled"],
            meetings=tuple(data["meetings"]),
            instructor=data["instructor"],
            exam_date=data["exam_date"],
        )

        sections_by_course[data["course_code"]].append(section)

    return dict(sections_by_course), course_id_map


def _parse_row(row: pd.Series, config: Config, idx: int) -> Optional[dict]:
    """
    Parse a single row from the Excel file.

    Args:
        row: DataFrame row
        config: Configuration object
        idx: Row index (for error reporting)

    Returns:
        Dictionary with parsed data, or None if row is invalid
    """
    # Extract course identification
    raw_course_id = str(row.get("Course ID", "")).strip()
    course_id = normalize_course_id(raw_course_id)
    subject = str(row.get("Subject", "")).strip()
    catalog = str(row.get("Catalog", "")).strip()

    if not course_id or not subject or not catalog:
        return None

    course_code = f"{subject} {catalog}"

    # Extract class number
    class_nbr_raw = row.get("Class Nbr", 0)
    if pd.isna(class_nbr_raw):
        return None
    try:
        class_nbr = int(float(class_nbr_raw))
    except (ValueError, TypeError):
        return None
    if not class_nbr:
        return None

    # Extract section info
    section_name = str(row.get("Section", "")).strip()
    component = str(row.get("Component", "")).strip()
    title = str(row.get("Title", row.get("Course Title", ""))).strip()

    # Extract schedule
    class_pattern = ""
    if pd.notna(row.get("Class Pattern")):
        class_pattern = str(row.get("Class Pattern")).strip()

    mtg_start = parse_time(row.get("Mtg Start", row.get("MTG START")))
    mtg_end = parse_time(row.get("End Time", row.get("END TIME")))

    # Extract capacity
    capacity = 0
    cap_raw = row.get("Cap Enrl", row.get("CAP ENRL"))
    if pd.notna(cap_raw):
        try:
            capacity = int(float(cap_raw))
        except (ValueError, TypeError):
            pass

    enrolled = 0
    enrl_raw = row.get("Tot Enrl", row.get("TOT ENRL"))
    if pd.notna(enrl_raw):
        try:
            enrolled = int(float(enrl_raw))
        except (ValueError, TypeError):
            pass

    # Extract instructor and room
    instructor = None
    if pd.notna(row.get("Name")):
        instructor = str(row.get("Name")).strip()

    room = None
    if pd.notna(row.get("Room")):
        room = str(row.get("Room")).strip()

    # Extract exam info
    exam_date = None
    if pd.notna(row.get("Exam Date")):
        try:
            val = row.get("Exam Date")
            if hasattr(val, "date"):
                exam_date = val.date().isoformat()
            else:
                exam_date = pd.to_datetime(val).date().isoformat()
        except Exception:
            pass

    return {
        "course_code": course_code,
        "course_id": course_id,
        "class_nbr": class_nbr,
        "section": section_name,
        "component": component,
        "title": title,
        "class_pattern": class_pattern,
        "mtg_start": mtg_start,
        "mtg_end": mtg_end,
        "capacity": capacity,
        "enrolled": enrolled,
        "instructor": instructor,
        "room": room,
        "exam_date": exam_date,
    }
