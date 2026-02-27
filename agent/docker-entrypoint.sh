#!/bin/sh
set -e

# Symlink persistent state files from the data volume into /app/
# where Path(__file__).parent resolves, so app code works unchanged.
# memory.json is a dict {}, habit_plan.json and draft_schedule.json are lists []
for f in memory.json; do
    if [ ! -f "/app/data/$f" ]; then
        echo "{}" > "/app/data/$f"
    fi
    ln -sf "/app/data/$f" "/app/$f"
done
for f in habit_plan.json draft_schedule.json; do
    if [ ! -f "/app/data/$f" ]; then
        echo "[]" > "/app/data/$f"
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
