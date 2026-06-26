#!/bin/bash
set -e

# Fix ownership of mounted volumes so appuser can write
chown -R appuser:appuser /app/static/uploads /app/data 2>/dev/null || true

exec gosu appuser "$@"
