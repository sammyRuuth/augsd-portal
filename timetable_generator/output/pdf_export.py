"""
PDF export for timetables.

Generates clean, modern visual timetable grids using reportlab.
"""

from pathlib import Path
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

from ..config import Config
from ..models import GenerationResult, Timetable

# Modern color palette for courses
COURSE_COLORS = [
    colors.Color(0.93, 0.95, 0.98),  # Light blue
    colors.Color(0.93, 0.98, 0.93),  # Light green
    colors.Color(0.98, 0.95, 0.93),  # Light orange
    colors.Color(0.95, 0.93, 0.98),  # Light purple
    colors.Color(0.98, 0.93, 0.95),  # Light pink
    colors.Color(0.98, 0.98, 0.93),  # Light yellow
    colors.Color(0.93, 0.98, 0.98),  # Light cyan
    colors.Color(0.96, 0.94, 0.93),  # Light brown
]

# Time slots for the grid
TIME_SLOTS = [
    ("08:00", "09:00"),
    ("09:00", "10:00"),
    ("10:00", "11:00"),
    ("11:00", "12:00"),
    ("12:00", "13:00"),
    ("13:00", "14:00"),
    ("14:00", "15:00"),
    ("15:00", "16:00"),
    ("16:00", "17:00"),
    ("17:00", "18:00"),
]

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


