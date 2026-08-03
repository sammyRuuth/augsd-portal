#!/bin/bash
set -e

# Runtime directories that need write access
RUNTIME_DIRS="uploads exports logs backups"

# If running as root, fix permissions and re-exec as appuser
if [ "$(id -u)" = "0" ]; then
    echo "Running as root - fixing permissions..."

    for dir in $RUNTIME_DIRS; do
        if [ -d "/app/$dir" ]; then
            chown -R appuser:appuser "/app/$dir"
        fi
    done

    # Re-execute as appuser
    exec gosu appuser "$@"
fi

# Running as non-root - verify we can write to required directories
for dir in $RUNTIME_DIRS; do
    if [ -d "/app/$dir" ] && [ ! -w "/app/$dir" ]; then
        echo "WARNING: /app/$dir is not writable by user $(id -u)"
    fi
done

exec "$@"
