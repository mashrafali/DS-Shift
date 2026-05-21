#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
docker compose config >/dev/null
docker compose ps
curl -kfsS https://localhost/api/health
curl -kfsSI https://localhost >/dev/null
docker compose exec -T database pg_isready -U "${POSTGRES_USER:-dsreplace}" -d "${POSTGRES_DB:-dsreplace}"
