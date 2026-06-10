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
- Provides dashboard, saved project management, VM inventory, host connectors, cloud connectors, migration engine, waves, reports, and settings control.
- Talks to the backend through relative `/api` URLs.
- Uses the DS Shift brand logo supplied by Defined Solutions.

`backend`

- FastAPI API service.
- Owns validation and workflow state changes.
- Creates database tables at startup for the MVP.
- Exposes OpenAPI documentation.
- Provides local username/password authentication with bearer sessions.
- Provides KVM discovery through Python SSH and `virsh`.
- Provides vCenter discovery through the VMware SDK for Python.
- Provides KVM-to-ESXi/vCenter migration test preflight job creation using connector validation, source VM inspection, target validation, and a `virt-v2v` runbook model.

`database`

- PostgreSQL.
- Persistent named Docker volume.
- Internal-only Compose network.

## Data Model

- `MigrationProject`: customer migration engagement and source/target context.
- `PlatformProfile`: source or target platform placeholder with credential reference metadata.
- `VmInventory`: VM assessment and migration state.
- `MigrationWave`: planned migration grouping and window.
- `VmStatusHistory`: audit trail for VM migration status changes.
- `LocalUser`: local login user with hashed password.
- `AuthSession`: bearer token session hash and expiry.
- `ConnectorProfile`: host and cloud connector metadata with credential references.
- `AppSetting`: editable product and UI settings.
- `DiscoveryRun`: connector discovery result, command evidence, and discovered VM records.
- `MigrationJob`: migration preflight job, runbook, dependency status, and operator-facing messages.

## Engine Boundary

The migration engine is intentionally split into safe preflight and live execution phases:

- Discovery runs call real KVM/vCenter APIs or command interfaces and record success or failure.
- Migration jobs create a non-destructive KVM-to-ESXi/vCenter test preflight and validate the live execution tool requirements.
- Live migration execution is not triggered automatically from the MVP UI.
- Runtime credentials should be injected through environment variables, mounted SSH keys, Docker secrets, or a future vault integration.

## Future Integration Points

- RBAC middleware.
- Vault-backed credential references.
- Platform discovery connectors for GCP, AWS, Azure, and Nutanix.
- Controlled migration execution adapters for virt-v2v, cloud migration APIs, and replication tools.
- Audit logging and report export services.
