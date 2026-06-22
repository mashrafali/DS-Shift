from collections import Counter
from datetime import datetime, timedelta
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
from pathlib import Path
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import models, schemas
from .config import settings
from .connector_client import (
    CONNECTOR_PLATFORMS,
    call_connector_engine,
    connector_engine_status,
    validate_connector_platform,
)
from .database import Base, engine, get_db
from .engines import build_kvm_to_esxi_preflight
from .service_status import get_service_statuses
from .spark_client import cancel_spark_job, create_spark_job, get_spark_job, preflight_spark_job, spark_capabilities

MIGRATION_STATUSES = {
    "Discovered",
    "Assessed",
    "Ready for migration",
    "Replication prepared",
    "Migration in progress",
    "Cutover scheduled",
    "Cutover completed",
    "Validation completed",
    "Failed",
    "Rolled back",
    "Blocked",
}
USER_ROLES = {"admin", "operator", "viewer"}
PROFILE_PHOTO_PATTERN = re.compile(r"^data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)$")
MAX_PROFILE_PHOTO_BYTES = 256 * 1024
STAGING_ROOT = Path(os.getenv("DS_SHIFT_STAGING_ROOT", "/DS-Shift-Staging"))

app = FastAPI(title=settings.app_name, version=settings.app_version)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.cors_origins == "*" else settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
    return f"pbkdf2_sha256$200000${salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt, expected = stored_hash.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
    return hmac.compare_digest(digest, expected)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def validate_profile_photo(profile_photo: str | None) -> str | None:
    if not profile_photo:
        return None
    match = PROFILE_PHOTO_PATTERN.fullmatch(profile_photo)
    if not match:
        raise HTTPException(400, "Profile photo must be a PNG, JPEG, or WebP image")
    try:
        photo_bytes = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "Profile photo contains invalid base64 data")
    if len(photo_bytes) > MAX_PROFILE_PHOTO_BYTES:
        raise HTTPException(400, "Profile photo must be 256 KB or smaller")
    image_type = match.group(1)
    signatures_valid = (
        image_type == "png" and photo_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        or image_type == "jpeg" and photo_bytes.startswith(b"\xff\xd8\xff")
        or image_type == "webp" and photo_bytes.startswith(b"RIFF") and photo_bytes[8:12] == b"WEBP"
    )
    if not signatures_valid:
        raise HTTPException(400, "Profile photo content does not match its image type")
    return profile_photo


def validate_user_role(role: str) -> str:
    if role not in USER_ROLES:
        raise HTTPException(400, "Role must be admin, operator, or viewer")
    return role


def connector_secret_key() -> bytes:
    configured = os.getenv("CONNECTOR_SECRET_KEY")
    seed = configured or os.getenv("POSTGRES_PASSWORD") or settings.database_url
    return base64.urlsafe_b64encode(hashlib.sha256(seed.encode()).digest())


def connector_secret_cipher() -> Fernet:
    return Fernet(connector_secret_key())


def encrypt_connector_secret(payload: dict | None) -> str | None:
    if not payload:
        return None
    compact = {key: value for key, value in payload.items() if value not in (None, "", [], {})}
    if not compact:
        return None
    return connector_secret_cipher().encrypt(json.dumps(compact, separators=(",", ":")).encode()).decode()


