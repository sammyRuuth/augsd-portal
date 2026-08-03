#!/usr/bin/env python3
"""
Database restore script for AUGSD Portal.

Restores a PostgreSQL database backup created by backup_database.py.

Usage:
    python scripts/restore_database.py <backup_file.tar.gz> [options]

Options:
    --confirm             Skip confirmation prompt (use with caution!)
    --drop-existing       Drop existing database before restore
"""

import argparse
import subprocess
import sys
import tarfile
from pathlib import Path
from urllib.parse import urlparse

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


def load_env():
    """Load environment variables from .env file."""
    env_file = Path(__file__).parent.parent / ".env"
    if not env_file.exists():
        print("❌ Error: .env file not found")
        sys.exit(1)

    env_vars = {}
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env_vars[key.strip()] = value.strip()

    return env_vars


def parse_database_url(database_url: str) -> dict:
    """Parse DATABASE_URL into components."""
    url = database_url.replace("+asyncpg", "")
    parsed = urlparse(url)

    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "user": parsed.username or "postgres",
        "password": parsed.password or "",
        "database": parsed.path.lstrip("/") or "portal_global",
    }


def extract_backup(tarball_path: Path, extract_dir: Path) -> Path | None:
    """
    Extract backup file from tarball.

    Args:
        tarball_path: Path to the tarball
        extract_dir: Directory to extract to

    Returns:
        Path to the extracted backup file
    """
    print("\n📦 Extracting backup...")
    print(f"   Source: {tarball_path}")

    try:
        extract_dir.mkdir(parents=True, exist_ok=True)

        with tarfile.open(tarball_path, "r:gz") as tar:
            # Get the first member (should be the backup file)
            members = tar.getmembers()
            if not members:
                print("❌ Error: Tarball is empty")
                return None

            backup_member = members[0]
            tar.extract(backup_member, extract_dir)

            backup_file = extract_dir / backup_member.name
            print(f"✓ Extracted: {backup_file.name}")

            return backup_file

    except Exception as e:
        print(f"❌ Error extracting tarball: {e}")
        return None


def drop_database(db_config: dict) -> bool:
    """
    Drop existing database.

    Args:
        db_config: Database connection parameters

    Returns:
        True if successful, False otherwise
    """
    print(f"\n⚠️  Dropping database '{db_config['database']}'...")

    # Connect to postgres database to drop the target database
    cmd = [
        "psql",
        f"--host={db_config['host']}",
        f"--port={db_config['port']}",
        f"--username={db_config['user']}",
        "--dbname=postgres",
        "--command",
        f"DROP DATABASE IF EXISTS {db_config['database']};",
    ]

    env = {}
    if db_config["password"]:
        env["PGPASSWORD"] = db_config["password"]

    try:
        subprocess.run(cmd, env=env, check=True, capture_output=True)
        print("✓ Database dropped")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Error dropping database: {e.stderr.decode()}")
        return False


def create_database(db_config: dict) -> bool:
    """
    Create database.

    Args:
        db_config: Database connection parameters

    Returns:
        True if successful, False otherwise
    """
    print(f"\n📝 Creating database '{db_config['database']}'...")

    cmd = [
        "psql",
        f"--host={db_config['host']}",
        f"--port={db_config['port']}",
        f"--username={db_config['user']}",
        "--dbname=postgres",
        "--command",
        f"CREATE DATABASE {db_config['database']};",
    ]

    env = {}
    if db_config["password"]:
        env["PGPASSWORD"] = db_config["password"]

    try:
        subprocess.run(cmd, env=env, check=True, capture_output=True)
        print("✓ Database created")
        return True
    except subprocess.CalledProcessError as e:
        # Database might already exist, that's ok
        stderr = e.stderr.decode()
        if "already exists" in stderr:
            print("✓ Database already exists")
            return True
        print(f"❌ Error creating database: {stderr}")
        return False


