#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

load_env() {
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
}

wait_for_database() {
  local retries=30
  local attempt
  for attempt in $(seq 1 "${retries}"); do
    if docker compose ps --format json database 2>/dev/null | jq -e 'select(.Service == "database" and .Health == "healthy")' >/dev/null; then
      return 0
    fi
    sleep 2
  done
  echo "Database did not become healthy in time" >&2
  return 1
}

sync_database_credentials() {
  local legacy_db="${LEGACY_POSTGRES_DB:-}"
  local legacy_user="${LEGACY_POSTGRES_USER:-}"
  local sql_user sql_password sql_db sql_legacy_db
  sql_user="$(python3 -c "import sys; print(repr(sys.argv[1]))" "${POSTGRES_USER}")"
  sql_password="$(python3 -c "import sys; print(repr(sys.argv[1]))" "${POSTGRES_PASSWORD}")"
  sql_db="$(python3 -c "import sys; print(repr(sys.argv[1]))" "${POSTGRES_DB}")"
  sql_legacy_db="$(python3 -c "import sys; print(repr(sys.argv[1]))" "${legacy_db}")"

  docker compose exec -T \
    -e TARGET_USER="${POSTGRES_USER}" \
    -e LEGACY_USER="${legacy_user}" \
    database \
    sh -lc '
      for candidate in "$TARGET_USER" "$LEGACY_USER" postgres; do
        if [ -n "$candidate" ] && psql -U "$candidate" -d postgres -Atqc "select 1" >/dev/null 2>&1; then
          exec psql -v ON_ERROR_STOP=1 -U "$candidate" -d postgres
        fi
      done
      echo "No bootstrap Postgres role is available for credential reconciliation" >&2
      exit 1
    ' <<SQL
DO \$\$
DECLARE
  target_user text := ${sql_user};
  target_password text := ${sql_password};
  target_db text := ${sql_db};
  legacy_db text := ${sql_legacy_db};
  target_db_exists boolean;
  legacy_db_exists boolean;
BEGIN
  IF target_user IS NULL OR target_user = '' THEN
    RAISE EXCEPTION 'target user is required';
  END IF;
  IF target_password IS NULL THEN
    RAISE EXCEPTION 'target password is required';
  END IF;
  IF target_db IS NULL OR target_db = '' THEN
    RAISE EXCEPTION 'target database is required';
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target_user) THEN
    EXECUTE format('CREATE ROLE %I WITH LOGIN SUPERUSER PASSWORD %L', target_user, target_password);
  ELSE
    EXECUTE format('ALTER ROLE %I WITH LOGIN SUPERUSER PASSWORD %L', target_user, target_password);
  END IF;

  SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = target_db) INTO target_db_exists;
  IF NOT target_db_exists THEN
    SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = legacy_db) INTO legacy_db_exists;
    IF legacy_db IS NOT NULL AND legacy_db <> '' AND legacy_db <> target_db AND legacy_db_exists THEN
      EXECUTE format('CREATE DATABASE %I OWNER %I TEMPLATE %I', target_db, target_user, legacy_db);
    ELSE
      EXECUTE format('CREATE DATABASE %I OWNER %I', target_db, target_user);
    END IF;
  END IF;

  EXECUTE format('ALTER DATABASE %I OWNER TO %I', target_db, target_user);
END
\$\$;
SQL
}

if [[ ! -f .env ]]; then
  password="$(openssl rand -base64 32 | tr -d '\n')"
  cp .env.example .env
  sed -i "s|change-me-to-a-random-local-secret|${password}|" .env
fi

postgres_password="$(awk -F= '$1 == "POSTGRES_PASSWORD" {sub(/^[^=]*=/, "", $0); print $0; exit}' .env)"
if [[ -n "${postgres_password}" ]]; then
  encoded_password="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "${postgres_password}")"
  if grep -q '^POSTGRES_PASSWORD_URLENCODED=' .env; then
    sed -i "s|^POSTGRES_PASSWORD_URLENCODED=.*|POSTGRES_PASSWORD_URLENCODED=${encoded_password}|" .env
  else
    printf 'POSTGRES_PASSWORD_URLENCODED=%s\n' "${encoded_password}" >> .env
  fi
fi

mkdir -p ops/certs
mkdir -p /DS-Shift-Staging
if [[ ! -f ops/certs/ds-shift.crt || ! -f ops/certs/ds-shift.key ]]; then
  openssl req -x509 -nodes -newkey rsa:4096 -days 825 \
    -keyout ops/certs/ds-shift.key \
    -out ops/certs/ds-shift.crt \
    -subj "/CN=ds-shift-app/O=Defined Solutions"
fi

load_env
docker compose up -d database
wait_for_database
sync_database_credentials
docker compose up -d --build
docker compose ps