def decrypt_connector_secret(connector: models.ConnectorProfile) -> dict:
    if not connector.secret_json_encrypted:
        return {}
    try:
        raw = connector_secret_cipher().decrypt(connector.secret_json_encrypted.encode()).decode()
        payload = json.loads(raw)
    except (InvalidToken, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(500, f"Stored connector credentials for {connector.name} could not be decrypted") from exc
    return payload if isinstance(payload, dict) else {}


def connector_secret_input(payload: schemas.ConnectorCreate) -> dict:
    secret = payload.credential_payload if isinstance(payload.credential_payload, dict) else {}
    normalized = {key: value for key, value in secret.items() if value not in (None, "", [], {})}
    if payload.password:
        normalized["password"] = payload.password
    return normalized


def connector_public(connector: models.ConnectorProfile) -> schemas.Connector:
    return schemas.Connector(
        id=connector.id,
        name=connector.name,
        connector_category=connector.connector_category,
        connector_type=connector.connector_type,
        endpoint=connector.endpoint,
        port=connector.port,
        username=connector.username,
        target_network=connector.target_network,
        target_datastore=connector.target_datastore,
        target_storage_pool=connector.target_storage_pool,
        target_vdc_name=connector.target_vdc_name,
        target_compute_name=connector.target_compute_name,
        credential_reference=connector.credential_reference,
        has_stored_secret=bool(connector.secret_json_encrypted),
        environment=connector.environment,
        status=connector.status,
        notes=connector.notes,
        created_at=connector.created_at,
        updated_at=connector.updated_at,
    )


def user_public(user: models.LocalUser) -> schemas.UserPublic:
    return schemas.UserPublic(
        id=user.id,
        username=user.username,
        role=user.role,
        is_active=user.is_active == "true",
        profile_photo=user.profile_photo,
    )


def active_admin_count(db: Session) -> int:
    return db.query(models.LocalUser).filter(
        models.LocalUser.role == "admin",
        models.LocalUser.is_active == "true",
    ).count()


def seed_defaults(db: Session) -> None:
    admin_username = os.getenv("ADMIN_INITIAL_USERNAME", "admin")
    admin_password = os.getenv("ADMIN_INITIAL_PASSWORD", "P@ssw0rd")
    if not db.query(models.LocalUser).filter(models.LocalUser.username == admin_username).first():
        db.add(models.LocalUser(username=admin_username, password_hash=hash_password(admin_password), role="admin"))
    app_settings = db.query(models.AppSetting).first()
    if not app_settings:
        db.add(models.AppSetting())
    elif app_settings.product_name.strip().lower() == "ds replace":
        app_settings.product_name = "DS Shift"
    db.commit()


def parsed_json_object(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parsed_json_array(value: str | None) -> list:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def raw_dashboard_summary(db: Session) -> schemas.DashboardSummary:
    plan_rows = db.query(models.MigrationPlan).all()
    plans = len(plan_rows)
    vms = db.query(models.VmInventory).all()
    status_counts = Counter(vm.current_status for vm in vms)
    migrated = status_counts["Validation completed"] + status_counts["Cutover completed"]
    failed = status_counts["Failed"] + status_counts["Rolled back"] + status_counts["Blocked"]
    planned_vm_ids = {
        int(vm_id)
        for plan in plan_rows
        for vm_id in parsed_json_array(plan.vm_ids_json)
        if isinstance(vm_id, int) or (isinstance(vm_id, str) and vm_id.isdigit())
    }
    planned = len(planned_vm_ids)
    progress = int((migrated / len(vms)) * 100) if vms else 0
    return schemas.DashboardSummary(
        total_plans=plans,
        vms_discovered=len(vms),
        vms_planned=planned,
        vms_migrated=migrated,
        vms_failed_or_blocked=failed,
        progress_percent=progress,
        by_status=dict(status_counts),
    )


def sync_spark_backed_plans(db: Session, plans: list[models.MigrationPlan]) -> None:
    changed = False
    for plan in plans:
        if not plan.spark_job_id:
            continue
        try:
            job = get_spark_job(plan.spark_job_id)
        except Exception:
            continue
        before = (plan.status, plan.results_json, plan.executed_at, plan.updated_at)
        apply_spark_job(db, plan, job)
        after = (plan.status, plan.results_json, plan.executed_at, plan.updated_at)
        if after != before:
            changed = True
    if changed:
        db.commit()


def normalize_host_value(raw: str | None) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    if "://" in value:
        parsed = urlparse(value)
        return parsed.hostname or value
    host_port = value.split("/", 1)[0]
    if "@" in host_port:
        host_port = host_port.split("@", 1)[1]
    if ":" in host_port and host_port.rsplit(":", 1)[1].isdigit():
        host_port = host_port.rsplit(":", 1)[0]
    return host_port or None


def normalize_connector_payload(payload: schemas.ConnectorCreate, connector_type: str) -> dict:
    endpoint = payload.endpoint.strip() if payload.endpoint else ""
    normalized = payload.model_dump(exclude={"password", "credential_payload"})
    if payload.connector_category != "host":
        normalized["endpoint"] = endpoint or None
        normalized["port"] = payload.port
        return normalized
    host = normalize_host_value(endpoint)
    if not host:
        raise HTTPException(400, "Host IP or hostname is required")
    username = (payload.username or "").strip() or None
    if connector_type == "KVM":
        user = username or "root"
        normalized["endpoint"] = f"qemu+ssh://{user}@{host}/system"
        normalized["port"] = 22
        normalized["username"] = user
    elif connector_type in {"VMware ESXi / vCenter", "VMware ESXi", "vCenter"}:
        normalized["endpoint"] = f"https://{host}/sdk"
        normalized["port"] = 443
    elif connector_type == "Nutanix AHV":
        normalized["endpoint"] = f"https://{host}:9440"
        normalized["port"] = 9440
    else:
        normalized["endpoint"] = host
    return normalized


def resolve_connector_defaults(connector: models.ConnectorProfile) -> dict:
    defaults = {}
    if connector.target_datastore:
        defaults["target_datastore"] = connector.target_datastore
    if connector.target_storage_pool:
        defaults["target_storage_pool"] = connector.target_storage_pool
    if connector.target_network:
        defaults["target_network"] = connector.target_network
    if connector.target_vdc_name:
        defaults["target_vdc_name"] = connector.target_vdc_name
        defaults["target_datacenter"] = connector.target_vdc_name
    if connector.target_compute_name:
        defaults["target_compute_name"] = connector.target_compute_name
    return defaults


def compact_execution_options(options: dict | None) -> dict:
    if not isinstance(options, dict):
        return {}
    blocked_keys = {
        "target_datastore",
        "target_storage_pool",
        "target_network",
        "target_vdc_name",
        "target_datacenter",
        "target_compute_name",
    }
    return {
        key: value
        for key, value in options.items()
        if key not in blocked_keys
        if value not in (None, "", [], {})
    }


def summarize_preflight_checks(checks: list[dict]) -> str:
    blocking = [check for check in checks if not check.get("ok")]
    if not blocking:
        return "Preflight passed"
    messages = []
    for check in blocking[:4]:
        label = str(check.get("check") or "check").replace("_", " ")
        vm_name = check.get("vm_name")
        detail = str(check.get("message") or "No details returned").strip()
        messages.append(f"{vm_name}: {label}: {detail}" if vm_name else f"{label}: {detail}")
    if len(blocking) > 4:
        messages.append(f"{len(blocking) - 4} more blocking check(s)")
    return "Preflight blocked: " + "; ".join(messages)


def validate_plan_vm_selection(vm_ids: list[int], db: Session) -> tuple[list[models.VmInventory], int]:
    normalized_ids = sorted(set(vm_ids))
    if not normalized_ids:
        raise HTTPException(400, "Select at least one VM")
    vms = db.query(models.VmInventory).filter(models.VmInventory.id.in_(normalized_ids)).all()
    if len(vms) != len(normalized_ids):
        raise HTTPException(404, "One or more selected VMs were not found")
    source_ids = {vm.connector_id for vm in vms}
    if None in source_ids or len(source_ids) != 1:
        raise HTTPException(400, "All selected VMs must come from the same discovered source connector")
    return vms, int(source_ids.pop())


def plan_ids_for_wave(wave: models.MigrationWave) -> list[int]:
    try:
        parsed = json.loads(wave.plan_ids_json or "[]")
    except json.JSONDecodeError:
        return []
    return sorted({int(plan_id) for plan_id in parsed if isinstance(plan_id, int) or str(plan_id).isdigit()})


def plans_for_wave(db: Session, plan_ids: list[int]) -> list[models.MigrationPlan]:
    if not plan_ids:
        return []
    plans = db.query(models.MigrationPlan).filter(models.MigrationPlan.id.in_(plan_ids)).all()
    if len(plans) != len(plan_ids):
        raise HTTPException(404, "One or more migration plans were not found")
    plan_by_id = {plan.id: plan for plan in plans}
    return [plan_by_id[plan_id] for plan_id in plan_ids]


def ensure_wave_plan_ids_available(db: Session, plan_ids: list[int], *, excluding_wave_id: int | None = None) -> None:
    if not plan_ids:
        return
    conflicting: list[str] = []
    for wave in db.query(models.MigrationWave).all():
        if excluding_wave_id and wave.id == excluding_wave_id:
            continue
        overlap = sorted(set(plan_ids) & set(plan_ids_for_wave(wave)))
        if overlap:
            conflicting.append(f'{wave.wave_name} ({", ".join(str(plan_id) for plan_id in overlap)})')
    if conflicting:
        raise HTTPException(409, f"Migration plan already belongs to another wave: {'; '.join(conflicting)}")


def sync_wave_vm_membership(db: Session, wave: models.MigrationWave, plans: list[models.MigrationPlan]) -> None:
    vm_ids = sorted({vm_id for plan in plans for vm_id in json.loads(plan.vm_ids_json or "[]")})
    current_vms = db.query(models.VmInventory).filter(models.VmInventory.wave_id == wave.id).all()
    current_vm_ids = {vm.id for vm in current_vms}
    desired_vm_ids = set(vm_ids)
    for vm in current_vms:
        if vm.id not in desired_vm_ids:
            vm.wave_id = None
            vm.migration_wave = None
    if desired_vm_ids:
        desired_vms = db.query(models.VmInventory).filter(models.VmInventory.id.in_(desired_vm_ids)).all()
        for vm in desired_vms:
            vm.wave_id = wave.id
            vm.migration_wave = wave.wave_name
        if len(desired_vms) != len(desired_vm_ids):
            missing_vm_ids = sorted(desired_vm_ids - {vm.id for vm in desired_vms})
            raise HTTPException(409, f"Wave references VMs that no longer exist: {missing_vm_ids}")
    elif current_vm_ids:
        for vm in current_vms:
            vm.wave_id = None
            vm.migration_wave = None


def validate_live_migration_plan(plan: models.MigrationPlan, db: Session) -> tuple[models.ConnectorProfile, models.ConnectorProfile, list[models.VmInventory]]:
    if plan.status in {"Queued", "Running"}:
        raise HTTPException(409, f'Migration plan "{plan.name}" already has an active Spark Engine job')
    source = db.get(models.ConnectorProfile, plan.source_connector_id)
    target = db.get(models.ConnectorProfile, plan.target_connector_id)
    if not source or not target:
        raise HTTPException(404, f'Source or target connector not found for migration plan "{plan.name}"')
    vm_ids = json.loads(plan.vm_ids_json or "[]")
    if not vm_ids:
        raise HTTPException(409, f'Migration plan "{plan.name}" has no assigned VMs')
    vms = db.query(models.VmInventory).filter(models.VmInventory.id.in_(vm_ids)).all()
    if len(vms) != len(vm_ids):
        raise HTTPException(409, f'One or more VMs for migration plan "{plan.name}" no longer exist')
    return source, target, vms


def queue_live_migration_plan(plan: models.MigrationPlan, db: Session, admin: models.LocalUser) -> dict:
    return queue_live_migration_plan_with_options(plan, db, admin)


def queue_live_migration_plan_with_options(plan: models.MigrationPlan, db: Session, admin: models.LocalUser, *, extra_options: dict | None = None) -> dict:
    source, target, vms = validate_live_migration_plan(plan, db)
    request = migration_plan_execution_payload(plan, source, target, vms, admin.username, live=True, extra_options=extra_options)
    try:
        job = create_spark_job(request)
    except ValueError as exc:
        raise HTTPException(409, f"Spark Engine rejected execution: {exc}") from exc
    except Exception as exc:
        raise HTTPException(503, f"Spark Engine unavailable: {exc}") from exc
    plan.spark_job_id = job["id"]
    plan.status = job["status"]
    return job


def stage_safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.")
    if not cleaned:
        raise HTTPException(409, "Migration plan contains a VM name that cannot be mapped to a staging path")
    return cleaned[:80]


def stage_plan_directory_name(plan_id: int, plan_name: str | None = None) -> str:
    if plan_name:
        return f"Plan-{stage_safe_name(plan_name)}"
    return f"plan-{plan_id}"


def plan_stage_directory(plan: models.MigrationPlan) -> Path:
    return STAGING_ROOT / stage_plan_directory_name(plan.id, plan.name)


def workload_stage_directory(plan_id: int, vm: models.VmInventory, plan_name: str | None = None) -> Path:
    return STAGING_ROOT / stage_plan_directory_name(plan_id, plan_name) / stage_safe_name(vm.vm_name)


def workload_stage_directories(plan: models.MigrationPlan, vm: models.VmInventory) -> list[Path]:
    preferred = workload_stage_directory(plan.id, vm, plan.name)
    legacy = STAGING_ROOT / stage_plan_directory_name(plan.id, None) / f"vm-{vm.id}-{stage_safe_name(vm.vm_name)}"
    seen: set[str] = set()
    ordered: list[Path] = []
    for path in [preferred, legacy]:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(path)
    return ordered


def plan_resume_metadata(plan: models.MigrationPlan, vms: list[models.VmInventory]) -> dict:
    if plan.status != "Failed":
        return {"allowed": False, "reason": "Only failed migration plans can continue from preserved staging"}
    if plan.migration_type not in {"KVM to VMware ESXi / vCenter", "VMware ESXi / vCenter to KVM"}:
        return {"allowed": False, "reason": "Continue is only available for host-path migrations that preserve staging"}
    results = parsed_json_array(plan.results_json)
    vm_result_by_id = {row.get("vm_id"): row for row in results if isinstance(row, dict) and row.get("vm_id")}
    resumable = []
    reasons = []
    for vm in vms:
        result = vm_result_by_id.get(vm.id, {})
        if result.get("can_resume") is not True:
            stage_path = next((path for path in workload_stage_directories(plan, vm) if path.exists()), workload_stage_directories(plan, vm)[0])
            resumable.append({"vm_id": vm.id, "vm_name": vm.vm_name, "stage_path": str(stage_path), "reuse_ready": False})
            continue
        candidate_paths = workload_stage_directories(plan, vm)
        stage_path = next((path for path in candidate_paths if path.exists()), candidate_paths[0])
        artifacts = []
        if stage_path.exists():
            artifacts = list(stage_path.glob("*.vmdk")) + list(stage_path.glob("*.qcow2")) + list(stage_path.glob("*.xml"))
        resumable.append({"vm_id": vm.id, "vm_name": vm.vm_name, "stage_path": str(stage_path), "reuse_ready": bool(artifacts)})
    if len(resumable) != len(vms):
        return {"allowed": False, "reason": reasons[0] if reasons else "Not every VM in the plan can continue"}
    return {"allowed": True, "workloads": resumable}


def append_migration_log(plan: models.MigrationPlan, vm: models.VmInventory, result: dict, *, when: datetime) -> None:
    STAGING_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = STAGING_ROOT / "migrated-vms.log"
    target_name = result.get("target_name") or vm.vm_name
    line = (
        f"{when.isoformat()}Z | plan={plan.name} | vm_id={vm.id} | vm={vm.vm_name} | "
        f"target={target_name} | migration={plan.migration_type}\n"
    )
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def current_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> models.LocalUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Authentication required")
    token = authorization.split(" ", 1)[1].strip()
    session = db.query(models.AuthSession).filter(models.AuthSession.token_hash == hash_token(token)).first()
    if not session or session.expires_at < datetime.utcnow():
        raise HTTPException(401, "Invalid or expired session")
    user = db.get(models.LocalUser, session.user_id)
    if not user or user.is_active != "true":
        raise HTTPException(401, "Inactive user")
    return user


def admin_user(user: models.LocalUser = Depends(current_user)) -> models.LocalUser:
    if user.role != "admin":
        raise HTTPException(403, "Admin role required")
    return user


@app.on_event("startup")
def startup() -> None:
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(84752002)"))
        Base.metadata.create_all(bind=connection)
        connection.execute(text("ALTER TABLE local_users ADD COLUMN IF NOT EXISTS profile_photo TEXT"))
        connection.execute(text("ALTER TABLE vm_inventory ALTER COLUMN project_id DROP NOT NULL"))
        connection.execute(text("ALTER TABLE vm_inventory ADD COLUMN IF NOT EXISTS connector_id INTEGER REFERENCES connector_profiles(id) ON DELETE SET NULL"))
        connection.execute(text("ALTER TABLE vm_inventory ADD COLUMN IF NOT EXISTS external_id VARCHAR(255)"))
        connection.execute(text("ALTER TABLE vm_inventory ADD COLUMN IF NOT EXISTS host_name VARCHAR(255)"))
        connection.execute(text("ALTER TABLE vm_inventory ADD COLUMN IF NOT EXISTS details_json TEXT NOT NULL DEFAULT '{}'"))
        connection.execute(text("ALTER TABLE connector_profiles ADD COLUMN IF NOT EXISTS secret_json_encrypted TEXT"))
        connection.execute(text("ALTER TABLE connector_profiles ADD COLUMN IF NOT EXISTS target_network VARCHAR(160)"))
        connection.execute(text("ALTER TABLE connector_profiles ADD COLUMN IF NOT EXISTS target_datastore VARCHAR(160)"))
        connection.execute(text("ALTER TABLE connector_profiles ADD COLUMN IF NOT EXISTS target_storage_pool VARCHAR(160)"))
        connection.execute(text("ALTER TABLE connector_profiles ADD COLUMN IF NOT EXISTS target_vdc_name VARCHAR(160)"))
        connection.execute(text("ALTER TABLE connector_profiles ADD COLUMN IF NOT EXISTS target_compute_name VARCHAR(160)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_vm_inventory_external_id ON vm_inventory (external_id)"))
        connection.execute(text("ALTER TABLE migration_plans ADD COLUMN IF NOT EXISTS execution_options_json TEXT NOT NULL DEFAULT '{}'"))
        connection.execute(text("ALTER TABLE migration_plans ADD COLUMN IF NOT EXISTS keep_source_vm BOOLEAN NOT NULL DEFAULT true"))
        connection.execute(text("ALTER TABLE migration_plans ADD COLUMN IF NOT EXISTS spark_job_id INTEGER"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_migration_plans_spark_job_id ON migration_plans (spark_job_id)"))
        connection.execute(text("ALTER TABLE migration_waves ALTER COLUMN project_id DROP NOT NULL"))
        connection.execute(text("ALTER TABLE migration_waves ADD COLUMN IF NOT EXISTS plan_ids_json TEXT NOT NULL DEFAULT '[]'"))
        with Session(bind=connection) as db:
            seed_defaults(db)


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("select 1"))
    return {"status": "ok", "application": settings.app_name, "version": settings.app_version}


@app.get("/api/about")
def about():
    return {
        "product": "DS Shift",
        "brand": "Defined Solutions",
        "version": settings.app_version,
        "purpose": "VM migration planning, controlled execution, and tracking platform.",
    }


@app.post("/api/auth/login", response_model=schemas.LoginResponse)
def login(payload: schemas.LoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.LocalUser).filter(models.LocalUser.username == payload.username).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Invalid username or password")
    token = secrets.token_urlsafe(32)
    db.add(models.AuthSession(user_id=user.id, token_hash=hash_token(token), expires_at=datetime.utcnow() + timedelta(hours=12)))
    db.commit()
    return schemas.LoginResponse(access_token=token, username=user.username, role=user.role)


@app.post("/api/auth/logout", status_code=204)
def logout(authorization: str | None = Header(default=None), db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    token = authorization.split(" ", 1)[1].strip()
    session = db.query(models.AuthSession).filter(models.AuthSession.token_hash == hash_token(token)).first()
    if session:
        db.delete(session)
        db.commit()
    return Response(status_code=204)


@app.get("/api/auth/me", response_model=schemas.UserPublic)
def me(user: models.LocalUser = Depends(current_user)):
    return user_public(user)


@app.get("/api/users", response_model=list[schemas.UserPublic])
def list_users(db: Session = Depends(get_db), _admin: models.LocalUser = Depends(admin_user)):
    return [user_public(user) for user in db.query(models.LocalUser).order_by(models.LocalUser.username).all()]


@app.post("/api/users", response_model=schemas.UserPublic, status_code=201)
def create_user(payload: schemas.UserCreate, db: Session = Depends(get_db), _admin: models.LocalUser = Depends(admin_user)):
    username = payload.username.strip()
    if not username:
        raise HTTPException(400, "Username is required")
    if not payload.password:
        raise HTTPException(400, "Password is required")
    if db.query(models.LocalUser).filter(models.LocalUser.username == username).first():
        raise HTTPException(409, "Username already exists")
    user = models.LocalUser(
        username=username,
        password_hash=hash_password(payload.password),
        role=validate_user_role(payload.role),
        is_active="true" if payload.is_active else "false",
        profile_photo=validate_profile_photo(payload.profile_photo),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user_public(user)


@app.put("/api/users/{user_id}", response_model=schemas.UserPublic)
def update_user(user_id: int, payload: schemas.UserUpdate, db: Session = Depends(get_db), admin: models.LocalUser = Depends(admin_user)):
    user = db.get(models.LocalUser, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    next_role = validate_user_role(payload.role) if payload.role is not None else user.role
    next_active = payload.is_active if payload.is_active is not None else user.is_active == "true"
    if user.id == admin.id and (next_role != "admin" or not next_active):
        raise HTTPException(400, "You cannot demote or deactivate your own account")
    if user.role == "admin" and user.is_active == "true" and (next_role != "admin" or not next_active) and active_admin_count(db) <= 1:
        raise HTTPException(400, "At least one active administrator is required")
    if payload.username is not None:
        username = payload.username.strip()
        if not username:
            raise HTTPException(400, "Username is required")
        existing = db.query(models.LocalUser).filter(
            models.LocalUser.username == username,
            models.LocalUser.id != user.id,
        ).first()
        if existing:
            raise HTTPException(409, "Username already exists")
        user.username = username
    if payload.password:
        user.password_hash = hash_password(payload.password)
    user.role = next_role
    user.is_active = "true" if next_active else "false"
    if "profile_photo" in payload.model_fields_set:
        user.profile_photo = validate_profile_photo(payload.profile_photo)
    db.commit()
    db.refresh(user)
    return user_public(user)


@app.delete("/api/users/{user_id}", status_code=204)
def delete_user(user_id: int, db: Session = Depends(get_db), admin: models.LocalUser = Depends(admin_user)):
    user = db.get(models.LocalUser, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if user.id == admin.id:
        raise HTTPException(400, "You cannot delete your own account")
    if user.role == "admin" and user.is_active == "true" and active_admin_count(db) <= 1:
        raise HTTPException(400, "At least one active administrator is required")
    db.delete(user)
    db.commit()
    return Response(status_code=204)


@app.get("/api/dashboard", response_model=schemas.DashboardSummary)
def dashboard(db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    sync_spark_backed_plans(db, db.query(models.MigrationPlan).filter(models.MigrationPlan.spark_job_id.is_not(None)).all())
    return raw_dashboard_summary(db)


@app.get("/api/projects", response_model=list[schemas.Project])
def list_projects(db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    return db.query(models.MigrationProject).order_by(models.MigrationProject.created_at.desc()).all()


@app.post("/api/projects", response_model=schemas.Project, status_code=201)
def create_project(payload: schemas.ProjectCreate, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    project = models.MigrationProject(**payload.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@app.get("/api/projects/{project_id}", response_model=schemas.Project)
def get_project(project_id: int, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    project = db.get(models.MigrationProject, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


@app.put("/api/projects/{project_id}", response_model=schemas.Project)
def update_project(project_id: int, payload: schemas.ProjectCreate, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    project = db.get(models.MigrationProject, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    for key, value in payload.model_dump().items():
        setattr(project, key, value)
    db.commit()
    db.refresh(project)
    return project


@app.delete("/api/projects/{project_id}", status_code=204)
def delete_project(project_id: int, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    project = db.get(models.MigrationProject, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    db.delete(project)
    db.commit()
    return Response(status_code=204)


@app.get("/api/platforms", response_model=list[schemas.Platform])
def list_platforms(db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    return db.query(models.PlatformProfile).order_by(models.PlatformProfile.name).all()


@app.post("/api/platforms", response_model=schemas.Platform, status_code=201)
def create_platform(payload: schemas.PlatformCreate, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    platform = models.PlatformProfile(**payload.model_dump())
    db.add(platform)
    db.commit()
    db.refresh(platform)
    return platform


@app.get("/api/connectors", response_model=list[schemas.Connector])
def list_connectors(category: str | None = None, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    query = db.query(models.ConnectorProfile)
    if category:
        query = query.filter(models.ConnectorProfile.connector_category == category)
    return [connector_public(connector) for connector in query.order_by(models.ConnectorProfile.created_at.desc()).all()]


@app.get("/api/connector-platforms")
def list_connector_platforms(_user: models.LocalUser = Depends(current_user)):
    return {"categories": CONNECTOR_PLATFORMS, "engines": connector_engine_status()}


@app.post("/api/connectors", response_model=schemas.Connector, status_code=201)
def create_connector(payload: schemas.ConnectorCreate, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    try:
        connector_type = validate_connector_platform(payload.connector_category, payload.connector_type)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    values = normalize_connector_payload(payload, connector_type)
    connector = models.ConnectorProfile(
        **{
            **values,
            "connector_type": connector_type,
            "status": "Not validated",
            "secret_json_encrypted": encrypt_connector_secret(connector_secret_input(payload)),
        }
    )
    db.add(connector)
    db.commit()
    db.refresh(connector)
    return connector_public(connector)


@app.put("/api/connectors/{connector_id}", response_model=schemas.Connector)
def update_connector(connector_id: int, payload: schemas.ConnectorCreate, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    connector = db.get(models.ConnectorProfile, connector_id)
    if not connector:
        raise HTTPException(404, "Connector not found")
    try:
        connector_type = validate_connector_platform(payload.connector_category, payload.connector_type)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    values = normalize_connector_payload(payload, connector_type)
    for key, value in {**values, "connector_type": connector_type, "status": "Not validated"}.items():
        setattr(connector, key, value)
    replacement_secret = connector_secret_input(payload)
    if replacement_secret:
        connector.secret_json_encrypted = encrypt_connector_secret(replacement_secret)
    elif payload.credential_reference:
        connector.secret_json_encrypted = None
    db.commit()
    db.refresh(connector)
    return connector_public(connector)


@app.delete("/api/connectors/{connector_id}", status_code=204)
def delete_connector(connector_id: int, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    connector = db.get(models.ConnectorProfile, connector_id)
    if not connector:
        raise HTTPException(404, "Connector not found")
    referenced_plans = db.query(models.MigrationPlan).filter(
        (models.MigrationPlan.source_connector_id == connector_id)
        | (models.MigrationPlan.target_connector_id == connector_id)
    ).count()
    if referenced_plans:
        raise HTTPException(
            409,
            f"Connector is referenced by {referenced_plans} migration plan(s) and cannot be deleted",
        )
    db.query(models.MigrationJob).filter(
        (models.MigrationJob.source_connector_id == connector_id)
        | (models.MigrationJob.target_connector_id == connector_id)
    ).delete(synchronize_session=False)
    db.query(models.DiscoveryRun).filter(models.DiscoveryRun.connector_id == connector_id).delete(synchronize_session=False)
    db.query(models.HostInventory).filter(models.HostInventory.connector_id == connector_id).delete(synchronize_session=False)
    db.query(models.VmInventory).filter(models.VmInventory.connector_id == connector_id).delete(synchronize_session=False)
    db.delete(connector)
    db.commit()


@app.post("/api/connectors/{connector_id}/validate", response_model=schemas.ConnectorValidationResult)
def validate_connector(connector_id: int, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    connector = db.get(models.ConnectorProfile, connector_id)
    if not connector:
        raise HTTPException(404, "Connector not found")
    try:
        payload = call_connector_engine(connector, "validate", credential_payload=decrypt_connector_secret(connector))
        connector.status = "Validated" if payload.get("ok") else "Validation failed"
        message = payload.get("message", "Connector engine returned no message")
        commands = payload.get("commands", [])
        status = connector.status
    except Exception as exc:
        connector.status = "Validation failed"
        message = f"{connector.connector_category.title()} Connector unavailable or failed: {exc}"
        commands = []
        status = connector.status
    db.commit()
    db.refresh(connector)
    return schemas.ConnectorValidationResult(connector=connector_public(connector), status=status, message=message, commands=commands)


@app.post("/api/connectors/{connector_id}/discover", response_model=schemas.DiscoveryRun)
def discover_connector(connector_id: int, payload: schemas.DiscoveryRequest, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    connector = db.get(models.ConnectorProfile, connector_id)
    if not connector:
        raise HTTPException(404, "Connector not found")
    try:
        result = call_connector_engine(connector, "discover", credential_payload=decrypt_connector_secret(connector))
        imported = 0
        records = result.get("records", [])
        hosts = result.get("hosts", [])
        discovered_hosts = 0
        if result.get("ok") and connector.connector_category == "host":
            discovered_hosts = sync_discovered_hosts(db, connector, hosts, records)
        if result.get("ok"):
            synced_vms = sync_discovered_vms(db, connector, records)
        else:
            synced_vms = 0
        connector.status = "Discovered" if result.get("ok") else "Discovery failed"
        if result.get("ok") and payload.import_to_project_id:
            imported = import_discovered_vms(db, payload.import_to_project_id, payload.target_platform or "Unassigned", records)
        message = result.get("message", "Connector engine returned no message")
        if discovered_hosts:
            message += f"; updated {discovered_hosts} host inventory record(s)"
        if synced_vms:
            message += f"; synchronized {synced_vms} VM inventory record(s)"
        if imported:
            message += f"; imported {imported} VMs"
        run = models.DiscoveryRun(
            connector_id=connector.id,
            status="Completed" if result.get("ok") else "Failed",
            message=message,
            records_json=json.dumps(records),
            commands_json=json.dumps(result.get("commands", [])),
        )
    except Exception as exc:
        connector.status = "Discovery failed"
        run = models.DiscoveryRun(
            connector_id=connector.id,
            status="Failed",
            message=f"{connector.connector_category.title()} Connector unavailable or failed: {exc}",
            records_json="[]",
            commands_json="[]",
        )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def sync_discovered_hosts(db: Session, connector: models.ConnectorProfile, hosts: list[dict], records: list[dict]) -> int:
    grouped_vms: dict[str, list[dict]] = {}
    for record in records:
        key = str(record.get("host_key") or record.get("host_name") or connector.endpoint or connector.name)
        grouped_vms.setdefault(key, []).append(record)

    normalized_hosts = hosts or [
        {
            "host_key": connector.endpoint or connector.name,
            "host_name": connector.endpoint or connector.name,
            "platform": connector.connector_type,
            "endpoint": connector.endpoint,
            "status": "Discovered",
        }
    ]
    seen_keys = set()
    for host in normalized_hosts:
        host_key = str(host.get("host_key") or host.get("host_name") or connector.endpoint or connector.name)
        seen_keys.add(host_key)
        host_vms = grouped_vms.get(host_key, [])
        row = db.query(models.HostInventory).filter(
            models.HostInventory.connector_id == connector.id,
            models.HostInventory.host_key == host_key,
        ).first()
        values = {
            "host_name": host.get("host_name") or host_key,
            "platform": host.get("platform") or connector.connector_type,
            "endpoint": host.get("endpoint") or connector.endpoint,
            "environment": connector.environment,
            "status": host.get("status") or "Discovered",
            "cpu": host.get("cpu") or 0,
            "memory_gb": host.get("memory_gb") or 0,
            "vm_count": len(host_vms),
            "vms_json": json.dumps(host_vms),
            "details_json": json.dumps(host.get("details") or {}),
            "last_discovered_at": datetime.utcnow(),
        }
        if row:
            for key, value in values.items():
                setattr(row, key, value)
        else:
            db.add(models.HostInventory(connector_id=connector.id, host_key=host_key, **values))

    for host_key, host_vms in grouped_vms.items():
        if host_key in seen_keys:
            continue
        db.add(
            models.HostInventory(
                connector_id=connector.id,
                host_key=host_key,
                host_name=host_vms[0].get("host_name") or host_key,
                platform=connector.connector_type,
                endpoint=connector.endpoint,
                environment=connector.environment,
                status="Discovered",
                vm_count=len(host_vms),
                vms_json=json.dumps(host_vms),
                details_json="{}",
                last_discovered_at=datetime.utcnow(),
            )
        )
    db.query(models.HostInventory).filter(
        models.HostInventory.connector_id == connector.id,
        ~models.HostInventory.host_key.in_(seen_keys | set(grouped_vms)),
    ).delete(synchronize_session=False)
    db.commit()
    return len(seen_keys | set(grouped_vms))


@app.get("/api/hosts", response_model=list[schemas.HostInventory])
def list_hosts(db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    return db.query(models.HostInventory).order_by(models.HostInventory.host_name).all()


def sync_discovered_vms(db: Session, connector: models.ConnectorProfile, records: list[dict]) -> int:
    count = 0
    for record in records:
        name = record.get("vm_name")
        if not name:
            continue
        external_id = record.get("external_id") or record.get("instance_id") or record.get("vm_id")
        host_name = record.get("host_name")
        existing = None
        if external_id:
            existing = db.query(models.VmInventory).filter(
                models.VmInventory.connector_id == connector.id,
                models.VmInventory.external_id == str(external_id),
            ).first()
            if not existing:
                existing = db.query(models.VmInventory).filter(
                    models.VmInventory.connector_id == connector.id,
                    models.VmInventory.external_id.is_(None),
                    models.VmInventory.host_name == host_name,
                    models.VmInventory.vm_name == name,
                ).first()
        else:
            existing = db.query(models.VmInventory).filter(
                models.VmInventory.connector_id == connector.id,
                models.VmInventory.host_name == host_name,
                models.VmInventory.vm_name == name,
            ).first()
        if not existing and external_id:
            existing = adopt_legacy_discovered_vm(db, connector, record)
        values = {
            "project_id": None,
            "connector_id": connector.id,
            "external_id": str(external_id) if external_id else None,
            "host_name": host_name,
            "source_platform": record.get("source_platform") or connector.connector_type,
            "cpu": record.get("cpu") or 0,
            "memory_gb": record.get("memory_gb") or 0,
            "disk_gb": record.get("disk_gb") or 0,
            "os_type": record.get("os_type") or "Unknown",
            "ip_address": record.get("ip_address"),
            "details_json": json.dumps(record),
        }
        if existing:
            existing.vm_name = name
            for key, value in values.items():
                setattr(existing, key, value)
        else:
            vm = models.VmInventory(
                vm_name=name,
                target_platform="Unassigned",
                criticality="Medium",
                current_status="Discovered",
                **values,
            )
            db.add(vm)
            db.flush()
            db.add(models.VmStatusHistory(vm_id=vm.id, status="Discovered", note=f"Discovered through connector {connector.name}"))
        count += 1
    db.commit()
    return count


def adopt_legacy_discovered_vm(db: Session, connector: models.ConnectorProfile, record: dict) -> models.VmInventory | None:
    source_platform = record.get("source_platform") or connector.connector_type
    cpu = int(record.get("cpu") or 0)
    memory_gb = int(record.get("memory_gb") or 0)
    disk_gb = int(record.get("disk_gb") or 0)
    ip_address = record.get("ip_address")
    os_type = record.get("os_type")
    host_name = record.get("host_name")
    path = record.get("path")

    candidates = db.query(models.VmInventory).filter(
        models.VmInventory.connector_id == connector.id,
        models.VmInventory.external_id.is_(None),
        models.VmInventory.source_platform == source_platform,
    ).all()

    ranked: list[tuple[int, models.VmInventory]] = []
    for candidate in candidates:
        score = 0
        if path:
            details = parsed_json_object(candidate.details_json)
            if details.get("path") == path:
                score += 5
        if host_name and candidate.host_name == host_name:
            score += 3
        if ip_address and candidate.ip_address == ip_address:
            score += 4
        if cpu and candidate.cpu == cpu:
            score += 1
        if memory_gb and candidate.memory_gb == memory_gb:
            score += 1
        if disk_gb and candidate.disk_gb == disk_gb:
            score += 1
        if os_type and candidate.os_type == os_type:
            score += 1
        if score >= 5:
            ranked.append((score, candidate))

    if not ranked:
        return None
    ranked.sort(key=lambda row: row[0], reverse=True)
    top_score = ranked[0][0]
    top_candidates = [candidate for score, candidate in ranked if score == top_score]
    if len(top_candidates) != 1:
        return None
    return top_candidates[0]


def import_discovered_vms(db: Session, project_id: int, target_platform: str, records: list[dict]) -> int:
    if not db.get(models.MigrationProject, project_id):
        raise HTTPException(404, "Import project not found")
    count = 0
    for record in records:
        name = record.get("vm_name")
        if not name:
            continue
        existing = db.query(models.VmInventory).filter(models.VmInventory.project_id == project_id, models.VmInventory.vm_name == name).first()
        if existing:
            existing.cpu = record.get("cpu") or existing.cpu
            existing.memory_gb = record.get("memory_gb") or existing.memory_gb
            existing.os_type = record.get("os_type") or existing.os_type
        else:
            db.add(
                models.VmInventory(
                    project_id=project_id,
                    vm_name=name,
                    source_platform=record.get("source_platform") or "Unknown",
                    target_platform=target_platform,
                    cpu=record.get("cpu") or 0,
                    memory_gb=record.get("memory_gb") or 0,
                    disk_gb=record.get("disk_gb") or 0,
                    os_type=record.get("os_type"),
                    ip_address=record.get("ip_address"),
                    current_status="Discovered",
                )
            )
        count += 1
    db.commit()
    return count


@app.get("/api/waves", response_model=list[schemas.Wave])
def list_waves(db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    return db.query(models.MigrationWave).order_by(models.MigrationWave.created_at.desc()).all()


@app.post("/api/waves", response_model=schemas.Wave, status_code=201)
def create_wave(payload: schemas.WaveCreate, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    wave_name = payload.wave_name.strip()
    if not wave_name:
        raise HTTPException(400, "Migration wave name is required")
    normalized_plan_ids = sorted(set(payload.plan_ids))
    ensure_wave_plan_ids_available(db, normalized_plan_ids)
    plans = plans_for_wave(db, normalized_plan_ids)
    wave = models.MigrationWave(
        project_id=payload.project_id,
        wave_name=wave_name,
        planned_window=payload.planned_window,
        status=payload.status,
        notes=payload.notes,
        plan_ids_json=json.dumps(normalized_plan_ids),
    )
    db.add(wave)
    db.flush()
    sync_wave_vm_membership(db, wave, plans)
    db.commit()
    db.refresh(wave)
    return wave


@app.put("/api/waves/{wave_id}", response_model=schemas.Wave)
def update_wave(wave_id: int, payload: schemas.WaveUpdate, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    wave = db.get(models.MigrationWave, wave_id)
    if not wave:
        raise HTTPException(404, "Migration wave not found")
    wave_name = payload.wave_name.strip()
    if not wave_name:
        raise HTTPException(400, "Migration wave name is required")
    normalized_plan_ids = sorted(set(payload.plan_ids))
    ensure_wave_plan_ids_available(db, normalized_plan_ids, excluding_wave_id=wave.id)
    plans = plans_for_wave(db, normalized_plan_ids)
    wave.project_id = payload.project_id
    wave.wave_name = wave_name
    wave.planned_window = payload.planned_window
    wave.status = payload.status
    wave.notes = payload.notes
    wave.plan_ids_json = json.dumps(normalized_plan_ids)
    sync_wave_vm_membership(db, wave, plans)
    db.commit()
    db.refresh(wave)
    return wave


@app.delete("/api/waves/{wave_id}", status_code=204)
def delete_wave(wave_id: int, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    wave = db.get(models.MigrationWave, wave_id)
    if not wave:
        raise HTTPException(404, "Migration wave not found")
    vms = db.query(models.VmInventory).filter(models.VmInventory.wave_id == wave.id).all()
    for vm in vms:
        vm.wave_id = None
        vm.migration_wave = None
    db.delete(wave)
    db.commit()


@app.post("/api/waves/{wave_id}/execute")
def execute_wave(
    wave_id: int,
    payload: schemas.WaveExecution,
    db: Session = Depends(get_db),
    admin: models.LocalUser = Depends(admin_user),
):
    wave = db.get(models.MigrationWave, wave_id)
    if not wave:
        raise HTTPException(404, "Migration wave not found")
    if payload.confirmation != wave.wave_name:
        raise HTTPException(400, "Type the exact migration wave name to approve live execution")
    plan_ids = plan_ids_for_wave(wave)
    if not plan_ids:
        raise HTTPException(409, "Migration wave has no migration plans to execute")
    plans = plans_for_wave(db, plan_ids)
    for plan in plans:
        validate_live_migration_plan(plan, db)
    jobs = []
    for plan in plans:
        job = queue_live_migration_plan(plan, db, admin)
        jobs.append({"plan": schemas.MigrationPlan.model_validate(plan), "job": job})
    wave.status = "Queued"
    db.commit()
    db.refresh(wave)
    return {"wave": schemas.Wave.model_validate(wave), "jobs": jobs}


@app.get("/api/vms", response_model=list[schemas.Vm])
def list_vms(db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    return db.query(models.VmInventory).order_by(models.VmInventory.created_at.desc()).all()


@app.post("/api/vms", response_model=schemas.Vm, status_code=201)
def create_vm(payload: schemas.VmCreate, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    if payload.current_status not in MIGRATION_STATUSES:
        raise HTTPException(400, "Unsupported migration status")
    vm = models.VmInventory(**payload.model_dump())
    db.add(vm)
    db.flush()
    db.add(models.VmStatusHistory(vm_id=vm.id, status=vm.current_status, note="Initial inventory entry"))
    db.commit()
    db.refresh(vm)
    return vm


@app.patch("/api/vms/{vm_id}/status", response_model=schemas.Vm)
def update_vm_status(vm_id: int, payload: schemas.StatusUpdate, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    if payload.status not in MIGRATION_STATUSES:
        raise HTTPException(400, "Unsupported migration status")
    vm = db.get(models.VmInventory, vm_id)
    if not vm:
        raise HTTPException(404, "VM not found")
    vm.current_status = payload.status
    db.add(models.VmStatusHistory(vm_id=vm.id, status=payload.status, note=payload.note))
    db.commit()
    db.refresh(vm)
    return vm


@app.get("/api/vms/{vm_id}/history", response_model=list[schemas.StatusHistory])
def vm_history(vm_id: int, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    return (
        db.query(models.VmStatusHistory)
        .filter(models.VmStatusHistory.vm_id == vm_id)
        .order_by(models.VmStatusHistory.changed_at.desc())
        .all()
    )


@app.get("/api/reports/readiness")
def readiness_report(db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    rows = db.query(models.VmInventory).all()
    return [
        {
            "vm_name": vm.vm_name,
            "project_id": vm.project_id,
            "source_platform": vm.source_platform,
            "target_platform": vm.target_platform,
            "criticality": vm.criticality,
            "status": vm.current_status,
            "ready": vm.current_status in {"Ready for migration", "Replication prepared", "Cutover scheduled"},
        }
        for vm in rows
    ]


@app.get("/api/settings", response_model=schemas.AppSettings)
def get_settings(db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    settings_row = db.query(models.AppSetting).first()
    if not settings_row:
        settings_row = models.AppSetting()
        db.add(settings_row)
        db.commit()
        db.refresh(settings_row)
    return settings_row


@app.get("/api/service-status")
def service_status(_user: models.LocalUser = Depends(current_user)):
    return get_service_statuses()


@app.put("/api/settings", response_model=schemas.AppSettings)
def update_settings(payload: schemas.SettingsUpdate, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    settings_row = db.query(models.AppSetting).first()
    if not settings_row:
        settings_row = models.AppSetting()
        db.add(settings_row)
    for key, value in payload.model_dump().items():
        setattr(settings_row, key, value)
    db.commit()
    db.refresh(settings_row)
    return settings_row


@app.get("/api/discovery-runs", response_model=list[schemas.DiscoveryRun])
def list_discovery_runs(db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    return db.query(models.DiscoveryRun).order_by(models.DiscoveryRun.created_at.desc()).all()


@app.get("/api/migration-jobs", response_model=list[schemas.MigrationJob])
def list_migration_jobs(db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    return db.query(models.MigrationJob).order_by(models.MigrationJob.created_at.desc()).all()


@app.get("/api/migration-plans", response_model=list[schemas.MigrationPlan])
def list_migration_plans(db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    plans = db.query(models.MigrationPlan).order_by(models.MigrationPlan.created_at.desc()).all()
    sync_spark_backed_plans(db, plans)
    for plan in plans:
        db.refresh(plan)
    return plans


@app.post("/api/migration-plans", response_model=schemas.MigrationPlan, status_code=201)
def create_migration_plan(payload: schemas.MigrationPlanCreate, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Migration plan name is required")
    if db.query(models.MigrationPlan).filter(models.MigrationPlan.name == name).first():
        raise HTTPException(409, "Migration plan name already exists")
    vms, source_connector_id = validate_plan_vm_selection(payload.vm_ids, db)
    if source_connector_id == payload.target_connector_id:
        raise HTTPException(400, "Source and target connectors must be different")
    source = db.get(models.ConnectorProfile, source_connector_id)
    target = db.get(models.ConnectorProfile, payload.target_connector_id)
    if not source or not target:
        raise HTTPException(404, "Source or target connector not found")
    plan = models.MigrationPlan(
        name=name,
        source_connector_id=source.id,
        target_connector_id=target.id,
        migration_type=f"{source.connector_type} to {target.connector_type}",
        vm_ids_json=json.dumps(sorted(set(payload.vm_ids))),
        notes=payload.notes,
        keep_source_vm=payload.keep_source_vm,
        execution_options_json=json.dumps(compact_execution_options(payload.execution_options)),
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


@app.put("/api/migration-plans/{plan_id}", response_model=schemas.MigrationPlan)
def update_migration_plan(plan_id: int, payload: schemas.MigrationPlanUpdate, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    plan = db.get(models.MigrationPlan, plan_id)
    if not plan:
        raise HTTPException(404, "Migration plan not found")
    if plan.status in {"Queued", "Running"}:
        raise HTTPException(409, "Cannot edit a migration plan while its Spark Engine job is active")
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Migration plan name is required")
    if db.query(models.MigrationPlan).filter(models.MigrationPlan.name == name, models.MigrationPlan.id != plan.id).first():
        raise HTTPException(409, "Migration plan name already exists")
    vms, source_connector_id = validate_plan_vm_selection(payload.vm_ids, db)
    source = db.get(models.ConnectorProfile, source_connector_id)
    target = db.get(models.ConnectorProfile, payload.target_connector_id)
    if not source or not target:
        raise HTTPException(404, "Source or target connector not found")
    if source.id == target.id:
        raise HTTPException(400, "Target connector must differ from source connector")
    plan.name = name
    plan.source_connector_id = source_connector_id
    plan.target_connector_id = payload.target_connector_id
    plan.vm_ids_json = json.dumps(sorted({vm.id for vm in vms}))
    plan.notes = payload.notes
    plan.keep_source_vm = payload.keep_source_vm
    plan.execution_options_json = json.dumps(compact_execution_options(payload.execution_options))
    plan.migration_type = f"{source.connector_type} to {target.connector_type}"
    plan.status = "Draft"
    plan.spark_job_id = None
    plan.results_json = "[]"
    plan.executed_at = None
    db.commit()
    db.refresh(plan)
    return plan


@app.get("/api/spark/capabilities")
def get_spark_capabilities(_user: models.LocalUser = Depends(current_user)):
    try:
        return spark_capabilities()
    except Exception as exc:
        raise HTTPException(503, f"Spark Engine unavailable: {exc}") from exc


def connector_execution_payload(connector: models.ConnectorProfile) -> dict:
    return {
        "id": connector.id,
        "name": connector.name,
        "connector_category": connector.connector_category,
        "connector_type": connector.connector_type,
        "endpoint": connector.endpoint,
        "port": connector.port,
        "username": connector.username,
        "target_network": connector.target_network,
        "target_datastore": connector.target_datastore,
        "target_storage_pool": connector.target_storage_pool,
        "target_vdc_name": connector.target_vdc_name,
        "target_compute_name": connector.target_compute_name,
        "credential_reference": connector.credential_reference,
        "credential_payload": decrypt_connector_secret(connector),
    }


def migration_plan_execution_payload(
    plan: models.MigrationPlan,
    source: models.ConnectorProfile,
    target: models.ConnectorProfile,
    vms: list[models.VmInventory],
    requested_by: str,
    *,
    live: bool,
    extra_options: dict | None = None,
) -> dict:
    options = {
        **resolve_connector_defaults(target),
        **compact_execution_options(parsed_json_object(plan.execution_options_json)),
        **compact_execution_options(extra_options or {}),
    }
    return {
        "plan_id": plan.id,
        "plan_name": plan.name,
        "source_connector": connector_execution_payload(source),
        "target_connector": connector_execution_payload(target),
        "workloads": [
            {
                "id": vm.id,
                "vm_name": vm.vm_name,
                "external_id": vm.external_id,
                "host_name": vm.host_name,
                "details": json.loads(vm.details_json or "{}"),
            }
            for vm in vms
        ],
        "options": options,
        "keep_source_vm": plan.keep_source_vm,
        "requested_by": requested_by,
        "live": live,
        "approval": f"EXECUTE:{plan.id}" if live else f"PREFLIGHT:{plan.id}",
    }


def apply_spark_job(db: Session, plan: models.MigrationPlan, job: dict) -> None:
    spark_status = job.get("status")
    if spark_status in {"Queued", "Running"}:
        plan.status = spark_status
        return
    results = job.get("result") or []
    previous_status = plan.status
    previous_results_json = plan.results_json or ""
    plan.results_json = json.dumps(results)
    completed_at = datetime.utcnow()
    plan.executed_at = completed_at
    plan.status = spark_status if spark_status in {"Succeeded", "Failed", "Canceled"} else ("Completed" if spark_status == "Succeeded" else "Failed")
    if spark_status == "Canceled":
        return
    vm_ids = {row.get("vm_id") for row in results if row.get("vm_id")}
    vms = db.query(models.VmInventory).filter(models.VmInventory.id.in_(vm_ids)).all() if vm_ids else []
    result_by_vm = {row.get("vm_id"): row for row in results}
    is_new_terminal_snapshot = previous_status != plan.status or previous_results_json != plan.results_json
    for vm in vms:
        result = result_by_vm.get(vm.id, {})
        vm.current_status = "Cutover completed" if result.get("ok") else "Failed"
        if is_new_terminal_snapshot:
            note = (
                f"Migration completed at {completed_at.isoformat()}Z via Spark Engine job {job.get('id')}: "
                f"{result.get('message') or job.get('message')}"
                if result.get("ok")
                else f"Migration failed at {completed_at.isoformat()}Z via Spark Engine job {job.get('id')}: "
                f"{result.get('message') or job.get('message')}"
            )
            db.add(models.VmStatusHistory(vm_id=vm.id, status=vm.current_status, note=note))
            if result.get("ok"):
                append_migration_log(plan, vm, result, when=completed_at)
    if is_new_terminal_snapshot and results and all(isinstance(row, dict) and row.get("ok") for row in results):
        shutil.rmtree(plan_stage_directory(plan), ignore_errors=True)


@app.post("/api/migration-plans/{plan_id}/launch")
def launch_migration_plan(
    plan_id: int,
    payload: schemas.MigrationLaunch,
    db: Session = Depends(get_db),
    admin: models.LocalUser = Depends(admin_user),
):
    plan = db.get(models.MigrationPlan, plan_id)
    if not plan:
        raise HTTPException(404, "Migration plan not found")
    if payload.confirmation != plan.name:
        raise HTTPException(400, "Type the exact migration plan name to approve live execution")
    job = queue_live_migration_plan(plan, db, admin)
    db.commit()
    db.refresh(plan)
    return {"plan": schemas.MigrationPlan.model_validate(plan), "job": job}


@app.post("/api/migration-plans/{plan_id}/continue")
def continue_migration_plan(
    plan_id: int,
    payload: schemas.MigrationContinue,
    db: Session = Depends(get_db),
    admin: models.LocalUser = Depends(admin_user),
):
    plan = db.get(models.MigrationPlan, plan_id)
    if not plan:
        raise HTTPException(404, "Migration plan not found")
    if payload.confirmation != plan.name:
        raise HTTPException(400, "Type the exact migration plan name to continue live execution")
    _, _, vms = validate_live_migration_plan(plan, db)
    resume = plan_resume_metadata(plan, vms)
    if not resume.get("allowed"):
        raise HTTPException(409, resume.get("reason") or "This migration plan cannot continue from staging")
    job = queue_live_migration_plan_with_options(plan, db, admin, extra_options={"resume_from_stage": True})
    db.commit()
    db.refresh(plan)
    return {"plan": schemas.MigrationPlan.model_validate(plan), "job": job, "resume": resume}


@app.post("/api/migration-plans/{plan_id}/force-stop")
def force_stop_migration_plan(
    plan_id: int,
    payload: schemas.MigrationForceStop,
    db: Session = Depends(get_db),
    admin: models.LocalUser = Depends(admin_user),
):
    plan = db.get(models.MigrationPlan, plan_id)
    if not plan:
        raise HTTPException(404, "Migration plan not found")
    if payload.confirmation != plan.name:
        raise HTTPException(400, "Type the exact migration plan name to force-stop execution")
    if not plan.spark_job_id:
        raise HTTPException(404, "Migration plan has no Spark Engine execution")
    try:
        job = cancel_spark_job(plan.spark_job_id)
    except ValueError as exc:
        raise HTTPException(409, f"Spark Engine rejected force-stop: {exc}") from exc
    except Exception as exc:
        raise HTTPException(503, f"Spark Engine unavailable: {exc}") from exc
    apply_spark_job(db, plan, job)
    db.commit()
    db.refresh(plan)
    return {"plan": schemas.MigrationPlan.model_validate(plan), "job": job, "stopped_by": admin.username}


@app.get("/api/migration-plans/{plan_id}/execution")
def migration_plan_execution(plan_id: int, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    plan = db.get(models.MigrationPlan, plan_id)
    if not plan:
        raise HTTPException(404, "Migration plan not found")
    if not plan.spark_job_id:
        raise HTTPException(404, "Migration plan has no Spark Engine execution")
    try:
        job = get_spark_job(plan.spark_job_id)
    except Exception as exc:
        raise HTTPException(503, f"Spark Engine unavailable: {exc}") from exc
    apply_spark_job(db, plan, job)
    db.commit()
    db.refresh(plan)
    return {"plan": schemas.MigrationPlan.model_validate(plan), "job": job}


@app.delete("/api/migration-plans/{plan_id}", status_code=204)
def delete_migration_plan(plan_id: int, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    plan = db.get(models.MigrationPlan, plan_id)
    if not plan:
        raise HTTPException(404, "Migration plan not found")
    if plan.status in {"Queued", "Running"}:
        raise HTTPException(409, "Cannot delete a migration plan while its Spark Engine job is active")
    db.delete(plan)
    db.commit()


@app.post("/api/migration-plans/{plan_id}/execute", response_model=schemas.MigrationPlan)
def execute_migration_plan(plan_id: int, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    plan = db.get(models.MigrationPlan, plan_id)
    if not plan:
        raise HTTPException(404, "Migration plan not found")
    source = db.get(models.ConnectorProfile, plan.source_connector_id)
    target = db.get(models.ConnectorProfile, plan.target_connector_id)
    vm_ids = json.loads(plan.vm_ids_json)
    vms = db.query(models.VmInventory).filter(models.VmInventory.id.in_(vm_ids)).all()
    if not source or not target:
        raise HTTPException(404, "Source or target connector not found")
    if len(vms) != len(vm_ids):
        raise HTTPException(409, "One or more migration plan VMs no longer exist")
    try:
        preflight = preflight_spark_job(
            migration_plan_execution_payload(
                plan,
                source,
                target,
                vms,
                _user.username if _user else "system",
                live=False,
            )
        )
    except ValueError as exc:
        raise HTTPException(409, f"Spark Engine rejected preflight: {exc}") from exc
    except Exception as exc:
        raise HTTPException(503, f"Spark Engine unavailable: {exc}") from exc
    checks_by_vm = {}
    shared_checks = []
    for check in preflight.get("checks", []):
        if check.get("vm_name"):
            checks_by_vm.setdefault(check["vm_name"], []).append(check)
        else:
            shared_checks.append(check)
    results = []
    for vm in vms:
        checks = shared_checks + checks_by_vm.get(vm.vm_name, [])
        ok = bool(checks) and all(check.get("ok") for check in checks)
        vm.current_status = "Ready for migration" if ok else "Blocked"
        db.add(models.VmStatusHistory(vm_id=vm.id, status=vm.current_status, note=f"Migration plan {plan.name} Spark preflight"))
        results.append(
            {
                "vm_id": vm.id,
                "vm_name": vm.vm_name,
                "ok": ok,
                "message": summarize_preflight_checks(checks),
                "checks": checks,
                "adapter": preflight.get("adapter"),
            }
        )
    plan.status = "Preflight ready" if preflight.get("ok") else "Blocked"
    plan.results_json = json.dumps(results)
    plan.executed_at = datetime.utcnow()
    db.commit()
    db.refresh(plan)
    return plan


@app.post("/api/migration-jobs", response_model=schemas.MigrationJob, status_code=201)
def create_migration_job(payload: schemas.MigrationJobCreate, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    source = db.get(models.ConnectorProfile, payload.source_connector_id)
    target = db.get(models.ConnectorProfile, payload.target_connector_id)
    if not source or not target:
        raise HTTPException(404, "Source or target connector not found")
    if source.connector_type != "KVM" or target.connector_type not in {"VMware ESXi / vCenter", "VMware ESXi", "vCenter"}:
        raise HTTPException(400, "Only KVM to ESXi/vCenter migration preflight is implemented in this engine")
    result = build_kvm_to_esxi_preflight(
        source.endpoint,
        source.username,
        source.credential_reference,
        decrypt_connector_secret(source).get("password"),
        target.endpoint,
        target.username,
        target.credential_reference,
        decrypt_connector_secret(target).get("password"),
        payload.vm_name,
        target.target_datastore,
    )
    job = models.MigrationJob(
        source_connector_id=source.id,
        target_connector_id=target.id,
        vm_name=payload.vm_name,
        target_name=payload.target_name,
        migration_type=payload.migration_type,
        status="Preflight ready" if result.ok else "Blocked",
        message=result.message,
        runbook_json=json.dumps(result.records),
        commands_json=json.dumps(result.commands),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job
