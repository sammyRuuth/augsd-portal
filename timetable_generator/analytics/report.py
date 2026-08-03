"""
Analytics report generation and display.

Provides rich console output and exportable reports.
"""

from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .diagnostics import AnalyticsSummary, TimetableAnalyzer


class AnalyticsReport:
    """
    Generates and displays analytics reports.

    Supports:
    - Rich console output with tables and panels
    - Quick summary view
    - Detailed comprehensive view
    - Export to file formats
    """

    def __init__(
        self,
        analyzer: TimetableAnalyzer,
        console: Optional[Console] = None,
    ):
        """
        Initialize report generator.

        Args:
            analyzer: TimetableAnalyzer instance
            console: Rich console for output
        """
        self.analyzer = analyzer
        self.console = console or Console()
        self._summary: Optional[AnalyticsSummary] = None

    @property
    def summary(self) -> AnalyticsSummary:
        """Get or compute analytics summary."""
        if self._summary is None:
            self._summary = self.analyzer.analyze()
        return self._summary

    def display_quick_summary(self) -> None:
        """Display a quick one-screen summary."""
        summary = self.summary

        self.console.print()
        self.console.rule("[bold blue]Quick Summary")
        self.console.print()

        # Overall status
        status_color = (
            "green"
            if summary.assignment_rate >= 100
            else "yellow"
            if summary.assignment_rate >= 90
            else "red"
        )
        status_emoji = (
            "✓"
            if summary.assignment_rate >= 100
            else "⚠"
            if summary.assignment_rate >= 90
            else "✗"
        )

        self.console.print(
            Panel(
                f"[bold {status_color}]{status_emoji} {summary.assigned_students}/{summary.total_students} students assigned ({summary.assignment_rate:.1f}%)[/]",
                title="Assignment Status",
                border_style=status_color,
            )
        )

        # Key metrics in a grid
        metrics_table = Table(show_header=False, box=None, padding=(0, 2))
        metrics_table.add_column("Metric", style="dim")
        metrics_table.add_column("Value", style="bold")

        metrics_table.add_row("Total Sections", str(summary.total_sections))
        metrics_table.add_row(
            "Overfilled Sections",
            f"[red]{summary.overfilled_sections}[/red]"
            if summary.overfilled_sections > 0
            else "0",
        )
        metrics_table.add_row(
            "Underutilized Sections",
            f"[yellow]{summary.underutilized_sections}[/yellow]"
            if summary.underutilized_sections > 0
            else "0",
        )
        metrics_table.add_row("Capacity Deficits", str(len(summary.deficits)))

        self.console.print(metrics_table)
        self.console.print()

        # Top issues
        if summary.issues:
            critical = [i for i in summary.issues if i.severity == "critical"]
            if critical:
                self.console.print("[bold red]Critical Issues:[/bold red]")
                for issue in critical[:3]:
                    self.console.print(
                        f"  • {issue.section.display_name}: {issue.description}"
                    )
                self.console.print()

        # Top suggestions
        if summary.suggestions:
            high_priority = [s for s in summary.suggestions if s.priority == "high"]
            if high_priority:
                self.console.print("[bold yellow]Priority Actions:[/bold yellow]")
                for sugg in high_priority[:3]:
                    self.console.print(f"  → {sugg.title}")
                self.console.print()

    def display_comprehensive(self) -> None:
        """Display comprehensive analytics report."""
        summary = self.summary

        self.console.print()
        self.console.rule("[bold blue]Comprehensive Analytics Report")

        # Section 1: Overall Summary
        self._display_overall_summary(summary)

        # Section 2: Plan Analysis
        self._display_plan_analysis(summary)

        # Section 3: Capacity Analysis
        self._display_capacity_analysis(summary)

        # Section 4: Section Issues
        self._display_section_issues(summary)

        # Section 5: Time Slot Analysis
        self._display_time_slot_analysis(summary)

        # Section 6: Suggestions
        self._display_suggestions(summary)

    def _display_overall_summary(self, summary: AnalyticsSummary) -> None:
        """Display overall summary section."""
        self.console.print()
        self.console.print("[bold]1. Overall Summary[/bold]")
        self.console.print()

        table = Table(show_header=False, box=None)
        table.add_column("Metric", style="dim", width=30)
        table.add_column("Value")

        table.add_row("Total Students", f"{summary.total_students:,}")
        table.add_row("Assigned Students", f"{summary.assigned_students:,}")
        table.add_row(
            "Unassigned Students",
            f"[red]{summary.unassigned_students:,}[/red]"
            if summary.unassigned_students > 0
            else "0",
        )
        table.add_row("Assignment Rate", f"{summary.assignment_rate:.1f}%")
        table.add_row("", "")
        table.add_row("Total Sections", f"{summary.total_sections:,}")
        table.add_row(
            "Overfilled Sections",
            f"[red]{summary.overfilled_sections}[/red]"
            if summary.overfilled_sections > 0
            else "0",
        )
        table.add_row("Underutilized Sections", f"{summary.underutilized_sections}")

        self.console.print(table)

    def _display_plan_analysis(self, summary: AnalyticsSummary) -> None:
        """Display plan-by-plan analysis."""
        self.console.print()
        self.console.print("[bold]2. Plan Analysis[/bold]")
        self.console.print()

        table = Table()
        table.add_column("Plan", style="cyan", max_width=40)
        table.add_column("Assigned", justify="right")
        table.add_column("Rate", justify="right")
        table.add_column("Timetables", justify="right")
        table.add_column("Status")

        for plan, data in sorted(summary.plan_summaries.items()):
            rate = data["assignment_rate"]
            rate_style = "green" if rate >= 100 else "yellow" if rate >= 90 else "red"
            status = "✓" if data["is_complete"] else "⚠"
            status_style = "green" if data["is_complete"] else "red"

            plan_display = plan[:37] + "..." if len(plan) > 40 else plan

            table.add_row(
                plan_display,
                f"{data['students_assigned']}/{data['students_needed']}",
                f"[{rate_style}]{rate:.1f}%[/{rate_style}]",
                str(data["timetables_generated"]),
                f"[{status_style}]{status}[/{status_style}]",
            )

        self.console.print(table)

    def _display_capacity_analysis(self, summary: AnalyticsSummary) -> None:
        """Display capacity deficit analysis."""
        if not summary.deficits:
            return

        self.console.print()
        self.console.print("[bold]3. Capacity Deficits[/bold]")
        self.console.print()

        table = Table()
        table.add_column("Course", style="cyan")
        table.add_column("Component")
        table.add_column("Current Cap", justify="right")
        table.add_column("Required", justify="right")
        table.add_column("Deficit", justify="right", style="red")
        table.add_column("Sections Needed", justify="right")

        for deficit in sorted(summary.deficits, key=lambda d: d.deficit, reverse=True):
            sections_needed = (deficit.deficit // 40) + 1

            table.add_row(
                deficit.course_code,
                deficit.component,
                str(deficit.current_capacity),
                str(deficit.required_capacity),
                f"-{deficit.deficit}",
                f"+{sections_needed}",
            )

        self.console.print(table)

    def _display_section_issues(self, summary: AnalyticsSummary) -> None:
        """Display section issues."""
        if not summary.issues:
            return

        self.console.print()
        self.console.print("[bold]4. Section Issues[/bold]")
        self.console.print()

        # Group by severity
        critical = [i for i in summary.issues if i.severity == "critical"]
        warnings = [i for i in summary.issues if i.severity == "warning"]

        if critical:
            self.console.print("[red]Critical Issues:[/red]")
            for issue in critical[:10]:
                self.console.print(
                    f"  • [bold]{issue.section.display_name}[/bold]: {issue.description}"
                )
                if issue.suggestion:
                    self.console.print(f"    → {issue.suggestion}", style="dim")
            if len(critical) > 10:
                self.console.print(
                    f"  ... and {len(critical) - 10} more critical issues"
                )
            self.console.print()

        if warnings:
            self.console.print("[yellow]Warnings:[/yellow]")
            for issue in warnings[:5]:
                self.console.print(
                    f"  • [bold]{issue.section.display_name}[/bold]: {issue.description}"
                )
            if len(warnings) > 5:
                self.console.print(f"  ... and {len(warnings) - 5} more warnings")

    def _display_time_slot_analysis(self, summary: AnalyticsSummary) -> None:
        """Display time slot utilization."""
        if not summary.time_slots:
            return

        self.console.print()
        self.console.print("[bold]5. Time Slot Utilization[/bold]")
        self.console.print()

        # Group by day
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        day_slots: dict[str, list] = {d: [] for d in days}

        for slot in summary.time_slots:
            if slot.day in day_slots:
                day_slots[slot.day].append(slot)

        # Show high-utilization slots
        high_util = [s for s in summary.time_slots if s.utilization > 0.7]
        low_util = [
            s
            for s in summary.time_slots
            if s.utilization < 0.3 and s.sections_count > 0
        ]

        if high_util:
            self.console.print("[bold]High Utilization Time Slots:[/bold]")
            for slot in sorted(high_util, key=lambda s: s.utilization, reverse=True)[
                :5
            ]:
                bar = "█" * int(slot.utilization * 10)
                self.console.print(
                    f"  {slot.day:10} {slot.slot}: {bar} ({slot.utilization:.0%}, {slot.total_students} students)"
                )
            self.console.print()

        if low_util:
            self.console.print("[bold]Available Time Slots (for new sections):[/bold]")
            for slot in sorted(low_util, key=lambda s: s.sections_count)[:5]:
                self.console.print(
                    f"  {slot.day:10} {slot.slot}: {slot.sections_count} sections"
                )

    def _display_suggestions(self, summary: AnalyticsSummary) -> None:
        """Display suggestions."""
        if not summary.suggestions:
            return

        self.console.print()
        self.console.print("[bold]6. Recommendations[/bold]")
        self.console.print()

        priority_colors = {"high": "red", "medium": "yellow", "low": "dim"}
        priority_icons = {"high": "🔴", "medium": "🟡", "low": "⚪"}

        for sugg in summary.suggestions:
            color = priority_colors.get(sugg.priority, "white")
            icon = priority_icons.get(sugg.priority, "•")

            self.console.print(f"{icon} [{color}][bold]{sugg.title}[/bold][/{color}]")
            self.console.print(f"   {sugg.description}", style="dim")
            self.console.print(f"   [green]Impact:[/green] {sugg.impact}")
            self.console.print()

    def export_to_json(self, path: Path) -> None:
        """Export analytics to JSON file."""
        import json

        summary = self.summary

        data = {
            "overall": {
                "total_students": summary.total_students,
                "assigned_students": summary.assigned_students,
                "unassigned_students": summary.unassigned_students,
                "assignment_rate": summary.assignment_rate,
                "total_sections": summary.total_sections,
                "overfilled_sections": summary.overfilled_sections,
                "underutilized_sections": summary.underutilized_sections,
            },
            "plans": summary.plan_summaries,
            "deficits": [
                {
                    "course": d.course_code,
                    "component": d.component,
                    "current_capacity": d.current_capacity,
                    "required_capacity": d.required_capacity,
                    "deficit": d.deficit,
                }
                for d in summary.deficits
            ],
            "issues": [
                {
                    "section": i.section.display_name,
                    "type": i.issue_type,
                    "severity": i.severity,
                    "description": i.description,
                    "suggestion": i.suggestion,
                }
                for i in summary.issues
            ],
            "suggestions": [
                {
                    "priority": s.priority,
                    "category": s.category,
                    "title": s.title,
                    "description": s.description,
                    "impact": s.impact,
                }
                for s in summary.suggestions
            ],
        }

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

        self.console.print(f"[green]Analytics exported to {path}[/green]")
