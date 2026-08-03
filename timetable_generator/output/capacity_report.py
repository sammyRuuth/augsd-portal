"""
Capacity report export for section utilization analysis.

Generates:
- Excel report with course/component/section breakdown
- PDF report with formatted tables
"""

from pathlib import Path
from typing import Optional

import xlsxwriter
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

from ..config import Config
from ..models import GenerationResult, Section


class CapacityReportExporter:
    """
    Exports section capacity utilization reports.

    Shows for each course:
    - Component-wise breakdown (LEC, TUT, LAB, etc.)
    - Per-section details (capacity, enrolled, allocated, final)
    - Comparison between initial capacity and final usage
    """

    def __init__(
        self,
        config: Config,
        sections_by_course: dict[str, list[Section]],
        result: GenerationResult,
        packages: dict[str, list[str]],
        console: Optional[Console] = None,
    ):
        """
        Initialize exporter.

        Args:
            config: Configuration object
            sections_by_course: All available sections
            result: Generation result with capacity usage
            packages: Package courses to filter by
            console: Rich console for output
        """
        self.config = config
        self.all_sections = sections_by_course
        self.result = result
        self.console = console or Console()
        self.packages = packages

    def _build_report_data(self) -> list[dict]:
        """Build structured report data."""
        from ..parsers.packages import find_course_match

        # Get package courses with matching
        package_courses = set()
        available = set(self.all_sections.keys())

        for course_list in self.packages.values():
            for course in course_list:
                course = course.strip()
                if course:
                    match = find_course_match(course, available)
                    if match:
                        package_courses.add(match)

        # Build report data
        report_data = []

        for course_code in sorted(package_courses):
            if course_code not in self.all_sections:
                continue

            sections = self.all_sections[course_code]

            # Group by component
            by_component: dict[str, list[Section]] = {}
            for section in sections:
                comp = section.component.value
                if comp not in by_component:
                    by_component[comp] = []
                by_component[comp].append(section)

            for component in sorted(by_component.keys()):
                comp_sections = sorted(by_component[component], key=lambda s: s.section)

                for section in comp_sections:
                    allocated = self.result.capacity_usage.get(section.class_nbr, 0)
                    total_used = section.enrolled + allocated
                    remaining = section.capacity - total_used

                    report_data.append({
                        "course_code": course_code,
                        "component": component,
                        "section": section.section,
                        "class_nbr": section.class_nbr,
                        "initial_capacity": section.capacity,
                        "enrolled": section.enrolled,
                        "allocated": allocated,
                        "total_used": total_used,
                        "remaining": remaining,
                        "fill_pct": (total_used / section.capacity * 100) if section.capacity > 0 else 0,
                        "instructor": section.instructor or "",
                    })

        return report_data

    def export_excel(self, path: Path) -> None:
        """
        Export capacity report to Excel using xlsxwriter.

        Args:
            path: Output file path
        """
        path.parent.mkdir(parents=True, exist_ok=True)

        workbook = xlsxwriter.Workbook(str(path))

        # Formats
        header_format = workbook.add_format({
            "bold": True,
            "font_color": "white",
            "bg_color": "#2E5090",
            "align": "center",
            "valign": "vcenter",
            "border": 1,
            "text_wrap": True,
        })

        cell_format = workbook.add_format({
            "align": "center",
            "valign": "vcenter",
            "border": 1,
        })

        cell_left_format = workbook.add_format({
            "align": "left",
            "valign": "vcenter",
            "border": 1,
        })

        overfill_format = workbook.add_format({
            "align": "center",
            "valign": "vcenter",
            "border": 1,
            "bg_color": "#FFCDD2",
            "font_color": "#B71C1C",
            "bold": True,
        })

        warning_format = workbook.add_format({
            "align": "center",
            "valign": "vcenter",
            "border": 1,
            "bg_color": "#FFF9C4",
        })

        good_format = workbook.add_format({
            "align": "center",
            "valign": "vcenter",
            "border": 1,
            "bg_color": "#C8E6C9",
        })

        number_format = workbook.add_format({
            "align": "center",
            "valign": "vcenter",
            "border": 1,
            "num_format": "0",
        })

        percent_format = workbook.add_format({
            "align": "center",
            "valign": "vcenter",
            "border": 1,
            "num_format": "0.0%",
        })

        # Sheet 1: Section Details
        ws = workbook.add_worksheet("Section Capacity Report")

        # Headers
        headers = [
            "Course Code",
            "Component",
            "Section",
            "Class Nbr",
            "Initial Capacity",
            "Already Enrolled",
            "Newly Allocated",
            "Total Used",
            "Remaining",
            "Fill %",
            "Status",
        ]

        for col, header in enumerate(headers):
            ws.write(0, col, header, header_format)

        # Data
        report_data = self._build_report_data()

        for row_idx, data in enumerate(report_data, 1):
            # Determine status and format
            if data["remaining"] < 0:
                status = "OVERFILLED"
                status_fmt = overfill_format
            elif data["fill_pct"] >= 90:
                status = "NEAR FULL"
                status_fmt = warning_format
            elif data["fill_pct"] >= 70:
                status = "OK"
                status_fmt = good_format
            else:
                status = "AVAILABLE"
                status_fmt = cell_format

            ws.write(row_idx, 0, data["course_code"], cell_left_format)
            ws.write(row_idx, 1, data["component"], cell_format)
            ws.write(row_idx, 2, data["section"], cell_format)
            ws.write(row_idx, 3, data["class_nbr"], number_format)
            ws.write(row_idx, 4, data["initial_capacity"], number_format)
            ws.write(row_idx, 5, data["enrolled"], number_format)
            ws.write(row_idx, 6, data["allocated"], number_format)
            ws.write(row_idx, 7, data["total_used"], number_format)

            # Remaining - special format if negative
            if data["remaining"] < 0:
                ws.write(row_idx, 8, data["remaining"], overfill_format)
            else:
                ws.write(row_idx, 8, data["remaining"], number_format)

            ws.write(row_idx, 9, data["fill_pct"] / 100, percent_format)
            ws.write(row_idx, 10, status, status_fmt)

        # Column widths
        col_widths = [18, 12, 10, 12, 16, 16, 16, 12, 12, 10, 14]
        for col, width in enumerate(col_widths):
            ws.set_column(col, col, width)

        # Freeze header row
        ws.freeze_panes(1, 0)

        # Sheet 2: Course Summary
        self._add_summary_sheet(workbook, report_data)

        workbook.close()
        self.console.print(f"[green]Excel capacity report saved to {path}[/green]")

    def _add_summary_sheet(self, workbook: xlsxwriter.Workbook, report_data: list[dict]) -> None:
        """Add a summary sheet with course-level totals."""
        ws = workbook.add_worksheet("Course Summary")

        # Formats
        header_format = workbook.add_format({
            "bold": True,
            "font_color": "white",
            "bg_color": "#2E5090",
            "align": "center",
            "valign": "vcenter",
            "border": 1,
        })

        cell_format = workbook.add_format({
            "align": "center",
            "valign": "vcenter",
            "border": 1,
        })

        cell_left_format = workbook.add_format({
            "align": "left",
            "valign": "vcenter",
            "border": 1,
        })

        number_format = workbook.add_format({
            "align": "center",
            "valign": "vcenter",
            "border": 1,
            "num_format": "0",
        })

        percent_format = workbook.add_format({
            "align": "center",
            "valign": "vcenter",
            "border": 1,
            "num_format": "0.0%",
        })

        deficit_format = workbook.add_format({
            "align": "center",
            "valign": "vcenter",
            "border": 1,
            "bg_color": "#FFCDD2",
            "font_color": "#B71C1C",
            "bold": True,
            "num_format": "0",
        })

        # Headers
        headers = [
            "Course Code",
            "Component",
            "Sections",
            "Total Capacity",
            "Total Enrolled",
            "Total Allocated",
            "Total Used",
            "Total Remaining",
            "Avg Fill %",
        ]

        for col, header in enumerate(headers):
            ws.write(0, col, header, header_format)

        # Aggregate by course+component
        aggregates: dict[tuple[str, str], dict] = {}
        for data in report_data:
            key = (data["course_code"], data["component"])
            if key not in aggregates:
                aggregates[key] = {
                    "sections": 0,
                    "capacity": 0,
                    "enrolled": 0,
                    "allocated": 0,
                    "used": 0,
                    "remaining": 0,
                }
            agg = aggregates[key]
            agg["sections"] += 1
            agg["capacity"] += data["initial_capacity"]
            agg["enrolled"] += data["enrolled"]
            agg["allocated"] += data["allocated"]
            agg["used"] += data["total_used"]
            agg["remaining"] += data["remaining"]

        # Write aggregates
        row_idx = 1
        for (course, component), agg in sorted(aggregates.items()):
            avg_fill = (agg["used"] / agg["capacity"]) if agg["capacity"] > 0 else 0

            ws.write(row_idx, 0, course, cell_left_format)
            ws.write(row_idx, 1, component, cell_format)
            ws.write(row_idx, 2, agg["sections"], number_format)
            ws.write(row_idx, 3, agg["capacity"], number_format)
            ws.write(row_idx, 4, agg["enrolled"], number_format)
            ws.write(row_idx, 5, agg["allocated"], number_format)
            ws.write(row_idx, 6, agg["used"], number_format)

            # Remaining - special format if negative
            if agg["remaining"] < 0:
                ws.write(row_idx, 7, agg["remaining"], deficit_format)
            else:
                ws.write(row_idx, 7, agg["remaining"], number_format)

            ws.write(row_idx, 8, avg_fill, percent_format)

            row_idx += 1

        # Column widths
        col_widths = [18, 12, 10, 16, 16, 16, 12, 16, 12]
        for col, width in enumerate(col_widths):
            ws.set_column(col, col, width)

        ws.freeze_panes(1, 0)

    def export_pdf(self, path: Path) -> None:
        """
        Export capacity report to PDF.

        Args:
            path: Output file path
        """
        path.parent.mkdir(parents=True, exist_ok=True)

        doc = SimpleDocTemplate(
            str(path),
            pagesize=landscape(A4),
            rightMargin=15 * mm,
            leftMargin=15 * mm,
            topMargin=15 * mm,
            bottomMargin=15 * mm,
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "TitleStyle",
            parent=styles["Heading1"],
            fontSize=18,
            spaceAfter=20,
            textColor=colors.Color(0.2, 0.2, 0.4),
        )

        subtitle_style = ParagraphStyle(
            "SubtitleStyle",
            parent=styles["Heading2"],
            fontSize=12,
            spaceAfter=10,
            textColor=colors.Color(0.3, 0.3, 0.5),
        )

        elements = []

        # Title
        elements.append(Paragraph("Section Capacity Report", title_style))
        elements.append(Spacer(1, 10))

        # Build report data
        report_data = self._build_report_data()

        # Group by course for PDF
        by_course: dict[str, list[dict]] = {}
        for data in report_data:
            course = data["course_code"]
            if course not in by_course:
                by_course[course] = []
            by_course[course].append(data)

        # Create tables for each course
        course_list = sorted(by_course.keys())
        for idx, course_code in enumerate(course_list):
            course_data = by_course[course_code]

            elements.append(Paragraph(f"{course_code}", subtitle_style))

            # Table data
            table_data = [["Comp", "Sec", "Class#", "Cap", "Enr", "Alloc", "Used", "Rem", "Fill%", "Status"]]

            for data in course_data:
                if data["remaining"] < 0:
                    status = "OVER"
                elif data["fill_pct"] >= 90:
                    status = "FULL"
                elif data["fill_pct"] >= 70:
                    status = "OK"
                else:
                    status = "AVAIL"

                table_data.append([
                    data["component"],
                    data["section"],
                    str(data["class_nbr"]),
                    str(data["initial_capacity"]),
                    str(data["enrolled"]),
                    str(data["allocated"]),
                    str(data["total_used"]),
                    str(data["remaining"]),
                    f"{data['fill_pct']:.0f}%",
                    status,
                ])

            # Create table
            col_widths = [40, 35, 50, 35, 35, 40, 40, 35, 40, 45]
            table = Table(table_data, colWidths=col_widths)

            # Style
            style_commands = [
                # Header
                ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.18, 0.31, 0.56)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 8),
                # Body
                ("FONTSIZE", (0, 1), (-1, -1), 7),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                # Grid
                ("GRID", (0, 0), (-1, -1), 0.5, colors.Color(0.7, 0.7, 0.7)),
                # Alternating rows
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.Color(0.95, 0.95, 0.95)]),
            ]

            # Color code status and remaining columns
            for row_idx, data in enumerate(course_data, 1):
                if data["remaining"] < 0:
                    style_commands.append(
                        ("BACKGROUND", (7, row_idx), (7, row_idx), colors.Color(1.0, 0.8, 0.82))
                    )
                    style_commands.append(
                        ("BACKGROUND", (9, row_idx), (9, row_idx), colors.Color(1.0, 0.8, 0.82))
                    )
                elif data["fill_pct"] >= 90:
                    style_commands.append(
                        ("BACKGROUND", (9, row_idx), (9, row_idx), colors.Color(1.0, 0.98, 0.76))
                    )

            table.setStyle(TableStyle(style_commands))
            elements.append(table)
            elements.append(Spacer(1, 15))

            # Page break every 3 courses to avoid overflow
            if idx % 3 == 2 and idx < len(course_list) - 1:
                elements.append(PageBreak())

        # Build PDF
        doc.build(elements)
        self.console.print(f"[green]PDF capacity report saved to {path}[/green]")

    def export_all(self, output_dir: Path) -> None:
        """
        Export both Excel and PDF capacity reports.

        Args:
            output_dir: Output directory
        """
        self.export_excel(output_dir / "capacity_report.xlsx")
        self.export_pdf(output_dir / "capacity_report.pdf")
