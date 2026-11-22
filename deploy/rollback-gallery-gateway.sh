#!/usr/bin/env bash
set -euo pipefail
# This rolls back ONLY the gateway image. Never stop private API services.
exec python3 "$(dirname "$0")/release.py" backend-rollback gallery "$@"
