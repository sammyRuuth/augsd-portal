#!/usr/bin/env python3
"""
Database backup script for AUGSD Portal.

Creates compressed backups of the PostgreSQL database using pg_dump.
Backups are stored as timestamped tarballs in the backups directory.

Usage:
    python scripts/backup_database.py [options]

Options:
    --retention-days N    Keep backups for N days (default: 30)
    --backup-dir PATH     Backup directory (default: backups/)
    --format FORMAT       Backup format: custom, tar, plain (default: custom)
    --compress-level N    Compression level 0-9 (default: 6)
"""

import argparse
import os
import subprocess
import sys
import tarfile
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


def load_env():
    """Load environment variables from .env file."""
    env_file = Path(__file__).parent.parent / ".env"
    if not env_file.exists():
        print("❌ Error: .env file not found")
        print("   Please create .env file with database credentials")
        sys.exit(1)

    env_vars = {}
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env_vars[key.strip()] = value.strip()

    return env_vars


def get_db_config(env_vars: dict) -> dict:
    """
    Get database configuration from environment variables.

    Supports both:
    - Individual POSTGRES_* variables (preferred for Docker setups)
    - DATABASE_URL parsing (fallback)
    """
    # Prefer individual env vars (Docker-friendly)
    if all(
        k in env_vars for k in ["POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"]
    ):
        return {
            "host": env_vars.get("POSTGRES_HOST", "localhost"),
            "port": int(env_vars.get("POSTGRES_PORT", 5432)),
            "user": env_vars["POSTGRES_USER"],
            "password": env_vars["POSTGRES_PASSWORD"],
            "database": env_vars["POSTGRES_DB"],
        }

    # Fall back to DATABASE_URL parsing
    if "DATABASE_URL" in env_vars:
        return parse_database_url(env_vars["DATABASE_URL"])

    print("❌ Error: No database configuration found in .env")
    print("   Set POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB")
    print("   Or set DATABASE_URL")
    sys.exit(1)


def parse_database_url(database_url: str) -> dict:
    """
    Parse DATABASE_URL into components.

    Handles both sync and async PostgreSQL URLs:
    - postgresql://user:pass@host:port/dbname
    - postgresql+asyncpg://user:pass@host:port/dbname
    """
    # Remove async driver if present
    url = database_url.replace("+asyncpg", "")
    parsed = urlparse(url)

    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "user": parsed.username or "postgres",
        "password": parsed.password or "",
        "database": parsed.path.lstrip("/") or "portal_global",
    }


def check_pg_dump():
    """Check if pg_dump is available."""
    try:
        result = subprocess.run(
            ["pg_dump", "--version"],
            capture_output=True,
            text=True,
            check=True,
        )
        print(f"✓ Found pg_dump: {result.stdout.strip()}")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ Error: pg_dump not found in PATH")
        print("   Please install PostgreSQL client tools")
        return False


def create_backup(
    db_config: dict,
    backup_dir: Path,
    backup_format: str = "custom",
    compress_level: int = 6,
) -> Path | None:
    """
    Create a database backup using pg_dump.

    Args:
        db_config: Database connection parameters
        backup_dir: Directory to store backups
        backup_format: pg_dump format (custom, tar, plain)
        compress_level: Compression level 0-9

    Returns:
        Path to the created backup file, or None if failed
    """
    # Create backup directory if it doesn't exist
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Generate timestamp-based filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Determine file extension based on format
    ext_map = {
        "custom": "dump",
        "tar": "tar",
        "plain": "sql",
    }
    ext = ext_map.get(backup_format, "dump")

    backup_file = backup_dir / f"backup_{timestamp}.{ext}"

    print("\n📦 Creating backup...")
    print(f"   Database: {db_config['database']}")
    print(f"   Host: {db_config['host']}:{db_config['port']}")
    print(f"   Format: {backup_format}")
    print(f"   Output: {backup_file}")

    # Build pg_dump command
    cmd = [
        "pg_dump",
        f"--host={db_config['host']}",
        f"--port={db_config['port']}",
        f"--username={db_config['user']}",
        f"--format={backup_format[0]}",  # c=custom, t=tar, p=plain
        f"--file={backup_file}",
        "--verbose",
    ]

    # Add compression level for custom and tar formats
    if backup_format in ["custom", "tar"]:
        cmd.append(f"--compress={compress_level}")

    # Add database name
    cmd.append(db_config["database"])

    # Set password in environment
    env = os.environ.copy()
    if db_config["password"]:
        env["PGPASSWORD"] = db_config["password"]

    try:
        # Run pg_dump
        subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )

        # Check if backup file was created
        if not backup_file.exists():
            print("❌ Error: Backup file was not created")
            return None

        file_size = backup_file.stat().st_size
        size_mb = file_size / (1024 * 1024)
        print(f"✓ Backup created successfully ({size_mb:.2f} MB)")

        return backup_file

    except subprocess.CalledProcessError as e:
        print(f"❌ Error creating backup: {e}")
        print(f"   stdout: {e.stdout}")
        print(f"   stderr: {e.stderr}")
        return None
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return None


