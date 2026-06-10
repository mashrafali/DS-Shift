#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p backups
timestamp="$(date +%Y%m%d-%H%M%S)"
docker compose exec -T database pg_dump -U "${POSTGRES_USER:-dsshift}" "${POSTGRES_DB:-dsshift}" > "backups/dsshift-${timestamp}.sql"
echo "Created backups/dsshift-${timestamp}.sql"
