#!/usr/bin/env python
"""
CLI Tool for User Management

Create admin and staff users from the command line.

Usage:
    uv run python scripts/manage_users.py create --role admin --username admin --email admin@example.com
    uv run python scripts/manage_users.py create --role staff --username staff1 --email staff1@example.com
    uv run python scripts/manage_users.py list
    uv run python scripts/manage_users.py reset-password --username admin
    uv run python scripts/manage_users.py create-from-env  # Create users from DEFAULT_USERS env var
"""

import asyncio
import os
import secrets
import string
import sys
from pathlib import Path

import click
from rich import print as rprint
from rich.console import Console

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

console = Console()


def load_env_file():
    """Load environment variables from .env file."""
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    # Only set if not already in environment
                    if key.strip() not in os.environ:
                        os.environ[key.strip()] = value.strip()


def generate_password(length: int = 12) -> str:
    """Generate a secure random password"""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def create_user_async(
    username: str, email: str, role: str, password: str | None = None
):
    """Create a user in the database"""
    import bcrypt
    from sqlalchemy import select

    from app.database import AsyncSessionLocal, Base, engine
    from app.models.user import User

    # Create tables if they don't exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Generate password if not provided
    if not password:
        password = generate_password()

    # Hash password using bcrypt directly
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode(
        "utf-8"
    )

    async with AsyncSessionLocal() as db:
        # Check if user already exists (use email as identifier)
        result = await db.execute(select(User).where(User.email == email))
        existing = result.scalar_one_or_none()

        if existing:
            raise ValueError(f"Email '{email}' already exists")

        # Create user (note: model uses email, not username)
        user = User(
            email=email,
            password_hash=password_hash,
            role=role,
            is_active=True,
        )

        db.add(user)
        await db.commit()
        await db.refresh(user)

        return user, password


@click.command()
@click.option(
    "--role", type=click.Choice(["admin", "staff"]), required=True, help="User role"
)
@click.option(
    "--username", required=False, help="Username (not used - email is identifier)"
)
@click.option("--email", required=True, help="User email address (used as login)")
@click.option(
    "--password", default=None, help="Password (auto-generated if not provided)"
)
def create_user(role: str, username: str | None, email: str, password: str | None):
    """Create a new admin or staff user"""
    console.rule(f"[bold blue]Creating {role.upper()} User")

    try:
        user, plain_password = asyncio.run(
            create_user_async(username or "", email, role, password)
        )

        console.print()
        rprint("[bold green]✓ User created successfully![/]")
        console.print()
        console.print(f"  [cyan]Email (login):[/] {user.email}")
        console.print(f"  [cyan]Role:[/] {user.role}")
        console.print(f"  [cyan]User ID:[/] {user.id}")
        console.print()
        console.print(f"  [bold yellow]Password:[/] {plain_password}")
        console.print()
        rprint("[yellow]⚠ Save this password - it cannot be recovered![/]")

    except ValueError as e:
        rprint(f"[red]✗ Error: {e}[/]")
        sys.exit(1)
    except Exception as e:
        rprint(f"[red]✗ Database error: {e}[/]")
        rprint("[yellow]Make sure PostgreSQL is running and the database exists.[/]")
        sys.exit(1)


@click.command()
def list_users():
    """List all users"""

    async def list_users_async():
        from sqlalchemy import select

        from app.database import AsyncSessionLocal, Base, engine
        from app.models.user import User

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).order_by(User.created_at))
            return result.scalars().all()

    console.rule("[bold blue]Users List")

    try:
        users = asyncio.run(list_users_async())

        if not users:
            rprint("[yellow]No users found.[/]")
            return

        from rich.table import Table

        table = Table()
        table.add_column("ID", style="dim")
        table.add_column("Email", style="cyan")
        table.add_column("Role", style="green")
        table.add_column("Active")
        table.add_column("Created")

        for user in users:
            table.add_row(
                str(user.id)[:8] + "...",
                user.email,
                user.role,
                "✓" if user.is_active else "✗",
                user.created_at.strftime("%Y-%m-%d %H:%M") if user.created_at else "",
            )

        console.print(table)
        console.print(f"\n[cyan]Total: {len(users)} users[/]")

    except Exception as e:
        rprint(f"[red]✗ Error: {e}[/]")
        sys.exit(1)


@click.command()
@click.option("--email", required=True, help="Email of user to reset password for")
@click.option(
    "--password", default=None, help="New password (auto-generated if not provided)"
)
def reset_password(email: str, password: str | None):
    """Reset a user's password"""

    async def reset_password_async():
        import bcrypt
        from sqlalchemy import select

        from app.database import AsyncSessionLocal
        from app.models.user import User

        new_password = password or generate_password()
        hashed = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode(
            "utf-8"
        )

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.email == email))
            user = result.scalar_one_or_none()

            if not user:
                raise ValueError(f"User with email '{email}' not found")

            user.password_hash = hashed
            await db.commit()

            return user, new_password

    console.rule(f"[bold blue]Reset Password for {email}")

    try:
        user, new_password = asyncio.run(reset_password_async())

        console.print()
        rprint("[bold green]✓ Password reset successfully![/]")
        console.print()
        console.print(f"  [cyan]Email:[/] {user.email}")
        console.print(f"  [bold yellow]New Password:[/] {new_password}")
        console.print()
        rprint("[yellow]⚠ Save this password - it cannot be recovered![/]")

    except ValueError as e:
        rprint(f"[red]✗ Error: {e}[/]")
        sys.exit(1)
    except Exception as e:
        rprint(f"[red]✗ Database error: {e}[/]")
        sys.exit(1)


