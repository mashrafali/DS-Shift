# Deployment Guide

## Requirements

- Linux VM with Docker Engine and Docker Compose v2.
- TCP ports 80 and 443 available.
- Git access to the DS Replace repository.

## Install Docker on CentOS Stream

```bash
./scripts/install-docker.sh
```

## Deploy

```bash
git clone git@github.com:mashrafali/ds-replace.git
cd ds-replace
cp .env.example .env
openssl rand -base64 32
vi .env
./scripts/deploy.sh
```

Set `ADMIN_INITIAL_USERNAME` and `ADMIN_INITIAL_PASSWORD` before the first startup. If omitted, the backend seeds `admin` with the lab default requested for the MVP.

The deploy script creates a self-signed certificate in `ops/certs/` if one is not already present.

## Validate

```bash
docker compose ps
curl -k https://localhost/api/health
curl -k -I https://localhost
./scripts/validate.sh
```

## Backup

```bash
./scripts/backup-db.sh
```

Backups are written to `backups/` and should be copied to durable storage.
