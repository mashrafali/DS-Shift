#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  password="$(openssl rand -base64 32 | tr -d '\n')"
  cp .env.example .env
  sed -i "s|change-me-to-a-random-local-secret|${password}|" .env
fi

mkdir -p ops/certs
if [[ ! -f ops/certs/ds-shift.crt || ! -f ops/certs/ds-shift.key ]]; then
  openssl req -x509 -nodes -newkey rsa:4096 -days 825 \
    -keyout ops/certs/ds-shift.key \
    -out ops/certs/ds-shift.crt \
    -subj "/CN=ds-shift-app/O=Defined Solutions"
fi

docker compose up -d --build
docker compose ps
