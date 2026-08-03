"""
Command-line interface for the timetable generator.

Provides a rich, user-friendly CLI with progress bars and
organized output.

uv run python -m timetable_generator.cli \
    --packages data/2025-2/defualt_packages_2025-2.json \
    --count data/2025-2/count.csv \
    --timetable data/2025-2/6-1-26/BITS_TIME_TABLE_WITHFACILITY_06012025_modified.xlsx \
    --output exports/t1
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from .analytics import AnalyticsReport, TimetableAnalyzer
from .config import Config, load_config, save_config
from .generator import TimetableOptimizer
from .output import CSVExporter, PDFExporter, CapacityReportExporter
from .parsers import (
    group_plans_by_packages,
    parse_counts,
    parse_excel_timetable,
    parse_packages,
)
from .parsers.counts import match_plan_to_count


def create_parser() -> argparse.ArgumentParser:
    """Create argument parser."""
    parser = argparse.ArgumentParser(
        description="Generate bulk student timetables with optimal allocation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --packages data/packages.json --count data/count.csv --timetable data/timetable.xlsx
  %(prog)s --config config.yaml
  %(prog)s --packages data/packages.json --count data/count.csv --timetable data/timetable.xlsx --output exports/run1
        """,
    )

    # Input files
    parser.add_argument(
        "--packages",
        type=Path,
        help="Path to packages.json file",
    )
    parser.add_argument(
        "--count",
        type=Path,
        help="Path to count.csv file",
    )
    parser.add_argument(
        "--timetable",
        type=Path,
        help="Path to Excel timetable file",
    )

    # Configuration
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to YAML configuration file",
    )
    parser.add_argument(
        "--save-config",
        type=Path,
        help="Save current configuration to file",
    )

    # Output options
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Output directory for generated files",
    )
    parser.add_argument(
        "--no-pdf",
        action="store_true",
        help="Skip PDF generation",
    )

    # Algorithm options
    parser.add_argument(
        "--strategies",
        type=int,
        default=25,
        help="Number of strategies to test (default: 25)",
    )
    parser.add_argument(
        "--min-timetables",
        type=int,
        default=10,
        help="Minimum timetables per plan (default: 10)",
    )

    # Capacity overrides
    parser.add_argument(
        "--max-tut-overfill",
        type=int,
        help="Maximum tutorial overfill",
    )
    parser.add_argument(
        "--max-lab-overfill",
        type=int,
        help="Maximum lab overfill",
    )

    # Output control
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Minimal output",
    )
    parser.add_argument(
        "--analytics",
        choices=["quick", "full", "both"],
        default="both",
        help="Analytics detail level (default: both)",
    )

    return parser


def main(args: Optional[list[str]] = None) -> int:
    """Main entry point."""
    parser = create_parser()
    opts = parser.parse_args(args)

    console = Console()

    # Load configuration
    config = load_config(opts.config)

    # Apply command-line overrides
    if opts.output:
        config.output.output_dir = str(opts.output)
    if opts.no_pdf:
        config.output.generate_pdf = False
    if opts.strategies:
        config.generator.num_strategies = opts.strategies
    if opts.min_timetables:
        config.generator.min_timetables_per_plan = opts.min_timetables
    if opts.max_tut_overfill is not None:
        config.capacity.max_overfill["TUT"] = opts.max_tut_overfill
    if opts.max_lab_overfill is not None:
        config.capacity.max_overfill["LAB"] = opts.max_lab_overfill

    # Save config if requested
    if opts.save_config:
        save_config(config, opts.save_config)
        console.print(f"[green]Configuration saved to {opts.save_config}[/green]")
        return 0

    # Validate required inputs
    if not opts.packages or not opts.count or not opts.timetable:
        console.print(
            "[red]Error: --packages, --count, and --timetable are required[/red]"
        )
        parser.print_help()
        return 1

    # Check files exist
    for path, name in [
        (opts.packages, "packages"),
        (opts.count, "count"),
        (opts.timetable, "timetable"),
    ]:
        if not path.exists():
            console.print(f"[red]Error: {name} file not found: {path}[/red]")
            return 1

    try:
        # Run generation
        return run_generation(opts, config, console)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        return 130
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        if opts.verbose:
            console.print_exception()
        return 1


