#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 4 ]] || { echo 'Usage: publish-frontend-module.sh MODULE VERSION PACKAGE EXPECTED_CURRENT_OR_none' >&2; exit 2; }
exec python3 "$(dirname "$0")/release.py" frontend-publish "$1" "$2" --package "$3" --expected-current "$4"
