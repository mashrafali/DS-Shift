# Deployment Guide

## Requirements

- Linux VM with Docker Engine and Docker Compose v2.
- TCP ports 80 and 443 available.
- Git access to the DS Shift source repository.

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
NUTANIX_PASSWORD=<prism-central-password>
```

Use these connector credential references in the UI:

- KVM connector: `env:KVM_PASSWORD`
- vCenter connector: `env:VCENTER_PASSWORD`
- Nutanix connector: `env:NUTANIX_PASSWORD`

Cloud connector credential values are JSON strings stored only in `.env`:

- AWS: `AWS_CONNECTOR_CREDENTIALS` with `access_key_id`, `secret_access_key`, optional `session_token`, and `region`.
- Google Cloud: `GCP_CONNECTOR_CREDENTIALS` containing a service-account JSON document.
- Azure: `AZURE_CONNECTOR_CREDENTIALS` with `tenant_id`, `client_id`, `client_secret`, and `subscription_id`.

The release-candidate engine validates and discovers real KVM and vCenter inventory without storing these passwords in PostgreSQL. Live KVM-to-ESXi conversion remains gated and additionally requires `qemu-img` and `virt-v2v` in the runtime.

For key-based KVM access in an upgraded legacy deployment, the engine SSH key may remain under `/opt/ds-replace/engine-ssh`. Install the public key on the KVM host and set the KVM connector credential reference to `ssh-key:container`. The Compose deployment mounts `./engine-ssh` read-only into the backend container as `/root/.ssh`.

## Backup

```bash
./scripts/backup-db.sh
```

Backups are written to `backups/` and should be copied to durable storage.
