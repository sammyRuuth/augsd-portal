"""Export service for timetable export functionality

Optimized for performance with:
- Raw SQL queries with JOINs instead of ORM eager loading
- Set-based operations for diffing
- xlsxwriter engine for faster Excel generation
- Single-pass data processing with deduplication
"""

import random
import string
import zipfile
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.utils import sanitize_filename
from app.models.registration_timetable import RegistrationTimetable


def get_exports_dir() -> Path:
    """Directory export files are written to, created if absent."""
    exports_dir = Path(get_settings().export_dir)
    exports_dir.mkdir(parents=True, exist_ok=True)
    return exports_dir


def generate_unique_id() -> str:
    """Generate a 3-character unique identifier using alphanumeric characters.

    Note: This function is maintained here for backward compatibility.
    For new code, consider using app.core.utils.generate_unique_filename() which
    includes filename sanitization and proper extension handling.
    """
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=3))


class ExportRow(NamedTuple):
    """Lightweight structure for export data"""

    campus_id: str
    class_nbr: int


ERP_EXPORT_COLUMNS = ["Campus ID", "Career", "Term", "Class Nbr"]


def transfer_export_timestamp() -> str:
    """Timestamp prefix for transfer export files: ddmm_hhmm."""
    return datetime.now().strftime("%d%m_%H%M")


def _build_erp_export_dataframe(
    campus_id: str, class_nbrs: list[int], term_code: str, career: str
) -> pd.DataFrame:
    """Build ERP upload dataframe with standard columns."""
    return pd.DataFrame(
        [
            {
                "Campus ID": campus_id,
                "Career": career,
                "Term": term_code,
                "Class Nbr": class_nbr,
            }
            for class_nbr in sorted(set(class_nbrs))
        ],
        columns=ERP_EXPORT_COLUMNS,
    )


def export_transfer_timetables(
    source_campus_id: str,
    target_campus_id: str,
    class_nbrs: list[int],
    term_code: str,
    career: str,
    timestamp: str | None = None,
) -> tuple[Path, Path, Path, str]:
    """
    Write delete/add ERP export files and a ZIP bundle for a timetable transfer.

    Returns:
        (delete_path, add_path, zip_path, timestamp_prefix)
    """
    ts = timestamp or transfer_export_timestamp()
    exports_dir = get_exports_dir()

    delete_filename = f"{ts}_delete.xlsx"
    add_filename = f"{ts}_add.xlsx"
    zip_filename = f"{ts}_transfer.zip"

    delete_path = exports_dir / delete_filename
    add_path = exports_dir / add_filename
    zip_path = exports_dir / zip_filename

    df_delete = _build_erp_export_dataframe(
        source_campus_id, class_nbrs, term_code, career
    )
    df_add = _build_erp_export_dataframe(
        target_campus_id, class_nbrs, term_code, career
    )

    df_delete.to_excel(delete_path, index=False, engine="xlsxwriter")
    df_add.to_excel(add_path, index=False, engine="xlsxwriter")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(delete_path, delete_filename)
        zf.write(add_path, add_filename)

    return delete_path, add_path, zip_path, ts


