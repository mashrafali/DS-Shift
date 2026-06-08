# DS Replace

Defined Solutions cloud-native platform for any-to-any VM migration planning, launch, execution, tracking, and workflow management.

DS Replace 1.0 RC1 is an MVP focused on assessment, source/target inventory, migration planning, migration waves, VM workflow state tracking, dashboard metrics, discovery runs, and migration preflight jobs. Live migration execution is approval-gated and should only be enabled after credential, network, and rollback controls are validated.

## Architecture

- Frontend: React/Vite enterprise dashboard.
- Backend: FastAPI REST API with OpenAPI documentation.
- Database: PostgreSQL with a persistent Docker volume.
- Edge: Nginx reverse proxy with self-signed HTTPS.
- Runtime: Docker Compose.

## Quick Start

```bash
cp .env.example .env
sed -i 's/change-me-to-a-random-local-secret/replace-with-a-random-secret/' .env
./scripts/deploy.sh
./scripts/validate.sh
```

Access:

- GUI: `https://<host>/`
- API health: `https://<host>/api/health`
- API docs: `https://<host>/docs`

Default MVP login:

- Username: `admin`
- Initial password: set with `ADMIN_INITIAL_PASSWORD`; lab bootstrap may use `P@ssw0rd`.
- Change the environment value before production use.

## Services

- `frontend`
- `backend`
- `database`
- `reverse-proxy`

PostgreSQL is internal to the Docker network and is not exposed publicly.

## Security Notes

- Do not commit `.env`.
- Use a strong random `POSTGRES_PASSWORD`.
- Self-signed TLS is suitable for MVP validation only.
- Local passwords are stored as PBKDF2 hashes, not clear text.
- Platform credentials are placeholders; real secret storage should use a vault integration.

## Users

Administrators manage local users from `Settings Control` in the web UI. The Settings page includes the current user list and an `Add local user` form. The same functions are exposed by the backend API:

- `GET /api/users`
- `POST /api/users`
- `PUT /api/users/{user_id}`

The seeded lab admin is `admin` with the configured `ADMIN_INITIAL_PASSWORD`.

## Discovery and Migration Engine

- KVM discovery uses Python SSH plus `virsh` against connector endpoints such as `qemu+ssh://root@kvm/system`.
- vCenter discovery uses the VMware SDK for Python with password references such as `env:VCENTER_PASSWORD`.
- KVM to ESXi/vCenter migration jobs run a non-destructive migration test preflight: source connector validation, source VM inspection, target vCenter validation, and live conversion tool checks.
- The engine container must receive `KVM_PASSWORD` and `VCENTER_PASSWORD` through `.env` for lab testing. Live conversion still requires `qemu-img` and `virt-v2v`.

## MVP Limitations

- Discovery engines are implemented for KVM and vCenter and require reachable endpoints and runtime credentials.
- KVM to ESXi/vCenter migration testing is implemented as a non-destructive preflight/runbook engine. Live execution still requires explicit operational approval.
- No production RBAC yet.
- No external certificate automation yet.
