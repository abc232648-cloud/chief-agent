#!/bin/sh
set -eu

# The product consists of the dashboard and the continuous worker.
# Keep both in one container for the simple single-container deployment;
# the worker is backgrounded and the dashboard remains PID 1.
python -m worker.runner &
worker_pid=$!

cleanup() {
  kill "$worker_pid" 2>/dev/null || true
  wait "$worker_pid" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

exec python dashboard_app.py
