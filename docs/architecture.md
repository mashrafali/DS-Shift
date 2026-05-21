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
- Provides dashboard, project management, VM inventory, platform profiles, waves, reports, and about views.
- Talks to the backend through relative `/api` URLs.

`backend`

- FastAPI API service.
- Owns validation and workflow state changes.
- Creates database tables at startup for the MVP.
- Exposes OpenAPI documentation.
- Provides local username/password authentication with bearer sessions.

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

## Future Integration Points

- Authentication and RBAC middleware.
- Vault-backed credential references.
- Platform discovery connectors for vCenter, libvirt/KVM, GCP, AWS, Azure, and Nutanix.
- Migration execution adapters for virt-v2v, cloud migration APIs, and replication tools.
- Audit logging and report export services.
