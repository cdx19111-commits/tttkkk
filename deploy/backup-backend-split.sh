#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
stamp="$(date +%Y%m%d-%H%M%S)"
backup="backup/backend-split-$stamp"
mkdir -p "$backup"
files=(Dockerfile requirements.txt compose.yaml Dockerfile.white-api Dockerfile.coverage-api \
  requirements.white-api.txt requirements.coverage-api.txt app/main.py app/api_proxy.py \
  app/coverage_monitor.py app/service_auth.py app/white_gallery_service.py app/coverage_service.py \
  deploy/white-gallery-api.compose.yaml deploy/coverage-api.compose.yaml \
  deploy/ensure-backend-networks.sh deploy/publish-backend-module.sh \
  deploy/publish-gallery-gateway.sh deploy/rollback-gallery-gateway.sh \
  deploy/backup-backend-split.sh)
: > "$backup/existing-files.txt"
: > "$backup/new-files.txt"
for file in "${files[@]}"; do
  if [[ -f "$file" ]]; then
    mkdir -p "$backup/$(dirname "$file")"
    cp -p "$file" "$backup/$file"
    printf '%s\n' "$file" >> "$backup/existing-files.txt"
  else
    printf '%s\n' "$file" >> "$backup/new-files.txt"
  fi
done
old_image="$(docker inspect --format '{{.Image}}' gallery-mvp-gallery-1)"
rollback_tag="gallery-mvp-gallery:rollback-$stamp"
docker image tag "$old_image" "$rollback_tag"
printf '%s\n' "$rollback_tag" > "$backup/rollback-image.txt"
echo "$backup"
