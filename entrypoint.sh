#!/bin/sh

echo "Running entrypoint.sh..."

if [ -z "$SERVICE" ]; then
  echo "SERVICE environment variable is not set"
  exit 1
fi

run_migrations_if_enabled() {
  # Normalize to lower-case for a case-insensitive truthy check.
  enabled=$(echo "${RUN_MIGRATIONS_ON_START:-false}" | tr '[:upper:]' '[:lower:]')
  if [ "$enabled" = "true" ] || [ "$enabled" = "1" ]; then
    echo "RUN_MIGRATIONS_ON_START enabled — running alembic upgrade head..."
    alembic upgrade head || { echo "Migration failed, aborting startup"; exit 1; }
  fi
}

if [ "$SERVICE" = "migrations" ]; then
    echo "Triggering migrations..."
    alembic upgrade head
elif [ "$SERVICE" = "api" ]; then
    run_migrations_if_enabled
    echo "Triggering api..."
    python entrypoint_api.py
elif [ "$SERVICE" = "worker" ]; then
    run_migrations_if_enabled
    echo "Triggering worker..."
    python entrypoint_worker.py
else
    echo "Unknown SERVICE: $SERVICE"
    exit 1
fi