def restore_backup(db_config: dict, backup_file: Path) -> bool:
    """
    Restore database from backup file.

    Args:
        db_config: Database connection parameters
        backup_file: Path to the backup file

    Returns:
        True if successful, False otherwise
    """
    print("\n🔄 Restoring backup...")
    print(f"   Database: {db_config['database']}")
    print(f"   Backup: {backup_file.name}")

    # Determine restore command based on file extension
    if backup_file.suffix == ".sql":
        # Plain SQL file - use psql
        cmd = [
            "psql",
            f"--host={db_config['host']}",
            f"--port={db_config['port']}",
            f"--username={db_config['user']}",
            f"--dbname={db_config['database']}",
            f"--file={backup_file}",
        ]
    else:
        # Custom or tar format - use pg_restore
        cmd = [
            "pg_restore",
            f"--host={db_config['host']}",
            f"--port={db_config['port']}",
            f"--username={db_config['user']}",
            f"--dbname={db_config['database']}",
            "--verbose",
            "--clean",  # Clean (drop) database objects before recreating
            "--if-exists",  # Use IF EXISTS when dropping objects
            str(backup_file),
        ]

    env = {}
    if db_config["password"]:
        env["PGPASSWORD"] = db_config["password"]

    try:
        subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        print("✓ Restore completed successfully")
        return True

    except subprocess.CalledProcessError as e:
        print("❌ Error restoring backup:")
        print(f"   {e.stderr}")
        return False


def main():
    """Main restore script."""
    parser = argparse.ArgumentParser(
        description="Restore AUGSD Portal database from backup",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "backup_file",
        type=Path,
        help="Path to backup tarball (.tar.gz)",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Skip confirmation prompt (use with caution!)",
    )
    parser.add_argument(
        "--drop-existing",
        action="store_true",
        help="Drop existing database before restore",
    )

    args = parser.parse_args()

    print("=" * 70)
    print("🔄 AUGSD Portal Database Restore")
    print("=" * 70)

    # Check if backup file exists
    if not args.backup_file.exists():
        print(f"\n❌ Error: Backup file not found: {args.backup_file}")
        sys.exit(1)

    # Load configuration
    print("\n📋 Loading configuration...")
    env_vars = load_env()

    if "DATABASE_URL" not in env_vars:
        print("❌ Error: DATABASE_URL not found in .env")
        sys.exit(1)

    db_config = parse_database_url(env_vars["DATABASE_URL"])
    print("✓ Configuration loaded")
    print(f"   Target database: {db_config['database']}")
    print(f"   Host: {db_config['host']}:{db_config['port']}")

    # Confirmation prompt
    if not args.confirm:
        print("\n⚠️  WARNING: This will restore the database from backup.")
        print("   All current data will be replaced!")
        response = input("\n   Continue? (yes/no): ").strip().lower()
        if response not in ["yes", "y"]:
            print("\n❌ Restore cancelled")
            sys.exit(0)

    # Extract backup from tarball
    temp_dir = Path("temp_restore")
    backup_file = extract_backup(args.backup_file, temp_dir)

    if not backup_file:
        print("\n❌ Restore failed - could not extract backup")
        sys.exit(1)

    try:
        # Drop database if requested
        if args.drop_existing:
            if not drop_database(db_config):
                print("\n❌ Restore failed - could not drop database")
                sys.exit(1)

            if not create_database(db_config):
                print("\n❌ Restore failed - could not create database")
                sys.exit(1)

        # Restore backup
        if not restore_backup(db_config, backup_file):
            print("\n❌ Restore failed")
            sys.exit(1)

        print("\n" + "=" * 70)
        print("✅ Restore completed successfully!")
        print("=" * 70)

    finally:
        # Cleanup temp directory
        if temp_dir.exists():
            import shutil

            shutil.rmtree(temp_dir)
            print("\n🧹 Cleaned up temporary files")


if __name__ == "__main__":
    main()
