# DS Replace

Defined Solutions cloud-native platform for any-to-any VM migration planning, tracking, and workflow management.

DS Replace 1.0 RC1 is an MVP focused on assessment, source/target inventory, migration planning, migration waves, VM workflow state tracking, dashboard metrics, and basic readiness reporting. It does not execute live VM migrations in this release.

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

## MVP Limitations

- No real platform discovery connectors yet.
- No migration execution engine yet.
- No production authentication or RBAC yet.
- No external certificate automation yet.
