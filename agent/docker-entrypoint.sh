#!/bin/sh
set -e

# Symlink persistent state files from the data volume into /app/
# where Path(__file__).parent resolves, so app code works unchanged.
for f in memory.json habit_plan.json draft_schedule.json; do
    # Create the file in the volume if it doesn't exist yet
    if [ ! -f "/app/data/$f" ]; then
        echo "{}" > "/app/data/$f"
    fi
    ln -sf "/app/data/$f" "/app/$f"
done

# Symlink Google OAuth credentials from the secrets volume
for f in credentials.json token.json; do
    if [ -f "/app/secrets/$f" ]; then
        ln -sf "/app/secrets/$f" "/app/$f"
    fi
done

exec uv run python main.py start
