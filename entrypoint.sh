#!/bin/sh

echo "Running entrypoint.sh..."

if [ -z "$SERVICE" ]; then
  echo "SERVICE environment variable is not set"
  exit 1
fi

if [ "$SERVICE" = "migrations" ]; then
    echo "Triggering migrations..."
    alembic upgrade head
elif [ "$SERVICE" = "api" ]; then
    echo "Triggering api..."
    python entrypoint_api.py
elif [ "$SERVICE" = "worker" ]; then
    echo "Triggering worker..."
    python entrypoint_worker.py
else
    echo "Unknown SERVICE: $SERVICE"
    exit 1
fi
