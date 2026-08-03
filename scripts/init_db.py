#!/usr/bin/env python
"""
Initialize the database and create default admin user.

Usage:
    uv run python scripts/init_db.py
"""
import app.models
import asyncio
import sys
from pathlib import Path

from rich import print as rprint
from rich.console import Console

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

console = Console()


async def init_database():
    """Initialize the database schema and create default admin"""
    from app.database import Base, engine

    console.rule("[bold blue]Initializing Database")

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    rprint("[green]✓ Database tables created[/]")

    # Check if admin exists
    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models.user import User

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.role == "admin").limit(1))
        admin = result.scalar_one_or_none()

        if admin:
            rprint(f"[cyan]Admin user already exists: {admin.email}[/]")
        else:
            rprint("[yellow]No admin user found. Creating default admin...[/]")

            # Create default admin using bcrypt directly
            import secrets
            import string

            import bcrypt

            # Generate password
            alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
            password = "".join(secrets.choice(alphabet) for _ in range(12))

            # Hash with bcrypt directly
            hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode(
                "utf-8"
            )

            admin = User(
                email="admin@augsd.bits-pilani.ac.in",
                password_hash=hashed,
                role="admin",
                is_active=True,
            )

            db.add(admin)
            await db.commit()

            console.print()
            rprint("[bold green]✓ Default admin created![/]")
            console.print()
            console.print("  [cyan]Email (login):[/] admin@augsd.bits-pilani.ac.in")
            console.print(f"  [bold yellow]Password:[/] {password}")
            console.print()
            rprint("[yellow]⚠ Save this password - it cannot be recovered![/]")

    await engine.dispose()


def main():
    console.rule("[bold magenta]AUGSD Portal - Database Initialization")

    try:
        asyncio.run(init_database())
        console.print()
        rprint("[bold green]Database initialization complete![/]")

    except Exception as e:
        rprint(f"[red]✗ Error: {e}[/]")
        rprint(
            "[yellow]Make sure PostgreSQL is running and DATABASE_URL is configured.[/]"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