def create_tarball(backup_file: Path, backup_dir: Path) -> Path | None:
    """
    Create a compressed tarball from the backup file.

    Args:
        backup_file: Path to the backup file
        backup_dir: Directory containing backups

    Returns:
        Path to the created tarball, or None if failed
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tarball_path = backup_dir / f"portal_backup_{timestamp}.tar.gz"

    print("\n📦 Creating tarball...")
    print(f"   Source: {backup_file.name}")
    print(f"   Target: {tarball_path.name}")

    try:
        with tarfile.open(tarball_path, "w:gz") as tar:
            tar.add(backup_file, arcname=backup_file.name)

        # Remove original backup file (we have it in the tarball now)
        backup_file.unlink()

        file_size = tarball_path.stat().st_size
        size_mb = file_size / (1024 * 1024)
        print(f"✓ Tarball created successfully ({size_mb:.2f} MB)")

        return tarball_path

    except Exception as e:
        print(f"❌ Error creating tarball: {e}")
        return None


def cleanup_old_backups(backup_dir: Path, retention_days: int):
    """
    Remove backups older than retention_days.

    Args:
        backup_dir: Directory containing backups
        retention_days: Number of days to keep backups
    """
    if retention_days <= 0:
        return

    print(f"\n🧹 Cleaning up old backups (retention: {retention_days} days)...")

    cutoff_date = datetime.now() - timedelta(days=retention_days)
    removed_count = 0
    removed_size = 0

    for backup_file in backup_dir.glob("portal_backup_*.tar.gz"):
        try:
            # Get file modification time
            mtime = datetime.fromtimestamp(backup_file.stat().st_mtime)

            if mtime < cutoff_date:
                file_size = backup_file.stat().st_size
                backup_file.unlink()
                removed_count += 1
                removed_size += file_size
                print(f"   Removed: {backup_file.name} ({mtime.strftime('%Y-%m-%d')})")

        except Exception as e:
            print(f"   ⚠️  Could not remove {backup_file.name}: {e}")

    if removed_count > 0:
        size_mb = removed_size / (1024 * 1024)
        print(f"✓ Removed {removed_count} old backup(s) ({size_mb:.2f} MB freed)")
    else:
        print("✓ No old backups to remove")


def list_backups(backup_dir: Path):
    """List all existing backups."""
    if not backup_dir.exists():
        print("\n📂 No backups directory found")
        return

    backups = sorted(backup_dir.glob("portal_backup_*.tar.gz"))

    if not backups:
        print(f"\n📂 No backups found in {backup_dir}")
        return

    print(f"\n📂 Existing backups in {backup_dir}:")
    total_size = 0

    for backup_file in backups:
        file_size = backup_file.stat().st_size
        size_mb = file_size / (1024 * 1024)
        mtime = datetime.fromtimestamp(backup_file.stat().st_mtime)
        total_size += file_size

        print(f"   • {backup_file.name}")
        print(
            f"     Size: {size_mb:.2f} MB | Created: {mtime.strftime('%Y-%m-%d %H:%M:%S')}"
        )

    total_mb = total_size / (1024 * 1024)
    print(f"\n   Total: {len(backups)} backup(s), {total_mb:.2f} MB")


def main():
    """Main backup script."""
    parser = argparse.ArgumentParser(
        description="Backup AUGSD Portal database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=30,
        help="Keep backups for N days (default: 30)",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path("backups"),
        help="Backup directory (default: backups/)",
    )
    parser.add_argument(
        "--format",
        choices=["custom", "tar", "plain"],
        default="custom",
        help="Backup format (default: custom)",
    )
    parser.add_argument(
        "--compress-level",
        type=int,
        default=6,
        choices=range(0, 10),
        help="Compression level 0-9 (default: 6)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List existing backups and exit",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Skip cleanup of old backups",
    )

    args = parser.parse_args()

    print("=" * 70)
    print("🗄️  AUGSD Portal Database Backup")
    print("=" * 70)

    # List backups if requested
    if args.list:
        list_backups(args.backup_dir)
        return

    # Check for pg_dump
    if not check_pg_dump():
        sys.exit(1)

    # Load environment variables
    print("\n📋 Loading configuration...")
    env_vars = load_env()

    # Get database config from env vars
    db_config = get_db_config(env_vars)
    print("✓ Configuration loaded")
    print(f"   Host: {db_config['host']}:{db_config['port']}")
    print(f"   Database: {db_config['database']}")

    # Create backup
    backup_file = create_backup(
        db_config=db_config,
        backup_dir=args.backup_dir,
        backup_format=args.format,
        compress_level=args.compress_level,
    )

    if not backup_file:
        print("\n❌ Backup failed")
        sys.exit(1)

    # Create tarball
    tarball_path = create_tarball(backup_file, args.backup_dir)

    if not tarball_path:
        print("\n❌ Tarball creation failed")
        sys.exit(1)

    # Cleanup old backups
    if not args.no_cleanup:
        cleanup_old_backups(args.backup_dir, args.retention_days)

    # List all backups
    list_backups(args.backup_dir)

    print("\n" + "=" * 70)
    print("✅ Backup completed successfully!")
    print(f"   Backup file: {tarball_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
