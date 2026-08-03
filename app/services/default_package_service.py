"""Service layer for default package management"""

import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.default_package import DefaultPackage
from app.schemas.default_package import DefaultPackageCreate, DefaultPackageUpdate


async def get_package_by_id(
    db: AsyncSession, package_id: uuid.UUID
) -> DefaultPackage | None:
    """Get a default package by ID"""
    result = await db.execute(
        select(DefaultPackage).where(DefaultPackage.id == package_id)
    )
    return result.scalar_one_or_none()


async def get_package_by_year_branch(
    db: AsyncSession, year: int, branch: str
) -> DefaultPackage | None:
    """Get a default package by year and branch"""
    result = await db.execute(
        select(DefaultPackage).where(
            DefaultPackage.year == year, DefaultPackage.branch == branch
        )
    )
    return result.scalar_one_or_none()


async def list_packages(
    db: AsyncSession,
    year: int | None = None,
    branch: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[DefaultPackage]:
    """
    List default packages with optional filters.

    Args:
        db: Database session
        year: Optional year filter
        branch: Optional branch filter (exact match)
        limit: Maximum number of results (default: 100)
        offset: Number of results to skip (default: 0)

    Returns:
        List of DefaultPackage objects
    """
    query = select(DefaultPackage).order_by(
        DefaultPackage.year.desc(), DefaultPackage.branch
    )

    if year is not None:
        query = query.where(DefaultPackage.year == year)
    if branch is not None:
        query = query.where(DefaultPackage.branch == branch)

    query = query.limit(limit).offset(offset)

    result = await db.execute(query)
    return list(result.scalars().all())


async def get_unique_years(db: AsyncSession) -> list[int]:
    """Get all unique years that have default packages"""
    result = await db.execute(
        select(DefaultPackage.year).distinct().order_by(DefaultPackage.year.desc())
    )
    return list(result.scalars().all())


async def get_unique_branches(db: AsyncSession, year: int | None = None) -> list[str]:
    """Get all unique branches, optionally filtered by year"""
    query = select(DefaultPackage.branch).distinct().order_by(DefaultPackage.branch)

    if year is not None:
        query = query.where(DefaultPackage.year == year)

    result = await db.execute(query)
    return list(result.scalars().all())


async def create_package(
    db: AsyncSession, package_data: DefaultPackageCreate
) -> DefaultPackage:
    """Create a new default package"""
    package = DefaultPackage(
        year=package_data.year,
        branch=package_data.branch,
        course_codes=package_data.course_codes,
    )
    db.add(package)
    await db.flush()
    await db.refresh(package)
    return package


async def update_package(
    db: AsyncSession, package_id: uuid.UUID, package_data: DefaultPackageUpdate
) -> DefaultPackage | None:
    """Update a default package"""
    package = await get_package_by_id(db, package_id)
    if not package:
        return None

    if package_data.year is not None:
        package.year = package_data.year
    if package_data.branch is not None:
        package.branch = package_data.branch
    if package_data.course_codes is not None:
        package.course_codes = package_data.course_codes

    await db.flush()
    await db.refresh(package)
    return package


async def delete_package(db: AsyncSession, package_id: uuid.UUID) -> bool:
    """Delete a default package"""
    package = await get_package_by_id(db, package_id)
    if not package:
        return False

    await db.delete(package)
    await db.flush()
    return True


async def delete_packages_by_year(db: AsyncSession, year: int) -> int:
    """Delete all packages for a given year. Returns number of deleted rows."""
    result = await db.execute(delete(DefaultPackage).where(DefaultPackage.year == year))
    await db.flush()
    return result.rowcount


async def upsert_packages(
    db: AsyncSession, packages_data: list[DefaultPackageCreate]
) -> tuple[int, int]:
    """
    Bulk upsert default packages.

    Uses PostgreSQL INSERT...ON CONFLICT to update existing packages
    or create new ones.

    Returns:
        Tuple of (inserted_count, updated_count)
    """
    if not packages_data:
        return (0, 0)

    # Prepare data for insert
    values = [
        {
            "year": pkg.year,
            "branch": pkg.branch,
            "course_codes": pkg.course_codes,
        }
        for pkg in packages_data
    ]

    # Use PostgreSQL INSERT...ON CONFLICT for upsert
    stmt = insert(DefaultPackage).values(values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_default_package_year_branch",
        set_={
            "course_codes": stmt.excluded.course_codes,
            "updated_at": stmt.excluded.updated_at,
        },
    )

    result = await db.execute(stmt)
    await db.flush()

    # PostgreSQL doesn't easily differentiate inserts vs updates in the result
    # So we return total rowcount and 0 for updates
    return (result.rowcount, 0)


async def parse_and_upsert_from_json(
    db: AsyncSession,
    json_data: dict[str, dict[str, list[str]]],
    overwrite: bool = False,
) -> tuple[int, int, list[str]]:
    """
    Parse JSON data and upsert default packages.

    JSON format: {"2025": {"A1, A2": ["COURSE1", "COURSE2"]}}

    Args:
        db: Database session
        json_data: Dictionary with year -> branches -> course_codes mapping
        overwrite: If True, delete all existing packages for affected years first

    Returns:
        Tuple of (total_packages, affected_years_count, years_list)
    """
    packages_to_create = []
    affected_years = set()

    # Parse JSON and split comma-separated branches
    for year_str, branches_dict in json_data.items():
        try:
            year = int(year_str)
            affected_years.add(year)

            for branches_str, course_codes in branches_dict.items():
                # Split comma-separated branches (e.g., "A1, A2, A3" -> ["A1", "A2", "A3"])
                branch_list = [b.strip() for b in branches_str.split(",") if b.strip()]

                for branch in branch_list:
                    packages_to_create.append(
                        DefaultPackageCreate(
                            year=year,
                            branch=branch,
                            course_codes=course_codes,
                        )
                    )
        except (ValueError, TypeError):
            continue

    # If overwrite mode, delete existing packages for affected years
    if overwrite:
        for year in affected_years:
            await delete_packages_by_year(db, year)

    # Upsert all packages
    inserted, updated = await upsert_packages(db, packages_to_create)

    return (len(packages_to_create), len(affected_years), sorted(affected_years))
