#!/usr/bin/env bash
#
# Backup logs directory to timestamped tarball in backups/logs_bck/
#
# Usage:
#   ./scripts/backup_logs.sh [--retention-days N]
#
# Options:
#   --retention-days N    Delete backups older than N days (default: 30)
#

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LOGS_DIR="$PROJECT_ROOT/logs"
BACKUP_ROOT="$PROJECT_ROOT/backups"
BACKUP_DIR="$BACKUP_ROOT/logs_bck"
RETENTION_DAYS=10000

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --retention-days)
            RETENTION_DAYS="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [--retention-days N]"
            echo ""
            echo "Backup logs directory to timestamped tarball in backups/logs_bck/"
            echo ""
            echo "Options:"
            echo "  --retention-days N    Delete backups older than N days (default: 30)"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Validate retention days
if ! [[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]] || [ "$RETENTION_DAYS" -lt 1 ]; then
    echo "❌ Error: retention-days must be a positive integer"
    exit 1
fi

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

# Check if logs directory exists and has content
if [ ! -d "$LOGS_DIR" ]; then
    echo "⚠️  Warning: logs directory does not exist at $LOGS_DIR"
    echo "   Creating empty logs directory..."
    mkdir -p "$LOGS_DIR"
    echo "✅ Created logs directory (nothing to backup)"
    exit 0
fi

if [ ! "$(ls -A "$LOGS_DIR" 2>/dev/null)" ]; then
    echo "⚠️  Warning: logs directory is empty"
    echo "   Nothing to backup"
    exit 0
fi

# Generate timestamp for backup filename
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/logs_backup_$TIMESTAMP.tar.gz"

echo "📦 Starting logs backup..."
echo "   Source: $LOGS_DIR"
echo "   Target: $BACKUP_FILE"

# Create tarball with progress indicator
if tar -czf "$BACKUP_FILE" -C "$PROJECT_ROOT" logs 2>/dev/null; then
    BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    echo "✅ Backup created successfully"
    echo "   File: $(basename "$BACKUP_FILE")"
    echo "   Size: $BACKUP_SIZE"
else
    echo "❌ Error: Failed to create backup"
    exit 1
fi

# Cleanup old backups
echo ""
echo "🧹 Cleaning up old backups (older than $RETENTION_DAYS days)..."

DELETED_COUNT=0
while IFS= read -r old_backup; do
    rm -f "$old_backup"
    echo "   Deleted: $(basename "$old_backup")"
    ((DELETED_COUNT++))
done < <(find "$BACKUP_DIR" -name "logs_backup_*.tar.gz" -type f -mtime +"$RETENTION_DAYS" 2>/dev/null)

if [ "$DELETED_COUNT" -eq 0 ]; then
    echo "   No old backups to delete"
else
    echo "   Deleted $DELETED_COUNT old backup(s)"
fi

# Show current backups
echo ""
echo "📋 Current backups:"
BACKUP_COUNT=$(find "$BACKUP_DIR" -name "logs_backup_*.tar.gz" -type f 2>/dev/null | wc -l)
if [ "$BACKUP_COUNT" -eq 0 ]; then
    echo "   No backups found"
else
    echo "   Total: $BACKUP_COUNT backup(s)"
    find "$BACKUP_DIR" -name "logs_backup_*.tar.gz" -type f -exec ls -lh {} \; 2>/dev/null | \
        awk '{printf "   - %s (%s)\n", $9, $5}' | \
        sed "s|$BACKUP_DIR/||g" | \
        sort -r | \
        head -5
    if [ "$BACKUP_COUNT" -gt 5 ]; then
        echo "   ... and $((BACKUP_COUNT - 5)) more"
    fi
fi

echo ""
echo "✅ Logs backup completed successfully"
