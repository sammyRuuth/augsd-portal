"""Excel and CSV parsers for data uploads - outputs Pydantic models"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from app.schemas.course import CourseCreate
from app.schemas.registration import RegistrationDataCreate
from app.schemas.student import StudentCreate


@dataclass
class ParseResult:
    """Result from parsing an Excel file with stats and duplicate tracking"""

    data: list[Any] = field(default_factory=list)
    total_rows: int = 0
    duplicates_removed: int = 0
    duplicate_details: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def __iter__(self):
        """Preserve older call sites that treated parser results like a list."""
        return iter(self.data)

    def __len__(self) -> int:
        """Preserve older call sites that used len(parse_result)."""
        return len(self.data)

    def __getitem__(self, index):
        """Preserve indexing against the parsed payload."""
        return self.data[index]

    def __bool__(self) -> bool:
        """Treat the result as truthy when parsed data exists."""
        return bool(self.data)


# Day pattern mapping for class patterns
DAY_PATTERNS = {
    "M": "Monday",
    "T": "Tuesday",
    "W": "Wednesday",
    "TH": "Thursday",
    "F": "Friday",
    "S": "Saturday",
    "SU": "Sunday",
}


def expand_class_pattern(pattern: str) -> list[str]:
    """
    Expand class patterns like 'MWF' or 'TTH' into list of days.

    Examples:
        'MWF' -> ['Monday', 'Wednesday', 'Friday']
        'TTH' -> ['Tuesday', 'Thursday']
        'TTHF' -> ['Tuesday', 'Thursday', 'Friday']
        'S' -> ['Saturday']
    """
    if not pattern:
        return []

    pattern = pattern.upper().strip()
    days = []
    i = 0

    while i < len(pattern):
        # Check for two-character patterns first
        if i + 1 < len(pattern):
            two_char = pattern[i : i + 2]
            if two_char == "TH":
                days.append("Thursday")
                i += 2
                continue
            elif two_char == "SU":
                days.append("Sunday")
                i += 2
                continue

        # Single character patterns
        char = pattern[i]
        if char in DAY_PATTERNS:
            days.append(DAY_PATTERNS[char])
        i += 1

    return days


def normalize_course_id(course_id: str) -> str:
    """Normalize course ID to 6-digit zero-padded format."""
    if not course_id:
        return ""
    # Remove any non-numeric characters and zero-pad
    numeric = "".join(c for c in str(course_id) if c.isdigit())
    return numeric.zfill(6) if numeric else ""


def split_course_code(value: str) -> tuple[str, str]:
    """
    Split a combined course code into (subject, catalog).

    Newer BITS_REG_DATA exports ship one "Catlog_Nbr" column holding "EEE F241"
    instead of separate Subject/Catalog columns.

        >>> split_course_code("EEE F241")
        ('EEE', 'F241')
    """
    text = " ".join(str(value).split())
    if not text:
        return "", ""

    subject, _, catalog = text.partition(" ")
    if catalog:
        return subject.strip(), catalog.strip()

    # No separator, e.g. "EEEF241": subject is the leading letters, catalog the rest
    match = re.match(r"^([A-Za-z]+?)([A-Za-z]?\d.*)$", text)
    if match:
        return match.group(1), match.group(2)
    return text, ""


def cell_text(row: "pd.Series", *names: str) -> str:
    """First non-empty value among the given column names, as stripped text."""
    for name in names:
        if name in row.index:
            value = row.get(name)
            if pd.notna(value):
                text = str(value).strip()
                if text:
                    return text
    return ""


HEADER_SCAN_ROWS = 25

# Column names used by exports that combine subject and catalog into one field
COMBINED_COURSE_CODE_COLUMNS = {
    "Catlog_Nbr",
    "Catalog Nbr",
    "Catlog Nbr",
    "Catalog_Nbr",
    "Course Code",
}


def detect_header_row(df: pd.DataFrame, expected_columns: list[str]) -> int:
    """
    Detect which row contains the header by looking for expected column names.

    Returns the index of the header row, or 0 if not found.

    ERP exports carry a variable number of title/metadata rows above the header,
    so this scans HEADER_SCAN_ROWS rows rather than assuming it is near the top.
    """
    for idx in range(min(HEADER_SCAN_ROWS, len(df))):
        row_values = df.iloc[idx].astype(str).str.strip().str.upper().tolist()
        expected_upper = [col.upper() for col in expected_columns]

        # Check if at least 50% of expected columns are present
        matches = sum(
            1 for exp in expected_upper if any(exp in val for val in row_values)
        )
        if matches >= len(expected_columns) * 0.5:
            return idx

    return 0  # Default to first row


def parse_students_excel(file_path: str | Path) -> ParseResult:
    """
    Parse students Excel file and return ParseResult with StudentCreate models.

    Expected columns: ID, Campus ID, Name, Email, Sex, Birthdate, etc.
    Tracks and removes duplicates by student_id and campus_id.
    """
    result = ParseResult()

    df = pd.read_excel(file_path, header=None)

    # Detect header row
    expected_cols = ["ID", "Campus ID", "Name", "Email", "Sex"]
    header_row = detect_header_row(df, expected_cols)

    # Re-read with correct header
    df = pd.read_excel(file_path, header=header_row)

    # Normalize column names
    df.columns = df.columns.str.strip()

    seen_student_ids: set[int] = set()
    seen_campus_ids: set[str] = set()
    students: list[StudentCreate] = []

    for idx, row in df.iterrows():
        result.total_rows += 1
        try:
            # Extract student data
            student_id = int(row.get("ID", row.get("Student ID", 0)))
            campus_id = str(row.get("Campus ID", row.get("CAMPUS ID", ""))).strip()

            if not student_id or not campus_id:
                continue

            # Check for duplicates
            is_duplicate = False
            if student_id in seen_student_ids:
                is_duplicate = True
                result.duplicate_details.append(
                    {
                        "row": idx + header_row + 2,
                        "type": "student_id",
                        "value": student_id,
                        "campus_id": campus_id,
                        "name": str(row.get("Name", "")).strip(),
                    }
                )
            elif campus_id in seen_campus_ids:
                is_duplicate = True
                result.duplicate_details.append(
                    {
                        "row": idx + header_row + 2,
                        "type": "campus_id",
                        "value": campus_id,
                        "student_id": student_id,
                        "name": str(row.get("Name", "")).strip(),
                    }
                )

            if is_duplicate:
                result.duplicates_removed += 1
                continue

            seen_student_ids.add(student_id)
            seen_campus_ids.add(campus_id)

            # Parse birthdate
            birthdate = None
            if pd.notna(row.get("Birthdate")):
                try:
                    birthdate = pd.to_datetime(row["Birthdate"]).date()
                except Exception:
                    pass

            student = StudentCreate(
                student_id=student_id,
                campus_id=campus_id,
                name=str(row.get("Name", "")).strip(),
                email=str(row.get("Email", row.get("Student Email", "")))
                .strip()
                .lower()
                if pd.notna(row.get("Email")) or pd.notna(row.get("Student Email"))
                else None,
                sex=str(row.get("Sex", "")).strip()
                if pd.notna(row.get("Sex"))
                else None,
                birthdate=birthdate,
                admission_category=str(row.get("Admission Category", "")).strip()
                if pd.notna(row.get("Admission Category"))
                else None,
            )
            students.append(student)
        except Exception as e:
            result.errors.append(f"Row {idx + header_row + 2}: {str(e)}")
            continue

    result.data = students
    return result


def parse_courses_timetable_excel(
    file_path: str | Path,
    *,
    include_parse_result: bool = False,
) -> (
    tuple[list[CourseCreate], list[dict[str, Any]]]
    | tuple[list[CourseCreate], list[dict[str, Any]], ParseResult]
):
    """
    Parse courses/timetable Excel file and return courses and sections.

    Expected columns: Course ID, Subject, Catalog, Title, Class Nbr, Section,
                     Component, Class Pattern, Mtg Start, End Time, Exam Date, etc.

    Returns either:
    - (courses, sections) for compatibility with older callers
    - (courses, sections, parse_result) when include_parse_result=True

    Note:
    - Expands class patterns (MWF) into individual day records
    - Handles split schedules (same class_nbr with different patterns/times on different rows)
    - Aggregates duplicate meetings (same class_nbr + day + time) that differ only by instructor
    """
    result = ParseResult()

    df = pd.read_excel(file_path, header=None)

    # Detect header row
    expected_cols = ["Course ID", "Subject", "Catalog", "Class Nbr", "Section"]
    header_row = detect_header_row(df, expected_cols)

    # Re-read with correct header
    df = pd.read_excel(file_path, header=header_row)
    df.columns = df.columns.str.strip()

    # Track unique courses
    courses_dict: dict[tuple[str, str, str], CourseCreate] = {}

    # Group meetings by (class_nbr, day, mtg_start, mtg_end) for proper aggregation
    # This handles: split schedules, day expansion, and instructor aggregation
    grouped_meetings: dict[tuple, dict[str, Any]] = {}
    instructor_sets: dict[tuple, set[str]] = {}

    for idx, row in df.iterrows():
        result.total_rows += 1
        try:
            raw_course_id = str(row.get("Course ID", row.get("COURSE ID", ""))).strip()
            course_id = normalize_course_id(raw_course_id)
            subject = str(row.get("Subject", row.get("SUBJECT", ""))).strip()
            catalog = str(row.get("Catalog", row.get("CATALOG", ""))).strip()

            if not course_id or not subject or not catalog:
                continue

            # Add course if not seen
            course_key = (course_id, subject, catalog)
            if course_key not in courses_dict:
                title = str(row.get("Title", row.get("Course Title", ""))).strip()
                max_units = None
                if pd.notna(row.get("Max Units")):
                    try:
                        max_units = float(row.get("Max Units"))
                    except Exception:
                        pass

                courses_dict[course_key] = CourseCreate(
                    course_id=course_id,
                    subject=subject,
                    catalog=catalog,
                    title=title,
                    max_units=max_units,
                )

            # Parse section info
            class_nbr_raw = row.get("Class Nbr", row.get("CLASS NBR", 0))
            if pd.isna(class_nbr_raw):
                continue
            class_nbr = int(float(class_nbr_raw))
            if not class_nbr:
                continue

            section = str(row.get("Section", row.get("SECTION", ""))).strip()
            component = str(row.get("Component", row.get("COMPONENT", ""))).strip()

            # Get class pattern for this row
            class_pattern = (
                str(row.get("Class Pattern", "")).strip()
                if pd.notna(row.get("Class Pattern"))
                else ""
            )

            # Parse times
            mtg_start = None
            mtg_start_str = None
            if pd.notna(row.get("Mtg Start", row.get("MTG START"))):
                try:
                    val = row.get("Mtg Start", row.get("MTG START"))
                    if hasattr(val, "hour"):
                        mtg_start = val
                    else:
                        mtg_start = pd.to_datetime(val).time()
                    mtg_start_str = mtg_start.strftime("%H:%M") if mtg_start else None
                except Exception:
                    pass

            mtg_end = None
            mtg_end_str = None
            end_time_val = row.get("End Time", row.get("END TIME"))
            if pd.notna(end_time_val):
                try:
                    if hasattr(end_time_val, "hour"):
                        mtg_end = end_time_val
                    else:
                        mtg_end = pd.to_datetime(end_time_val).time()
                    mtg_end_str = mtg_end.strftime("%H:%M") if mtg_end else None
                except Exception:
                    pass

            # Expand class pattern to individual days
            days = expand_class_pattern(class_pattern)
            if not days or not (mtg_start and mtg_end):
                # No pattern or no times - create single record with empty day
                days_to_emit = [""]
            else:
                days_to_emit = days

            # Parse exam date/time (same for all meetings of this class)
            exam_date = None
            if pd.notna(row.get("Exam Date", row.get("EXAM DATE"))):
                try:
                    val = row.get("Exam Date", row.get("EXAM DATE"))
                    if hasattr(val, "date"):
                        exam_date = val.date() if hasattr(val, "date") else val
                    else:
                        exam_date = pd.to_datetime(val).date()
                except Exception:
                    pass

            exam_start = None
            start_time_val = row.get("Start Time")
            if pd.notna(start_time_val):
                try:
                    if hasattr(start_time_val, "hour"):
                        exam_start = start_time_val
                    else:
                        exam_start = pd.to_datetime(start_time_val).time()
                except Exception:
                    pass

            exam_end = None
            end_time_cols = [c for c in row.index if "End Time" in str(c)]
            if len(end_time_cols) > 1:
                exam_end_val = row.get(end_time_cols[1])
                if pd.notna(exam_end_val):
                    try:
                        if hasattr(exam_end_val, "hour"):
                            exam_end = exam_end_val
                        else:
                            exam_end = pd.to_datetime(exam_end_val).time()
                    except Exception:
                        pass

            # Parse enrollment
            cap_enrl = 0
            if pd.notna(row.get("Cap Enrl", row.get("CAP ENRL"))):
                try:
                    cap_enrl = int(float(row.get("Cap Enrl", row.get("CAP ENRL"))))
                    # Handle negative values
                    if cap_enrl < 0:
                        cap_enrl = 0
                except Exception:
                    pass

            tot_enrl = 0
            if pd.notna(row.get("Tot Enrl", row.get("TOT ENRL"))):
                try:
                    tot_enrl = int(float(row.get("Tot Enrl", row.get("TOT ENRL"))))
                    # Handle negative values (e.g., -1 meaning "not set" in source data)
                    if tot_enrl < 0:
                        tot_enrl = 0
                except Exception:
                    pass

            # Get instructor and room
            instructor = ""
            name_val = row.get("Name")
            if pd.notna(name_val):
                instructor = str(name_val).strip()

            room = ""
            room_val = row.get("Room")
            if pd.notna(room_val):
                room = str(room_val).strip()

            # Create a meeting record for each day
            for day in days_to_emit:
                # Key for grouping: unique meeting slot
                meeting_key = (
                    class_nbr,
                    day,
                    mtg_start_str if day else None,
                    mtg_end_str if day else None,
                )

                if meeting_key in grouped_meetings:
                    # Duplicate meeting - aggregate instructor only
                    result.duplicates_removed += 1
                    result.duplicate_details.append(
                        {
                            "row": idx + header_row + 2,
                            "type": "meeting",
                            "value": class_nbr,
                            "course": f"{subject} {catalog}",
                            "section": section,
                            "day": day,
                            "time": f"{mtg_start_str}-{mtg_end_str}"
                            if mtg_start_str
                            else "N/A",
                            "instructor": instructor,
                            "action": "instructor_aggregated",
                        }
                    )

                    # Aggregate instructor
                    if instructor:
                        instructor_sets[meeting_key].add(instructor)

                    # Keep max enrollment
                    existing = grouped_meetings[meeting_key]
                    if cap_enrl > existing.get("cap_enrl", 0):
                        existing["cap_enrl"] = cap_enrl
                    if tot_enrl > existing.get("tot_enrl", 0):
                        existing["tot_enrl"] = tot_enrl
                    # Use room if existing is empty
                    if room and not existing.get("room"):
                        existing["room"] = room
                else:
                    # New meeting slot
                    meeting_data = {
                        "course_id": course_id,
                        "class_nbr": class_nbr,
                        "section": section,
                        "component": component,
                        "day": day if day else None,
                        "class_pattern": class_pattern if class_pattern else None,
                        "mtg_start": mtg_start if day else None,
                        "mtg_end": mtg_end if day else None,
                        "exam_date": exam_date,
                        "exam_start": exam_start,
                        "exam_end": exam_end,
                        "instructor": instructor,
                        "room": room,
                        "cap_enrl": cap_enrl,
                        "tot_enrl": tot_enrl,
                    }
                    grouped_meetings[meeting_key] = meeting_data
                    instructor_sets[meeting_key] = {instructor} if instructor else set()

        except Exception as e:
            result.errors.append(f"Row {idx + header_row + 2}: {str(e)}")
            continue

    # Finalize: join instructors and build sections list
    sections: list[dict[str, Any]] = []
    for meeting_key, meeting_data in grouped_meetings.items():
        instructors = instructor_sets.get(meeting_key, set())
        # Filter empty and sort for consistent output
        cleaned_instructors = sorted(
            {name.strip() for name in instructors if name and name.strip()},
            key=lambda x: x.lower(),
        )
        meeting_data["instructor"] = (
            ", ".join(cleaned_instructors) if cleaned_instructors else None
        )
        sections.append(meeting_data)

    courses = list(courses_dict.values())
    result.data = sections
    if include_parse_result:
        return courses, sections, result
    return courses, sections


def parse_prerequisites_excel(file_path: str | Path) -> list[dict[str, Any]]:
    """
    Parse prerequisites Excel file.

    The file has a complex structure with columns like:
    - Course ID, Subject, Catalog, Title
    - Preq1 code, preq1 subject, preq1 catalog, pereq1 title, Co /Pre
    - AND/OR column
    - Preq2 code, preq2 sub, preq2 cat, etc.

    Returns: List of dicts with prerequisite info (to be processed by service layer)
    """
    df = pd.read_excel(file_path, header=None)

    # Detect header row - look for 'Course ID' in column values
    expected_cols = ["Course ID", "Subject", "Catalog"]
    header_row = detect_header_row(df, expected_cols)

    # Re-read with correct header
    df = pd.read_excel(file_path, header=header_row)
    # Don't strip column names - they may have duplicates

    prerequisites: list[dict[str, Any]] = []

    for _, row in df.iterrows():
        try:
            course_id = normalize_course_id(
                str(row.iloc[0] if pd.notna(row.iloc[0]) else "")
            )
            subject = str(row.iloc[1]).strip() if pd.notna(row.iloc[1]) else ""
            catalog = str(row.iloc[2]).strip() if pd.notna(row.iloc[2]) else ""
            title = str(row.iloc[4]).strip() if pd.notna(row.iloc[4]) else ""

            if not course_id or not subject or not catalog:
                continue

            # Parse prereq columns based on actual column positions
            # Prereq 1: columns 5-9 (Preq1 code, preq1 subject, preq1 catalog, pereq1 title, Co /Pre)
            # AND/OR 1: column 10
            # Prereq 2: columns 11-15
            # etc.

            prereq_positions = [
                (
                    5,
                    6,
                    7,
                    8,
                    9,
                    10,
                ),  # prereq1: code, subject, catalog, title, co/pre, and/or
                (11, 12, 13, 14, 15, 16),  # prereq2
                (17, 18, 19, 20, 21, 22),  # prereq3
                (23, 24, 25, 26, 27, None),  # prereq4 (no AND/OR after)
            ]

            for i, positions in enumerate(prereq_positions, 1):
                code_pos, subj_pos, cat_pos, title_pos, copre_pos, andor_pos = positions

                # Check if prereq code exists
                prereq_code = ""
                if code_pos < len(row) and pd.notna(row.iloc[code_pos]):
                    prereq_code = normalize_course_id(str(row.iloc[code_pos]))

                if not prereq_code:
                    continue

                prereq_subject = ""
                if subj_pos < len(row) and pd.notna(row.iloc[subj_pos]):
                    prereq_subject = str(row.iloc[subj_pos]).strip()

                prereq_catalog = ""
                if cat_pos < len(row) and pd.notna(row.iloc[cat_pos]):
                    prereq_catalog = str(row.iloc[cat_pos]).strip()

                prereq_title = ""
                if title_pos < len(row) and pd.notna(row.iloc[title_pos]):
                    prereq_title = str(row.iloc[title_pos]).strip()

                # Co/Pre - PRE = prerequisite, C = corequisite
                is_coreq = False
                if copre_pos < len(row) and pd.notna(row.iloc[copre_pos]):
                    copre_val = str(row.iloc[copre_pos]).strip().upper()
                    is_coreq = copre_val in ("C", "CO", "COREQ")

                # AND/OR logic
                prereq_type = "AND"
                if andor_pos and andor_pos < len(row) and pd.notna(row.iloc[andor_pos]):
                    andor_val = str(row.iloc[andor_pos]).strip().upper()
                    if "OR" in andor_val:
                        prereq_type = "OR"

                # Build prereq value string
                prereq_value = f"{prereq_subject} {prereq_catalog}".strip()
                if not prereq_value:
                    prereq_value = prereq_code

                prerequisites.append(
                    {
                        "course_id": course_id,
                        "subject": subject,
                        "catalog": catalog,
                        "title": title,
                        "prereq_code": prereq_code,
                        "prereq_subject": prereq_subject,
                        "prereq_catalog": prereq_catalog,
                        "prereq_title": prereq_title,
                        "prereq_value": prereq_value,
                        "prereq_order": i,
                        "prereq_type": prereq_type,
                        "is_corequisite": is_coreq,
                    }
                )

        except Exception:
            continue

    return prerequisites


def parse_registration_excel(file_path: str | Path) -> list[RegistrationDataCreate]:
    """
    Parse registration data Excel file.

    Expected columns: Campus ID, Course ID, Subject, Catalog, Section, Component,
                     Class Nbr, Add Dt, Drop Dt, Unit Taken, Grade In, etc.
    """
    df = pd.read_excel(file_path, header=None)

    # Detect header row
    expected_cols = ["Campus ID", "Course ID", "Class Nbr", "Subject"]
    header_row = detect_header_row(df, expected_cols)

    # Re-read with correct header
    df = pd.read_excel(file_path, header=header_row)
    df.columns = df.columns.str.strip()

    # Fail loudly on a header mismatch. Without this every row silently fails the
    # completeness check below and the caller only sees "no valid registration data".
    required_cols = ["Campus ID", "Course ID", "Class Nbr"]
    missing = [col for col in required_cols if col not in df.columns]
    # Subject/catalog arrive either as two columns or as one combined column
    if not ({"Subject", "Catalog"} <= set(df.columns)) and not (
        COMBINED_COURSE_CODE_COLUMNS & set(df.columns)
    ):
        missing.append("Subject + Catalog (or Catlog_Nbr)")
    if missing:
        found = ", ".join(str(col) for col in list(df.columns)[:15]) or "(none)"
        raise ValueError(
            f"Registration file is missing required column(s): {', '.join(missing)}. "
            f"Header was detected on row {header_row + 1}, giving columns: {found}. "
            f"Expected a raw BITS_REG_DATA export with its header row within the "
            f"first {HEADER_SCAN_ROWS} rows."
        )

    registrations: list[RegistrationDataCreate] = []
    skipped_missing_class_nbr = 0
    skipped_missing_ids = 0
    skipped_errors = 0
    first_error: str | None = None

    for _, row in df.iterrows():
        try:
            campus_id = str(row.get("Campus ID", "")).strip()
            course_id = normalize_course_id(str(row.get("Course ID", "")))
            class_nbr_raw = row.get("Class Nbr", 0)
            if pd.isna(class_nbr_raw):
                skipped_missing_class_nbr += 1
                continue
            class_nbr = int(float(class_nbr_raw))

            if not campus_id or not course_id or not class_nbr:
                skipped_missing_ids += 1
                continue

            # Older exports carry Subject and Catalog separately; newer ones combine
            # them into a single Catlog_Nbr column ("EEE F241").
            subject = cell_text(row, "Subject")
            catalog = cell_text(row, "Catalog")
            if not subject or not catalog:
                combined = cell_text(row, *COMBINED_COURSE_CODE_COLUMNS)
                if combined:
                    derived_subject, derived_catalog = split_course_code(combined)
                    subject = subject or derived_subject
                    catalog = catalog or derived_catalog

            # Parse dates
            add_dt = None
            if pd.notna(row.get("Add Dt")):
                try:
                    val = row.get("Add Dt")
                    if hasattr(val, "date"):
                        add_dt = val.date() if hasattr(val, "date") else val
                    else:
                        add_dt = pd.to_datetime(val).date()
                except Exception:
                    pass

            drop_dt = None
            if pd.notna(row.get("Drop Dt")):
                try:
                    val = row.get("Drop Dt")
                    if hasattr(val, "date"):
                        drop_dt = val.date() if hasattr(val, "date") else val
                    else:
                        drop_dt = pd.to_datetime(val).date()
                except Exception:
                    pass

            # Parse units
            unit_taken = None
            if pd.notna(row.get("Unit Taken")):
                try:
                    unit_taken = float(row.get("Unit Taken"))
                except Exception:
                    pass

            registration = RegistrationDataCreate(
                campus_id=campus_id,
                course_id=course_id,
                subject=subject,
                catalog=catalog,
                section=str(row.get("Section", "")).strip(),
                component=str(row.get("Component", "")).strip(),
                class_nbr=class_nbr,
                add_dt=add_dt,
                drop_dt=drop_dt,
                unit_taken=unit_taken,
                grade_in=str(row.get("Grade In", "")).strip()
                if pd.notna(row.get("Grade In"))
                else None,
                instructor_name=str(
                    row.get("Name", row.get("Instructor Name", ""))
                ).strip()
                if pd.notna(row.get("Name")) or pd.notna(row.get("Instructor Name"))
                else None,
                admit_sem=str(row.get("Admit Sem", "")).strip()
                if pd.notna(row.get("Admit Sem"))
                else None,
                last_reg_sem=str(row.get("Last Reg Sem", "")).strip()
                if pd.notna(row.get("Last Reg Sem"))
                else None,
                degree1=str(row.get("Degree1", "")).strip()
                if pd.notna(row.get("Degree1"))
                else None,
                degree2=str(row.get("Degree2", "")).strip()
                if pd.notna(row.get("Degree2"))
                else None,
            )
            registrations.append(registration)

        except Exception as exc:
            skipped_errors += 1
            if first_error is None:
                first_error = str(exc).replace("\n", " ")[:300]
            continue

    if not registrations:
        detail = f" First error: {first_error}" if first_error else ""
        raise ValueError(
            f"No usable registration rows in file. Read {len(df)} row(s) below the "
            f"header on row {header_row + 1}; skipped {skipped_missing_class_nbr} with "
            f"a blank Class Nbr, {skipped_missing_ids} missing Campus ID/Course ID, "
            f"and {skipped_errors} that could not be parsed.{detail}"
        )

    return registrations


@dataclass
class BufferTimetableData:
    """Data structure for a parsed buffer timetable."""

    plan: str
    timetable_id: int
    batch_size: int
    capacity_ceiling: int
    is_variant: bool
    items: list[dict]  # List of {course_code, component, section, days, times, room, instructor}


@dataclass
class BufferTimetableParseResult:
    """Result from parsing buffer timetables CSV."""

    timetables: list[BufferTimetableData]
    total_rows: int = 0
    total_timetables: int = 0
    total_items: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def parse_buffer_timetables_csv(file_path: str | Path) -> BufferTimetableParseResult:
    """
    Parse buffer timetables CSV file generated by generate_buffer_csv.py script.

    Expected columns:
        Plan, Timetable ID, Batch Size, Capacity Ceiling, Variant,
        Course Code, Component, Section, Days, Times, Room, Instructor

    Returns BufferTimetableParseResult with grouped timetables.
    """
    import csv

    result = BufferTimetableParseResult(timetables=[])

    file_path = Path(file_path)
    if not file_path.exists():
        result.errors.append(f"File not found: {file_path}")
        return result

    # Read CSV
    with open(file_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        # Group rows by (plan, timetable_id)
        timetable_map: dict[tuple[str, int], BufferTimetableData] = {}

        for row_num, row in enumerate(reader, start=2):  # Start at 2 (header is row 1)
            result.total_rows += 1

            try:
                plan = row.get("Plan", "").strip()
                tt_id_str = row.get("Timetable ID", "0").strip()
                batch_size_str = row.get("Batch Size", "0").strip()
                capacity_ceiling_str = row.get("Capacity Ceiling", "0").strip()
                variant_str = row.get("Variant", "no").strip().lower()

                if not plan:
                    result.warnings.append(f"Row {row_num}: Empty plan, skipping")
                    continue

                tt_id = int(tt_id_str) if tt_id_str else 0
                batch_size = int(batch_size_str) if batch_size_str else 0
                capacity_ceiling = int(capacity_ceiling_str) if capacity_ceiling_str else 0
                is_variant = variant_str == "yes"

                key = (plan, tt_id)

                # Create timetable entry if not exists
                if key not in timetable_map:
                    timetable_map[key] = BufferTimetableData(
                        plan=plan,
                        timetable_id=tt_id,
                        batch_size=batch_size,
                        capacity_ceiling=capacity_ceiling,
                        is_variant=is_variant,
                        items=[],
                    )

                # Add item
                course_code = row.get("Course Code", "").strip()
                component = row.get("Component", "").strip()
                section = row.get("Section", "").strip()

                if course_code and component and section:
                    item = {
                        "course_code": course_code,
                        "component": component,
                        "section": section,
                        "days": row.get("Days", "").strip(),
                        "times": row.get("Times", "").strip(),
                        "room": row.get("Room", "").strip(),
                        "instructor": row.get("Instructor", "").strip(),
                    }
                    timetable_map[key].items.append(item)
                    result.total_items += 1
                else:
                    result.warnings.append(
                        f"Row {row_num}: Missing course_code/component/section"
                    )

            except ValueError as e:
                result.errors.append(f"Row {row_num}: Invalid number format - {e}")
            except Exception as e:
                result.errors.append(f"Row {row_num}: {str(e)}")

        # Convert to list
        result.timetables = list(timetable_map.values())
        result.total_timetables = len(result.timetables)

    return result
