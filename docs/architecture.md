# Architecture

DS Replace uses a three-tier application architecture deployed with Docker Compose.

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
- Uses a custom Defined Solutions data-center migration mark instead of a generic security icon.

`backend`

- FastAPI API service.
- Owns validation and workflow state changes.
- Creates database tables at startup for the MVP.
- Exposes OpenAPI documentation.
- Provides local username/password authentication with bearer sessions.
- Provides KVM discovery through SSH and `virsh`.
- Provides vCenter discovery through `govc`.
- Provides KVM-to-ESXi/vCenter migration preflight job creation using a `virt-v2v` runbook model.

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

- Discovery runs call real tools and record success or failure.
- Migration jobs create a real runbook for KVM-to-ESXi/vCenter conversion and validate required local tools.
- Live migration execution is not triggered automatically from the MVP UI.
- Runtime credentials should be injected through environment variables, mounted SSH keys, Docker secrets, or a future vault integration.

## Future Integration Points

- RBAC middleware.
- Vault-backed credential references.
- Platform discovery connectors for GCP, AWS, Azure, and Nutanix.
- Controlled migration execution adapters for virt-v2v, cloud migration APIs, and replication tools.
- Audit logging and report export services.
