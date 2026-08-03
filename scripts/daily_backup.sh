#!/bin/bash
# Daily backup script for AUGSD Portal
# This script is designed to be run via cron

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Change to project directory
cd "$PROJECT_DIR" || exit 1

# Create logs directory if it doesn't exist
mkdir -p logs

# Log file
LOG_FILE="logs/backup_$(date +%Y%m).log"

# Function to log messages
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

log "======================================================================"
log "Starting daily backup"
log "======================================================================"
	
# Run backup with 1000-day retention
if python scripts/backup_database.py --retention-days 1000 >> "$LOG_FILE" 2>&1; then
    log "✅ Backup completed successfully"
    EXIT_CODE=0
else
    log "❌ Backup failed with exit code $?"
    EXIT_CODE=1
fi

log "======================================================================"
log "Daily backup finished"
log "======================================================================"
log ""

exit $EXIT_CODE
