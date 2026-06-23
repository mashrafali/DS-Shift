# DS Shift

Defined Solutions platform for VM migration planning, controlled execution, tracking, and workflow management.

DS Shift 1.0 RC1 is an MVP focused on assessment, source/target inventory,
migration planning, migration waves, VM workflow state tracking, dashboard
metrics, discovery runs, and migration execution jobs. Live execution is
approval-gated and enabled by default.

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

One-command installer:

```bash
curl -fsSL https://raw.githubusercontent.com/mashrafali/DS-Shift/main/install-ds-shift.sh | sudo bash
```

Useful overrides:

```bash
curl -fsSL https://raw.githubusercontent.com/mashrafali/DS-Shift/main/install-ds-shift.sh | \
  sudo DS_SHIFT_INSTALL_DIR=/opt/ds-shift DS_SHIFT_BRANCH=main DS_SHIFT_POSTGRES_PASSWORD='StrongDatabaseSecretHere' bash
```

Access:

- GUI: `https://<host>/`
- API health: `https://<host>/api/health`
- API docs: `https://<host>/docs`

First login:

- Open the GUI after the first deployment.
- DS Shift asks for the initial `admin` password when no local users exist.
- That password is hashed and used for later `admin` sign-ins.

## Services

- `frontend`
- `backend`
- `database`
- `reverse-proxy`
- `edge-gateway` (single host-port binding in front of the proxy pool)
- `host-connector`
- `cloud-connector`
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

The `admin` user is created by the first-run GUI setup when the local user table is empty.

## Discovery and Migration Engine

- The Host Connector supports KVM through Paramiko/`virsh`, VMware through pyVmomi, and Nutanix AHV through the Prism Central v3 API.
- The Cloud Connector supports AWS EC2 through Boto3, Google Compute Engine through the Google Cloud SDK, and Azure VMs through Azure Identity and Compute Management SDKs.
- Connector metadata and discovery history remain in the main backend; validation and discovery execute in the isolated engine containers.
- Spark Engine has executable adapters for AWS-to-AWS within one account,
  GCP-to-GCP using machine images, Azure-to-Azure within one subscription, and
  KVM-to-KVM using `virsh migrate`.
- KVM-to-vCenter uses `qemu-img` plus an OVA import through `govc`.
- vCenter-to-KVM downloads VMware disk artifacts into staging, converts them
  with `qemu-img`, transfers converted disks to the selected KVM storage pool,
  and defines the generated libvirt domain through LaunchGrid.
- Other cross-provider execution remains blocked until its required packaging,
  staging, network mapping, and import pipeline exists.
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
