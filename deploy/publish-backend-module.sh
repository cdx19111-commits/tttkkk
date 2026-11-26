#!/usr/bin/env bash
set -euo pipefail
module="${1:-}"
case "$module" in white-gallery-api|coverage-api|governance-api|douyin-api|integration-api|main-image-api) shift;; *) echo 'Usage: publish-backend-module.sh white-gallery-api|coverage-api|main-image-api --source DIR --expected-image sha256:... --release ID --base-image IMAGE@sha256:...' >&2; exit 2;; esac
exec python3 "$(dirname "$0")/release.py" backend-publish "$module" "$@"
