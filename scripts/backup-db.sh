#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p backups
timestamp="$(date +%Y%m%d-%H%M%S)"
docker compose exec -T database pg_dump -U "${POSTGRES_USER:-dsreplace}" "${POSTGRES_DB:-dsreplace}" > "backups/dsreplace-${timestamp}.sql"
echo "Created backups/dsreplace-${timestamp}.sql"
