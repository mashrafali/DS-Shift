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
git clone git@github.com:mashrafali/DS-Shift.git
cd DS-Shift
cp .env.example .env
openssl rand -base64 32
vi .env
./scripts/deploy.sh
```

All-in-one installer:

```bash
curl -fsSL https://raw.githubusercontent.com/mashrafali/DS-Shift/main/install-ds-shift.sh | sudo bash
```

Optional installer environment overrides:

```bash
DS_SHIFT_INSTALL_DIR=/opt/ds-shift
DS_SHIFT_BRANCH=main
DS_SHIFT_ADMIN_INITIAL_USERNAME=admin
DS_SHIFT_ADMIN_INITIAL_PASSWORD=<set-a-password>
DS_SHIFT_POSTGRES_PASSWORD=<set-a-password>
DS_SHIFT_SKIP_VALIDATE=true
```

Example:

```bash
curl -fsSL https://raw.githubusercontent.com/mashrafali/DS-Shift/main/install-ds-shift.sh | \
  sudo DS_SHIFT_ADMIN_INITIAL_PASSWORD='<set-a-password>' DS_SHIFT_POSTGRES_PASSWORD='<set-a-password>' bash
```

If `.env` already exists in the install directory, the installer preserves the
current values unless you explicitly override them through `DS_SHIFT_*`
environment variables.

Set `ADMIN_INITIAL_USERNAME` and `ADMIN_INITIAL_PASSWORD` before the first startup. If omitted, the backend seeds `admin` with the lab default requested for the MVP.

The deploy script creates a self-signed certificate in `ops/certs/` if one is not already present.

## Validate

```bash
docker compose ps
curl -k https://localhost/api/health
curl -k -I https://localhost
./scripts/validate.sh
```

The Settings service-status panel requires the `service-status-monitor`
replicas to read `/var/run/docker.sock`. The socket is not mounted into the
application backend. The monitor uses a read-only root filesystem, drops Linux
capabilities, enables `no-new-privileges`, and exposes only health and status
endpoints on the internal Compose network.

## Replica Topology

Docker Compose starts three replicas for backend, frontend, host connector,
cloud connector, Spark Engine, reverse proxy, and service status monitor.
`edge-gateway` remains a singleton because it owns host ports 80 and 443.
PostgreSQL remains one primary because replicas sharing the same data volume
would corrupt data and would not provide database HA.

Validate the topology:

```bash
docker compose ps --format json |
  jq -s 'group_by(.Service) | map({service: .[0].Service, replicas: length})'
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

Spark Engine runs three Compose replicas and uses PostgreSQL as its shared job
queue. Keep live execution disabled during installation and validation:

```bash
SPARK_LIVE_EXECUTION_ENABLED=false
docker compose up -d --build
docker compose ps spark-engine
```

Set the flag to `true` only after validating provider permissions, target
network mappings, rollback procedures, and operational approval. Enabling the
flag does not bypass the admin-only launch and exact plan-name confirmation.

The Spark image includes `virt-v2v`, `qemu-img`, libvirt clients, and `govc`.
KVM-to-vCenter plans require a target datastore and network. vCenter-to-KVM
plans require a target libvirt storage pool and discovered datacenter/compute
resource metadata. Both source VM types must be powered off for these cold
conversion adapters.

Compose does not contain host-specific DNS or IP mappings. Connector endpoints
must be addresses resolvable and reachable from the connector and Spark
containers, so each deployment can use its own KVM, ESXi, or vCenter systems.

For key-based KVM access in an upgraded legacy deployment, move or preserve the engine SSH key under `/opt/ds-shift/engine-ssh`. Install the public key on the KVM host and set the KVM connector credential reference to `ssh-key:container`. The Compose deployment mounts `./engine-ssh` read-only into the backend container as `/root/.ssh`.

## Backup

```bash
./scripts/backup-db.sh
```

Backups are written to `backups/` and should be copied to durable storage.