class PDFExporter:
    """
    Exports timetables to PDF with clean visual grids.

    Features:
    - One page per timetable
    - Color-coded courses
    - Clean, modern design
    - Summary page with capacity info
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
        self.styles = getSampleStyleSheet()

        # Custom styles
        self.title_style = ParagraphStyle(
            "TitleStyle",
            parent=self.styles["Heading1"],
            fontSize=16,
            spaceAfter=12,
            textColor=colors.Color(0.2, 0.2, 0.4),
        )

        self.subtitle_style = ParagraphStyle(
            "SubtitleStyle",
            parent=self.styles["Normal"],
            fontSize=10,
            spaceAfter=8,
            textColor=colors.Color(0.4, 0.4, 0.4),
        )

        self.cell_style = ParagraphStyle(
            "CellStyle",
            parent=self.styles["Normal"],
            fontSize=7,
            leading=9,
            alignment=1,  # Center
        )

    def export(self, path: Optional[Path] = None) -> None:
        """
        Export all timetables to PDF.

        Args:
            path: Output file path (uses default if not specified)
        """
        if path is None:
            path = Path(self.config.output.output_dir) / "all_timetables.pdf"

        path.parent.mkdir(parents=True, exist_ok=True)

        # Count total timetables
        total = sum(len(a.timetables) for a in self.result.assignments.values())

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            console=self.console,
        ) as progress:
            task = progress.add_task(
                f"[cyan]Generating PDF ({total} timetables)...", total=total
            )

            # Build document
            doc = SimpleDocTemplate(
                str(path),
                pagesize=landscape(A4),
                rightMargin=15 * mm,
                leftMargin=15 * mm,
                topMargin=15 * mm,
                bottomMargin=15 * mm,
            )

            elements = []

            # Add title page
            elements.extend(self._create_title_page())

            # Add timetables
            for assignment in self.result.assignments.values():
                for timetable in assignment.timetables:
                    elements.extend(self._create_timetable_page(timetable))
                    progress.update(task, advance=1)

            # Add summary page
            elements.extend(self._create_summary_page())

            # Build PDF
            doc.build(elements)

        self.console.print(f"[green]PDF exported to {path}[/green]")

    def _create_title_page(self) -> list:
        """Create title page elements."""
        elements = []

        # Title
        elements.append(Paragraph("Timetable Allocation Report", self.title_style))
        elements.append(Spacer(1, 20))

        # Summary stats
        total_students = sum(a.student_count for a in self.result.assignments.values())
        assigned = self.result.total_students_assigned
        rate = self.result.overall_assignment_rate

        stats_data = [
            ["Total Students", f"{total_students:,}"],
            ["Assigned", f"{assigned:,}"],
            ["Assignment Rate", f"{rate:.1f}%"],
            ["Strategy Used", self.result.strategy_used],
            ["Total Timetables", str(len(self.result.all_timetables))],
        ]

        stats_table = Table(stats_data, colWidths=[150, 150])
        stats_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 11),
                    ("TEXTCOLOR", (0, 0), (0, -1), colors.Color(0.4, 0.4, 0.4)),
                    ("FONTNAME", (1, 0), (1, -1), "Helvetica-Bold"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )

        elements.append(stats_table)
        elements.append(PageBreak())

        return elements

    def _create_timetable_page(self, timetable: Timetable) -> list:
        """Create a single timetable page."""
        elements = []

        # Header
        variant_str = " (Variant)" if timetable.is_variant else ""
        title = f"{timetable.plan} - Timetable #{timetable.timetable_id}{variant_str}"
        elements.append(Paragraph(title, self.title_style))

        subtitle = f"Batch Size: {timetable.batch_size} students | Capacity Ceiling: {timetable.capacity_ceiling}"
        elements.append(Paragraph(subtitle, self.subtitle_style))
        elements.append(Spacer(1, 10))

        # Create grid
        grid = self._create_timetable_grid(timetable)
        elements.append(grid)
        elements.append(Spacer(1, 10))

        # Course legend
        legend = self._create_course_legend(timetable)
        elements.append(legend)

        elements.append(PageBreak())

        return elements

    def _create_timetable_grid(self, timetable: Timetable) -> Table:
        """Create the visual timetable grid."""
        # Initialize grid
        grid_data = [[""]]  # Top-left corner

        # Add time slot headers
        for start, end in TIME_SLOTS:
            grid_data[0].append(f"{start}\n{end}")

        # Assign colors to courses
        course_codes = list(set(s.course_code for s in timetable.sections))
        course_colors = {
            code: COURSE_COLORS[i % len(COURSE_COLORS)]
            for i, code in enumerate(course_codes)
        }

        # Build grid content
        cell_colors = {}  # (row, col) -> color

        for day_idx, day in enumerate(DAYS):
            row = [day[:3]]  # Day abbreviation

            for slot_idx, (slot_start, slot_end) in enumerate(TIME_SLOTS):
                cell_content = self._get_cell_content(
                    timetable, day, slot_start, slot_end
                )
                row.append(cell_content)

                # Store cell color if there's content
                if cell_content:
                    # Find course for this cell
                    for section in timetable.sections:
                        for meeting in section.meetings:
                            if meeting.day == day:
                                start_hour = int(meeting.start.split(":")[0])
                                slot_hour = int(slot_start.split(":")[0])
                                if start_hour == slot_hour:
                                    cell_colors[(day_idx + 1, slot_idx + 1)] = (
                                        course_colors.get(
                                            section.course_code,
                                            colors.white,
                                        )
                                    )

            grid_data.append(row)

        # Create table
        col_widths = [40] + [65] * len(TIME_SLOTS)
        table = Table(
            grid_data, colWidths=col_widths, rowHeights=[25] + [50] * len(DAYS)
        )

        # Base style
        style_commands = [
            # Header row
            ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.2, 0.3, 0.5)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            # Day column
            ("BACKGROUND", (0, 1), (0, -1), colors.Color(0.9, 0.9, 0.95)),
            ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 1), (0, -1), 9),
            # All cells
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("FONTSIZE", (1, 1), (-1, -1), 7),
            # Grid lines
            ("GRID", (0, 0), (-1, -1), 0.5, colors.Color(0.7, 0.7, 0.7)),
            ("BOX", (0, 0), (-1, -1), 1, colors.Color(0.3, 0.3, 0.3)),
        ]

        # Add cell colors
        for (row, col), color in cell_colors.items():
            style_commands.append(("BACKGROUND", (col, row), (col, row), color))

        table.setStyle(TableStyle(style_commands))

        return table

    def _get_cell_content(
        self,
        timetable: Timetable,
        day: str,
        slot_start: str,
        slot_end: str,
    ) -> str:
        """Get content for a grid cell."""
        slot_start_hour = int(slot_start.split(":")[0])
        contents = []

        for section in timetable.sections:
            for meeting in section.meetings:
                if meeting.day != day:
                    continue

                try:
                    start_hour = int(meeting.start.split(":")[0])
                    if start_hour == slot_start_hour:
                        # Format: COURSE\nCOMP-SEC
                        content = f"{section.course_code}\n{section.component.value}-{section.section}"
                        contents.append(content)
                except (ValueError, IndexError):
                    continue

        return "\n".join(contents)

    def _create_course_legend(self, timetable: Timetable) -> Table:
        """Create course legend."""
        course_codes = sorted(set(s.course_code for s in timetable.sections))
        course_colors = {
            code: COURSE_COLORS[i % len(COURSE_COLORS)]
            for i, code in enumerate(course_codes)
        }

        # Build legend data (2 columns)
        legend_data = []
        row = []

        for i, code in enumerate(course_codes):
            row.extend(["", code])
            if len(row) >= 8 or i == len(course_codes) - 1:
                legend_data.append(row)
                row = []

        if not legend_data:
            return Table([[""]])

        # Create table
        table = Table(legend_data, colWidths=[15, 80] * 4)

        style_commands = [
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]

        # Add color boxes
        for row_idx, row in enumerate(legend_data):
            for col_idx in range(0, len(row), 2):
                if col_idx + 1 < len(row) and row[col_idx + 1]:
                    code = row[col_idx + 1]
                    if code in course_colors:
                        style_commands.append(
                            (
                                "BACKGROUND",
                                (col_idx, row_idx),
                                (col_idx, row_idx),
                                course_colors[code],
                            )
                        )

        table.setStyle(TableStyle(style_commands))

        return table

    def _create_summary_page(self) -> list:
        """Create summary page with allocation overview."""
        elements = []

        elements.append(Paragraph("Allocation Summary", self.title_style))
        elements.append(Spacer(1, 15))

        # Plan summary table
        summary_data = [["Plan", "Assigned", "Needed", "Rate", "Timetables"]]

        for plan, assignment in sorted(self.result.assignments.items()):
            rate = assignment.assignment_rate
            summary_data.append(
                [
                    plan[:35] + "..." if len(plan) > 38 else plan,
                    str(assignment.students_assigned),
                    str(assignment.student_count),
                    f"{rate:.1f}%",
                    str(len(assignment.timetables)),
                ]
            )

        table = Table(summary_data, colWidths=[200, 60, 60, 60, 70])
        table.setStyle(
            TableStyle(
                [
                    # Header
                    ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.2, 0.3, 0.5)),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 10),
                    # Body
                    ("FONTSIZE", (0, 1), (-1, -1), 9),
                    ("ALIGN", (1, 0), (-1, -1), "CENTER"),
                    # Alternating rows
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.Color(0.95, 0.95, 0.95)],
                    ),
                    # Grid
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.Color(0.7, 0.7, 0.7)),
                    ("BOX", (0, 0), (-1, -1), 1, colors.Color(0.3, 0.3, 0.3)),
                ]
            )
        )

        elements.append(table)

        return elements
