#!/bin/sh
set -e

# Ensure the persistent data volume is mounted and writable.
# Per-user subdirectories (e.g. /app/data/google-12345/) are created
# on demand by the agent when a user connects.
mkdir -p /app/data

# Symlink Google OAuth credentials from the secrets volume
for f in credentials.json token.json; do
    if [ -f "/app/secrets/$f" ]; then
        ln -sf "/app/secrets/$f" "/app/$f"
    fi
done

exec uv run python main.py start