# Audit actions that take a class away from a student, and where the old section
# id(s) sit inside the JSON details. 'transfer' is excluded on purpose: transfers
# ship their own ERP files at transfer time, so replaying them here would send the
# same delete twice.
_PORTAL_REMOVAL_SQL = """
    WITH removed AS (
        SELECT t.student_id, elem::uuid AS section_id
        FROM timetable_audit_trail aud
        JOIN timetables t ON t.id = aud.timetable_id
        CROSS JOIN LATERAL jsonb_array_elements_text(aud.details -> 'section_ids') AS elem
        WHERE aud.action = 'remove_course'
          AND jsonb_typeof(aud.details -> 'section_ids') = 'array'

        UNION ALL

        SELECT t.student_id, (aud.details ->> 'old_section_id')::uuid
        FROM timetable_audit_trail aud
        JOIN timetables t ON t.id = aud.timetable_id
        WHERE aud.action = 'swap_section'
          AND aud.details ->> 'old_section_id' IS NOT NULL

        UNION ALL

        SELECT t.student_id, (sw ->> 'old_section_id')::uuid
        FROM timetable_audit_trail aud
        JOIN timetables t ON t.id = aud.timetable_id
        CROSS JOIN LATERAL jsonb_array_elements(aud.details -> 'swaps') AS sw
        WHERE aud.action = 'multi_swap'
          AND jsonb_typeof(aud.details -> 'swaps') = 'array'
          AND sw ->> 'old_section_id' IS NOT NULL
    )
    SELECT DISTINCT s.id AS student_id, s.campus_id, cs.class_nbr
    FROM removed r
    JOIN students s ON s.id = r.student_id
    JOIN course_sections cs ON cs.id = r.section_id
"""


async def collect_portal_removals(
    db: AsyncSession, current_by_student: dict[str, set[int]]
) -> set[ExportRow]:
    """
    Deletions for classes the portal took away that the registration diff cannot see.

    The diff below is against the registration baseline, so it only produces a delete
    when the class came from registration. A class the portal itself added and then
    removed or swapped away appears in neither the baseline nor the current state, so
    without this the student would keep both the old and the new section in ERP.

    Replaying history can emit a delete for a class ERP never received (added and
    swapped away between two exports). That is an accepted trade-off: a no-op delete
    is cheaper than a student registered in two sections at once.
    """
    result = await db.execute(text(_PORTAL_REMOVAL_SQL))

    removals: set[ExportRow] = set()
    for row in result.fetchall():
        # Still held (removed then re-added, or swapped back): nothing to delete
        if row.class_nbr in current_by_student.get(str(row.student_id), set()):
            continue
        removals.add(ExportRow(row.campus_id, row.class_nbr))
    return removals


async def build_erp_change_sets(
    db: AsyncSession,
) -> tuple[set[ExportRow], set[ExportRow]]:
    """
    Compute the (to_add, to_delete) ERP change sets for the whole session.

    Registration data, when present, is the baseline: anything the student holds now
    but did not register for is an add, anything they registered for but no longer
    hold is a delete. Without a registration baseline every current class is an add.
    Classes the portal removed or swapped away are added to the deletes on top, since
    the baseline diff alone cannot see those.

    Timetable transfers are deliberately NOT covered here - they ship their own ERP
    files at transfer time (see export_transfer_timetables). Emitting them here too
    would push the same add/delete to ERP twice.
    """
    reg_tt_result = await db.execute(select(RegistrationTimetable).limit(1))
    has_registration_baseline = reg_tt_result.scalar_one_or_none() is not None

    # Current state: what each student holds right now
    current_result = await db.execute(
        text("""
            SELECT DISTINCT s.id AS student_id, s.campus_id, cs.class_nbr
            FROM timetables t
            JOIN students s ON t.student_id = s.id
            JOIN timetable_items ti ON ti.timetable_id = t.id
            JOIN course_sections cs ON ti.course_section_id = cs.id
            WHERE t.status IN ('committed', 'edited')
        """)
    )
    current_by_student: dict[str, set[int]] = {}
    campus_by_student: dict[str, str] = {}
    for row in current_result.fetchall():
        student_id = str(row.student_id)
        current_by_student.setdefault(student_id, set()).add(row.class_nbr)
        campus_by_student[student_id] = row.campus_id

    original_by_student: dict[str, set[int]] = {}
    if has_registration_baseline:
        reg_result = await db.execute(
            text("""
                SELECT DISTINCT s.id AS student_id, s.campus_id, cs.class_nbr
                FROM registration_timetables rt
                JOIN students s ON rt.student_id = s.id
                JOIN registration_timetable_items rti ON rti.timetable_id = rt.id
                JOIN course_sections cs ON rti.course_section_id = cs.id
            """)
        )
        for row in reg_result.fetchall():
            student_id = str(row.student_id)
            original_by_student.setdefault(student_id, set()).add(row.class_nbr)
            campus_by_student.setdefault(student_id, row.campus_id)

    to_add: set[ExportRow] = set()
    to_delete: set[ExportRow] = set()

    for student_id in set(original_by_student) | set(current_by_student):
        campus_id = campus_by_student.get(student_id, "")
        original = original_by_student.get(student_id, set())
        current = current_by_student.get(student_id, set())

        for class_nbr in current - original:
            to_add.add(ExportRow(campus_id, class_nbr))
        for class_nbr in original - current:
            to_delete.add(ExportRow(campus_id, class_nbr))

    to_delete |= await collect_portal_removals(db, current_by_student)

    # A class can be queued for both only if it is somehow still held; current state wins
    to_delete -= to_add

    return to_add, to_delete


