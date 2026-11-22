#!/usr/bin/env bash
set -euo pipefail
for network in gallery-white-api-internal gallery-coverage-api-internal; do
  if ! docker network inspect "$network" >/dev/null 2>&1; then
    docker network create --internal "$network" >/dev/null
  fi
done
