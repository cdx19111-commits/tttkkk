#!/usr/bin/env bash
set -euo pipefail
module="${1:-}"
case "$module" in white-gallery-api|coverage-api) shift;; *) echo 'Usage: rollback-backend-module.sh white-gallery-api|coverage-api --expected-image sha256:... --image sha256:... --release ID' >&2; exit 2;; esac
exec python3 "$(dirname "$0")/release.py" backend-rollback "$module" "$@"