def run_generation(opts: argparse.Namespace, config: Config, console: Console) -> int:
    """Run the timetable generation process."""
    # Header
    console.print()
    console.print(
        Panel.fit(
            "[bold blue]Timetable Generator v2.0[/bold blue]\n"
            "[dim]Modular bulk timetable generation with optimization[/dim]",
            border_style="blue",
        )
    )
    console.print()

    # Phase 1: Load data
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Loading data...", total=3)

        # Load packages
        progress.update(task, description="[cyan]Loading packages...")
        packages = parse_packages(opts.packages)
        progress.update(task, advance=1)

        # Load counts
        progress.update(task, description="[cyan]Loading student counts...")
        counts = parse_counts(opts.count)
        progress.update(task, advance=1)

        # Load timetable
        progress.update(task, description="[cyan]Loading timetable Excel...")
        sections_by_course, course_id_map = parse_excel_timetable(
            opts.timetable, config
        )
        progress.update(task, advance=1)

    # Show data summary
    if not opts.quiet:
        _display_data_summary(console, packages, counts, sections_by_course)

    # Group plans by package
    grouped_packages, grouped_counts, membership = group_plans_by_packages(
        packages, counts
    )

    # Match counts to grouped packages
    for plan in grouped_packages:
        if plan not in grouped_counts or grouped_counts[plan] == 0:
            matched_count = match_plan_to_count(plan, counts)
            if matched_count > 0:
                grouped_counts[plan] = matched_count

    # Phase 2: Optimize
    console.print()
    optimizer = TimetableOptimizer(
        config=config,
        packages=grouped_packages,
        counts=grouped_counts,
        sections_by_course=sections_by_course,
        console=console,
    )

    result = optimizer.optimize()

    # Phase 3: Analytics
    console.print()
    console.rule("[bold blue]Analytics")

    analyzer = TimetableAnalyzer(
        config=config,
        sections_by_course=sections_by_course,
        result=result,
        counts=grouped_counts,
        packages=grouped_packages,
    )

    report = AnalyticsReport(analyzer, console)

    if opts.analytics in ("quick", "both"):
        report.display_quick_summary()

    if opts.analytics in ("full", "both"):
        report.display_comprehensive()

    # Phase 4: Export
    console.print()
    console.rule("[bold blue]Export")

    output_dir = Path(config.output.output_dir)

    # CSV export
    csv_exporter = CSVExporter(config, result, console)
    csv_exporter.export_all(output_dir)

    # PDF export
    if config.output.generate_pdf:
        pdf_exporter = PDFExporter(config, result, console)
        pdf_exporter.export(output_dir / "all_timetables.pdf")

    # Capacity report (Excel + PDF)
    capacity_exporter = CapacityReportExporter(
        config=config,
        sections_by_course=sections_by_course,
        result=result,
        packages=grouped_packages,
        console=console,
    )
    capacity_exporter.export_all(output_dir)

    # Export analytics JSON
    report.export_to_json(output_dir / "analytics.json")

    # Final summary
    console.print()
    _display_final_summary(console, result)

    return 0 if result.overall_assignment_rate >= 100 else 1


def _display_data_summary(
    console: Console,
    packages: dict,
    counts: dict,
    sections_by_course: dict,
) -> None:
    """Display summary of loaded data."""
    table = Table(title="Data Summary", show_header=False, box=None)
    table.add_column("Item", style="dim")
    table.add_column("Value", style="bold")

    table.add_row("Plans loaded", str(len(packages)))
    table.add_row("Total students", f"{sum(counts.values()):,}")
    table.add_row("Courses available", str(len(sections_by_course)))
    table.add_row(
        "Total sections", str(sum(len(s) for s in sections_by_course.values()))
    )

    console.print(table)


def _display_final_summary(console: Console, result) -> None:
    """Display final summary."""
    rate = result.overall_assignment_rate
    status_style = "green" if rate >= 100 else "yellow" if rate >= 90 else "red"
    status = "SUCCESS" if rate >= 100 else "PARTIAL" if rate >= 90 else "FAILED"

    console.print(
        Panel(
            f"[bold {status_style}]{status}[/bold {status_style}]\n\n"
            f"Assigned: {result.total_students_assigned:,} / {result.total_students_needed:,}\n"
            f"Rate: {rate:.1f}%\n"
            f"Timetables: {len(result.all_timetables)}",
            title="Generation Complete",
            border_style=status_style,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
