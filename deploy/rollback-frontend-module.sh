#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 3 ]] || { echo 'Usage: rollback-frontend-module.sh MODULE VERSION EXPECTED_CURRENT' >&2; exit 2; }
exec python3 "$(dirname "$0")/release.py" frontend-rollback "$1" "$2" --expected-current "$3"
