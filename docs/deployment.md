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

## Migration Testing Credentials

For lab migration testing, set connector credential references to environment-backed values and keep the real passwords only in `.env`:

```bash
KVM_PASSWORD=<kvm-password>
VCENTER_PASSWORD=<vcenter-password>
```

Use these connector credential references in the UI:

- KVM connector: `env:KVM_PASSWORD`
- vCenter connector: `env:VCENTER_PASSWORD`

The release-candidate engine validates and discovers real KVM and vCenter inventory without storing these passwords in PostgreSQL. Live KVM-to-ESXi conversion remains gated and additionally requires `qemu-img` and `virt-v2v` in the runtime.

For key-based KVM access, create an engine SSH key under `/opt/ds-replace/engine-ssh` on the application VM, install the public key on the KVM host, and set the KVM connector credential reference to `ssh-key:container`. The Compose deployment mounts `./engine-ssh` read-only into the backend container as `/root/.ssh`.

## Backup

```bash
./scripts/backup-db.sh
```

Backups are written to `backups/` and should be copied to durable storage.
