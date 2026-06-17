# Architecture

DS Shift uses a service-oriented application architecture deployed with Docker Compose.

## Components

`reverse-proxy`

- Three internal Nginx replicas.
- Terminates TLS using a self-signed certificate.
- Load balances `/api/*`, `/docs`, and `/openapi.json` across three backend
  replicas using Docker DNS.
- Load balances all other requests across three frontend replicas.

`edge-gateway`

- Single Nginx host-port binding for TCP ports 80 and 443.
- Redirects HTTP to HTTPS and load balances HTTPS requests across the three
  reverse-proxy replicas.
- Exists because multiple Compose containers cannot bind the same host ports.

`frontend`

- Three stateless React/Nginx application replicas.
- Provides dashboard, connector-synchronized VM inventory, migration plans,
  host connectors, cloud connectors, waves, reports, and settings control.
- Talks to the backend through relative `/api` URLs.
- Uses the DS Shift brand logo supplied by Defined Solutions.

`backend`

- Three stateless FastAPI API replicas.
- Owns validation and workflow state changes.
- Creates database tables at startup for the MVP.
- Exposes OpenAPI documentation.
- Provides local username/password authentication with bearer sessions.
- Brokers connector validation and discovery requests to dedicated connector engine services.
- Persists host inventory and VM-to-host placement returned by Host Connector
  discovery.
- Synchronizes discovered workloads into VM inventory.
- Creates and monitors durable Spark Engine execution jobs.

`host-connector`

- Three stateless connector replicas.
- Validates and discovers KVM/libvirt with Paramiko and `virsh`.
- Validates and discovers VMware vCenter/ESXi with pyVmomi.
- Validates and discovers Nutanix AHV with the Prism Central v3 REST API.
- Receives only host-platform credentials and the connector SSH key mount.

`cloud-connector`

- Three stateless connector replicas.
- Validates and discovers AWS EC2 with Boto3.
- Validates and discovers Google Compute Engine with the Google Cloud Compute SDK.
- Validates and discovers Azure VMs with Azure Identity and Compute Management SDKs.
- Receives only cloud credential JSON environment variables.

`spark-engine`

- Runs as three stateless worker replicas.
- Claims durable PostgreSQL jobs with row locking and `SKIP LOCKED`, so one
  execution job is handled by only one replica.
- Executes AWS-to-AWS, GCP-to-GCP, Azure-to-Azure, KVM-to-KVM,
  KVM-to-vCenter, and vCenter-to-KVM adapters.
- Contains `virt-v2v`, `qemu-img`, libvirt clients, and pinned `govc` for host
  conversion, packaging, import, and target definition.
- Enables live execution by default and can be paused by setting
  `SPARK_LIVE_EXECUTION_ENABLED=false`.
- Rejects unsupported source and target combinations instead of generating
  commands that the underlying tools cannot execute.

`service-status-monitor`

- Three stateless monitor replicas.
- Reads Docker Engine container state for the current Compose project.
- Publishes a status-only internal endpoint consumed by the backend.
- Keeps the Docker socket isolated from the main application backend.

`database`

- One PostgreSQL primary.
- Persistent named Docker volume.
- Internal-only Compose network.
- A three-node database tier is not created by setting `replicas: 3`; it
  requires replication, health-aware routing, fencing, and controlled failover.

## Data Model

- `MigrationProject`: retained legacy compatibility data; no longer exposed in
  navigation.
- `PlatformProfile`: source or target platform placeholder with credential reference metadata.
- `VmInventory`: connector-owned discovered VM inventory with stable provider identifiers, host placement, and migration state.
- `MigrationWave`: planned migration grouping and window.
- `VmStatusHistory`: audit trail for VM migration status changes.
- `LocalUser`: local login user with hashed password.
- `AuthSession`: bearer token session hash and expiry.
- `ConnectorProfile`: host and cloud connector metadata with credential references.
- `AppSetting`: editable product and UI settings.
- `DiscoveryRun`: connector discovery result, command evidence, and discovered VM records.
- `MigrationJob`: migration preflight job, runbook, dependency status, and operator-facing messages.
- `MigrationPlan`: selected VMs, source and target connectors, provider options,
  Spark job reference, execution state, and per-VM results.
- `spark_execution_jobs`: durable Spark worker queue, ownership, status, request,
  and result data.

## Engine Boundary

The migration engine is intentionally split into preflight and live execution phases:

- Discovery runs call real KVM/vCenter APIs or command interfaces and record success or failure.
- `Preflight` calls the selected Spark adapter and checks tools, connector
  credentials, source power state, target storage, and target networking
  without creating or changing a VM.
- `Launch` is admin-only, requires exact plan-name confirmation, and submits a
  live job to Spark Engine.
- KVM-to-vCenter converts powered-off file-backed disks to stream-optimized
  VMDK, packages an OVA, and imports it with `govc`.
- vCenter-to-KVM uses `virt-v2v`, transfers converted qcow2 disks to the
  selected target storage pool, and defines the generated libvirt domain.
- Cross-account, cross-subscription, and other cross-provider combinations are
  rejected until explicit staging and import workflows exist.
- Runtime credentials should be injected through environment variables, mounted SSH keys, Docker secrets, or a future vault integration.

## Future Integration Points

- RBAC middleware.
- Vault-backed credential references.
- Additional platform discovery connectors and richer inventory attributes.
- Cross-provider cloud staging and replication-based adapters.
- Audit logging and report export services.
