"""
CSV export for timetables.

Generates:
- Summary CSV with all timetables
- Per-plan CSV files
- Class numbers registry
"""

import csv
from pathlib import Path
from typing import Optional

from rich.console import Console

from ..config import Config
from ..models import GenerationResult, Timetable


class CSVExporter:
    """
    Exports timetables to CSV files.

    Generates:
    - timetables_summary.csv: All timetables with full details
    - timetables_classnbrs.csv: Class number registry for registration
    - timetable_{plan}.csv: Individual plan files
    """

    def __init__(
        self,
        config: Config,
        result: GenerationResult,
        console: Optional[Console] = None,
    ):
        """
        Initialize exporter.

        Args:
            config: Configuration object
            result: Generation result to export
            console: Rich console for output
        """
        self.config = config
        self.result = result
        self.console = console or Console()

    def export_all(self, output_dir: Optional[Path] = None) -> None:
        """
        Export all CSV files.

        Args:
            output_dir: Output directory (uses config default if not specified)
        """
        if output_dir is None:
            output_dir = Path(self.config.output.output_dir)

        output_dir.mkdir(parents=True, exist_ok=True)

        # Export summary
        self.export_summary(output_dir / "timetables_summary.csv")

        # Export class numbers
        self.export_class_numbers(output_dir / "timetables_classnbrs.csv")

        # Export per-plan files
        if self.config.output.generate_plan_csvs:
            self.export_per_plan(output_dir)

        self.console.print(f"[green]CSV files exported to {output_dir}[/green]")

    def export_summary(self, path: Path) -> None:
        """Export summary CSV with all timetables."""
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "Plan",
                    "Timetable ID",
                    "Batch Size",
                    "Capacity Ceiling",
                    "Variant",
                    "Course Code",
                    "Component",
                    "Section",
                    "Class Nbr",
                    "Day",
                    "Start",
                    "End",
                    "Room",
                    "Instructor",
                    "Capacity",
                    "Enrolled",
                ]
            )

            for assignment in self.result.assignments.values():
                for timetable in assignment.timetables:
                    self._write_timetable_rows(writer, timetable)

    def export_class_numbers(self, path: Path) -> None:
        """Export class numbers registry."""
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "Plan",
                    "Timetable ID",
                    "Class Nbr",
                    "Course Code",
                    "Section",
                    "Component",
                ]
            )

            for assignment in self.result.assignments.values():
                for timetable in assignment.timetables:
                    seen = set()
                    for section in timetable.sections:
                        if section.class_nbr not in seen:
                            seen.add(section.class_nbr)
                            writer.writerow(
                                [
                                    timetable.plan,
                                    timetable.timetable_id,
                                    section.class_nbr,
                                    section.course_code,
                                    section.section,
                                    section.component.value,
                                ]
                            )

    def export_per_plan(self, output_dir: Path) -> None:
        """Export individual CSV files per plan."""
        for assignment in self.result.assignments.values():
            if not assignment.timetables:
                continue

            # Create safe filename
            safe_plan = (
                assignment.plan.replace(",", "_")
                .replace(" ", "")
                .replace(":", "_")
                .replace("+", "_")
            )
            path = output_dir / f"timetable_{safe_plan}.csv"

            with open(path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        "Timetable ID",
                        "Batch Size",
                        "Capacity Ceiling",
                        "Variant",
                        "Class Nbr",
                        "Course Code",
                        "Component",
                        "Section",
                        "Day",
                        "Start",
                        "End",
                        "Room",
                    ]
                )

                for timetable in assignment.timetables:
                    for section in timetable.sections:
                        if section.meetings:
                            for meeting in section.meetings:
                                writer.writerow(
                                    [
                                        timetable.timetable_id,
                                        timetable.batch_size,
                                        timetable.capacity_ceiling,
                                        "yes" if timetable.is_variant else "no",
                                        section.class_nbr,
                                        section.course_code,
                                        section.component.value,
                                        section.section,
                                        meeting.day,
                                        meeting.start,
                                        meeting.end,
                                        meeting.room or "",
                                    ]
                                )
                        else:
                            # Section with no meetings
                            writer.writerow(
                                [
                                    timetable.timetable_id,
                                    timetable.batch_size,
                                    timetable.capacity_ceiling,
                                    "yes" if timetable.is_variant else "no",
                                    section.class_nbr,
                                    section.course_code,
                                    section.component.value,
                                    section.section,
                                    "Online/Self-Study",
                                    "",
                                    "",
                                    "",
                                ]
                            )

    def _write_timetable_rows(
        self,
        writer: csv.writer,
        timetable: Timetable,
    ) -> None:
        """Write rows for a single timetable."""
        for section in timetable.sections:
            if section.meetings:
                for meeting in section.meetings:
                    writer.writerow(
                        [
                            timetable.plan,
                            timetable.timetable_id,
                            timetable.batch_size,
                            timetable.capacity_ceiling,
                            "yes" if timetable.is_variant else "no",
                            section.course_code,
                            section.component.value,
                            section.section,
                            section.class_nbr,
                            meeting.day,
                            meeting.start,
                            meeting.end,
                            meeting.room or "",
                            section.instructor or "",
                            section.capacity,
                            section.enrolled,
                        ]
                    )
            else:
                # Section with no meeting times
                writer.writerow(
                    [
                        timetable.plan,
                        timetable.timetable_id,
                        timetable.batch_size,
                        timetable.capacity_ceiling,
                        "yes" if timetable.is_variant else "no",
                        section.course_code,
                        section.component.value,
                        section.section,
                        section.class_nbr,
                        "Online/Self-Study",
                        "",
                        "",
                        "",
                        section.instructor or "",
                        section.capacity,
                        section.enrolled,
                    ]
                )