@click.command()
@click.option("--email", required=True, help="Email of user to deactivate")
def deactivate_user(email: str):
    """Deactivate a user account"""

    async def deactivate_async():
        from sqlalchemy import select

        from app.database import AsyncSessionLocal
        from app.models.user import User

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.email == email))
            user = result.scalar_one_or_none()

            if not user:
                raise ValueError(f"User with email '{email}' not found")

            user.is_active = False
            await db.commit()

            return user

    console.rule(f"[bold blue]Deactivate User: {email}")

    try:
        user = asyncio.run(deactivate_async())
        rprint(f"[green]✓ User '{user.email}' has been deactivated.[/]")

    except ValueError as e:
        rprint(f"[red]✗ Error: {e}[/]")
        sys.exit(1)
    except Exception as e:
        rprint(f"[red]✗ Database error: {e}[/]")
        sys.exit(1)


@click.command()
@click.option("--email", required=True, help="Email of user to activate")
def activate_user(email: str):
    """Activate a user account"""

    async def activate_async():
        from sqlalchemy import select

        from app.database import AsyncSessionLocal
        from app.models.user import User

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.email == email))
            user = result.scalar_one_or_none()

            if not user:
                raise ValueError(f"User with email '{email}' not found")

            user.is_active = True
            await db.commit()

            return user

    console.rule(f"[bold blue]Activate User: {email}")

    try:
        user = asyncio.run(activate_async())
        rprint(f"[green]✓ User '{user.email}' has been activated.[/]")

    except ValueError as e:
        rprint(f"[red]✗ Error: {e}[/]")
        sys.exit(1)
    except Exception as e:
        rprint(f"[red]✗ Database error: {e}[/]")
        sys.exit(1)


@click.group()
def cli():
    """AUGSD Portal - User Management CLI

    Manage admin and staff users for the portal.

    Examples:

        # Create an admin user
        uv run python scripts/manage_users.py create --role admin --username admin --email admin@bits.edu

        # Create a staff user
        uv run python scripts/manage_users.py create --role staff --username staff1 --email staff1@bits.edu

        # List all users
        uv run python scripts/manage_users.py list

        # Reset a user's password
        uv run python scripts/manage_users.py reset-password --email admin@bits.edu

        # Create default users from .env
        uv run python scripts/manage_users.py create-from-env
    """
    # Load .env file on CLI initialization
    load_env_file()


@click.command()
@click.option(
    "--skip-existing",
    is_flag=True,
    default=True,
    help="Skip users that already exist (default: True)",
)
def create_from_env(skip_existing: bool):
    """Create default users from DEFAULT_USERS environment variable.

    The DEFAULT_USERS env var should be in format:
    email:password:role,email2:password2:role2

    Example:
        DEFAULT_USERS=admin@example.com:password123:admin,staff@example.com:pass456:staff
    """

    async def create_users_from_env_async():
        import bcrypt
        from sqlalchemy import select

        from app.database import AsyncSessionLocal, Base, engine
        from app.models.user import User

        # Create tables if they don't exist
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        default_users = os.environ.get("DEFAULT_USERS", "")
        if not default_users:
            return [], []

        created = []
        skipped = []

        async with AsyncSessionLocal() as db:
            for user_spec in default_users.split(","):
                user_spec = user_spec.strip()
                if not user_spec:
                    continue

                parts = user_spec.split(":")
                if len(parts) != 3:
                    rprint(
                        f"[yellow]⚠ Invalid user spec '{user_spec}' - expected email:password:role[/]"
                    )
                    continue

                email, password, role = parts

                if role not in ["admin", "staff"]:
                    rprint(
                        f"[yellow]⚠ Invalid role '{role}' for {email} - must be 'admin' or 'staff'[/]"
                    )
                    continue

                # Check if user already exists
                result = await db.execute(select(User).where(User.email == email))
                existing = result.scalar_one_or_none()

                if existing:
                    if skip_existing:
                        skipped.append(email)
                        continue
                    else:
                        raise ValueError(f"User '{email}' already exists")

                # Hash password
                password_hash = bcrypt.hashpw(
                    password.encode("utf-8"), bcrypt.gensalt()
                ).decode("utf-8")

                # Create user
                user = User(
                    email=email,
                    password_hash=password_hash,
                    role=role,
                    is_active=True,
                )

                db.add(user)
                created.append((email, role))

            await db.commit()

        return created, skipped

    console.rule("[bold blue]Creating Users from Environment")

    try:
        default_users = os.environ.get("DEFAULT_USERS", "")
        if not default_users:
            rprint("[yellow]No DEFAULT_USERS environment variable found.[/]")
            rprint(
                "[cyan]Set DEFAULT_USERS=email:password:role,email2:password2:role2 in .env[/]"
            )
            return

        created, skipped = asyncio.run(create_users_from_env_async())

        console.print()
        if created:
            rprint(f"[bold green]✓ Created {len(created)} user(s):[/]")
            for email, role in created:
                console.print(f"  • {email} ({role})")

        if skipped:
            rprint(f"[yellow]⚠ Skipped {len(skipped)} existing user(s):[/]")
            for email in skipped:
                console.print(f"  • {email}")

        if not created and not skipped:
            rprint("[yellow]No users to create.[/]")

    except ValueError as e:
        rprint(f"[red]✗ Error: {e}[/]")
        sys.exit(1)
    except Exception as e:
        rprint(f"[red]✗ Database error: {e}[/]")
        rprint("[yellow]Make sure PostgreSQL is running and the database exists.[/]")
        sys.exit(1)


cli.add_command(create_user, name="create")
cli.add_command(list_users, name="list")
cli.add_command(reset_password, name="reset-password")
cli.add_command(deactivate_user, name="deactivate")
cli.add_command(activate_user, name="activate")
cli.add_command(create_from_env, name="create-from-env")


if __name__ == "__main__":
    cli()
