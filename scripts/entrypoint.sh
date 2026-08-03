#!/bin/bash
set -e

# Runtime directories that need write access. The configured paths are used rather
# than fixed ones so that a mounted volume (Railway, Fly, Render) is created and
# chowned too - otherwise it stays root-owned and the app cannot write to it.
RUNTIME_DIRS="${UPLOAD_DIR:-uploads} ${EXPORT_DIR:-exports} ${LOG_DIR:-logs} ${BACKUP_DIR:-backups}"

# If running as root, fix permissions and re-exec as appuser
if [ "$(id -u)" = "0" ]; then
    echo "Running as root - fixing permissions..."

    for dir in $RUNTIME_DIRS; do
        mkdir -p "$dir"
        chown -R appuser:appuser "$dir"
    done

    # Re-execute as appuser
    exec gosu appuser "$@"
fi

# Running as non-root - verify we can write to required directories
for dir in $RUNTIME_DIRS; do
    mkdir -p "$dir" 2>/dev/null || true
    if [ ! -w "$dir" ]; then
        echo "WARNING: $dir is not writable by user $(id -u)"
    fi
done

exec "$@"