def write_erp_change_files(
    to_add: set[ExportRow],
    to_delete: set[ExportRow],
    session_name: str,
    term_code: str,
    career: str,
) -> tuple[Path, Path, Path]:
    """
    Write the add and delete ERP uploads as two separate workbooks plus a ZIP bundle.

    Returns:
        (add_path, delete_path, zip_path)
    """
    exports_dir = get_exports_dir()

    unique_id = generate_unique_id()
    safe_session_name = sanitize_filename(session_name).replace(" ", "_")
    base = f"timetables_{safe_session_name}_{unique_id}"

    add_filename = f"{base}_add.xlsx"
    delete_filename = f"{base}_delete.xlsx"
    zip_filename = f"{base}.zip"

    add_path = exports_dir / add_filename
    delete_path = exports_dir / delete_filename
    zip_path = exports_dir / zip_filename

    def _frame(rows: set[ExportRow]) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "Campus ID": row.campus_id,
                    "Career": career,
                    "Term": term_code,
                    "Class Nbr": row.class_nbr,
                }
                for row in sorted(rows)
            ],
            columns=ERP_EXPORT_COLUMNS,
        )

    _frame(to_add).to_excel(add_path, index=False, engine="xlsxwriter")
    _frame(to_delete).to_excel(delete_path, index=False, engine="xlsxwriter")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(add_path, add_filename)
        zf.write(delete_path, delete_filename)

    return add_path, delete_path, zip_path


async def export_timetables(
    db: AsyncSession, session_name: str, term_code: str, career: str
) -> Path:
    """
    Export the session's ERP changes as two workbooks bundled in a ZIP.

    The bundle holds one file of rows to add in ERP and one of rows to delete.
    Timetable transfers are excluded - they are exported separately at transfer time.

    Returns the ZIP path.
    """
    to_add, to_delete = await build_erp_change_sets(db)
    _add_path, _delete_path, zip_path = write_erp_change_files(
        to_add, to_delete, session_name, term_code, career
    )
    return zip_path


# Legacy aliases. All export paths now go through export_timetables, which decides
# for itself whether a registration baseline exists.
async def export_changes_diff(
    db: AsyncSession, session_name: str, term_code: str, career: str
) -> Path:
    """Export ERP changes (legacy name)"""
    return await export_timetables(db, session_name, term_code, career)


async def export_portal_timetables(
    db: AsyncSession, session_name: str, term_code: str, career: str
) -> Path:
    """Export ERP changes (legacy name)"""
    return await export_timetables(db, session_name, term_code, career)


async def export_all_timetables(
    db: AsyncSession, session_name: str, term_code: str, career: str
) -> Path:
    """Export ERP changes (legacy name)"""
    return await export_timetables(db, session_name, term_code, career)


async def export_changes(
    db: AsyncSession, session_name: str, term_code: str, career: str
) -> Path:
    """Export ERP changes (legacy name)"""
    return await export_timetables(db, session_name, term_code, career)


async def export_changes_new(
    db: AsyncSession, session_name: str, term_code: str, career: str
) -> Path:
    """Export ERP changes (legacy name)"""
    return await export_timetables(db, session_name, term_code, career)
