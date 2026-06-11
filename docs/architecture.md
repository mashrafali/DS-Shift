# Architecture

DS Shift uses a three-tier application architecture deployed with Docker Compose.

## Components

`reverse-proxy`

- Nginx public edge.
- Redirects HTTP to HTTPS.
- Terminates TLS using a self-signed certificate.
- Routes `/api/*`, `/docs`, and `/openapi.json` to the backend.
- Routes all other requests to the frontend.

`frontend`

- React application.
- Provides dashboard, connector-synchronized VM inventory, migration plans,
  host connectors, cloud connectors, waves, reports, and settings control.
- Talks to the backend through relative `/api` URLs.
- Uses the DS Shift brand logo supplied by Defined Solutions.

`backend`

- FastAPI API service.
- Owns validation and workflow state changes.
- Creates database tables at startup for the MVP.
- Exposes OpenAPI documentation.
- Provides local username/password authentication with bearer sessions.
- Brokers connector validation and discovery requests to dedicated connector engine services.
- Persists host inventory and VM-to-host placement returned by Host Connector
  discovery.
- Synchronizes discovered workloads into VM inventory.
- Executes KVM-to-ESXi/vCenter migration-plan preflight using connector
  validation, source VM inspection, target validation, and a `virt-v2v` runbook
  model.

`host-connector-engine`

- Validates and discovers KVM/libvirt with Paramiko and `virsh`.
- Validates and discovers VMware vCenter/ESXi with pyVmomi.
- Validates and discovers Nutanix AHV with the Prism Central v3 REST API.
- Receives only host-platform credentials and the connector SSH key mount.

`cloud-connector-engine`

- Validates and discovers AWS EC2 with Boto3.
- Validates and discovers Google Compute Engine with the Google Cloud Compute SDK.
- Validates and discovers Azure VMs with Azure Identity and Compute Management SDKs.
- Receives only cloud credential JSON environment variables.

`service-status-monitor`

- Reads Docker Engine container state for the current Compose project.
- Publishes a status-only internal endpoint consumed by the backend.
- Keeps the Docker socket isolated from the main application backend.

`database`

- PostgreSQL.
- Persistent named Docker volume.
- Internal-only Compose network.

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
- `MigrationPlan`: selected VMs, source and target connectors, execution state,
  and per-VM preflight results.

## Engine Boundary

The migration engine is intentionally split into safe preflight and live execution phases:

- Discovery runs call real KVM/vCenter APIs or command interfaces and record success or failure.
- Migration plan execution creates a non-destructive KVM-to-ESXi/vCenter
  preflight per selected VM and validates live execution tool requirements.
- Live migration execution is not triggered automatically from the MVP UI.
- Runtime credentials should be injected through environment variables, mounted SSH keys, Docker secrets, or a future vault integration.

## Future Integration Points

- RBAC middleware.
- Vault-backed credential references.
- Additional platform discovery connectors and richer inventory attributes.
- Controlled migration execution adapters for virt-v2v, cloud migration APIs, and replication tools.
- Audit logging and report export services.
