# DS Shift

Defined Solutions platform for VM migration planning, controlled execution, tracking, and workflow management.

DS Shift 1.0 RC1 is an MVP focused on assessment, source/target inventory,
migration planning, migration waves, VM workflow state tracking, dashboard
metrics, discovery runs, and migration execution jobs. Live execution is
approval-gated and disabled by default.

## Architecture

- Frontend: React/Vite enterprise dashboard.
- Backend: FastAPI REST API with OpenAPI documentation.
- Database: PostgreSQL with a persistent Docker volume.
- Edge: Nginx reverse proxy with self-signed HTTPS.
- Runtime: Docker Compose.
- Runtime scaling: three replicas for every stateless application service,
  with Docker DNS-aware Nginx load balancing.
- Data tier: one PostgreSQL primary. Database HA requires a separate
  primary/standby and failover design; cloning the primary container against
  one volume is not supported.

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
- `edge-gateway` (single host-port binding in front of the proxy pool)
- `host-connector-engine`
- `cloud-connector-engine`
- `spark-engine` (three replicas)
- `service-status-monitor`

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

- The Host Connector Engine supports KVM through Paramiko/`virsh`, VMware through pyVmomi, and Nutanix AHV through the Prism Central v3 API.
- The Cloud Connector Engine supports AWS EC2 through Boto3, Google Compute Engine through the Google Cloud SDK, and Azure VMs through Azure Identity and Compute Management SDKs.
- Connector metadata and discovery history remain in the main backend; validation and discovery execute in the isolated engine containers.
- Spark Engine has executable adapters for AWS-to-AWS within one account,
  GCP-to-GCP using machine images, Azure-to-Azure within one subscription, and
  KVM-to-KVM using `virsh migrate`.
- KVM-to-VMware and cross-provider cloud execution remain blocked until their
  required packaging, staging, network mapping, and import pipelines exist.
- Spark Engine accepts live jobs only when
  `SPARK_LIVE_EXECUTION_ENABLED=true`; each launch also requires an admin and
  exact migration-plan-name confirmation.

## MVP Limitations

- Execution is limited to the adapter matrix listed above; the product is not
  yet an unrestricted any-to-any migration engine.
- Credentials are environment-backed; production deployments still require a
  vault, least-privilege identities, rollback procedures, and audited approvals.
- No production RBAC yet.
- No external certificate automation yet.
