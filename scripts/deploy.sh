#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

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

docker compose up -d --build
docker compose ps
