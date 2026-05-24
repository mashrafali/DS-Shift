from collections import Counter
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import os
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import models, schemas
from .config import settings
from .database import Base, engine, get_db
from .engines import build_kvm_to_esxi_preflight, discover_kvm, discover_vcenter, validate_kvm, validate_vcenter

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


def seed_defaults(db: Session) -> None:
    admin_username = os.getenv("ADMIN_INITIAL_USERNAME", "admin")
    admin_password = os.getenv("ADMIN_INITIAL_PASSWORD", "P@ssw0rd")
    if not db.query(models.LocalUser).filter(models.LocalUser.username == admin_username).first():
        db.add(models.LocalUser(username=admin_username, password_hash=hash_password(admin_password), role="admin"))
    if not db.query(models.AppSetting).first():
        db.add(models.AppSetting())
    db.commit()


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
    Base.metadata.create_all(bind=engine)
    db = next(get_db())
    try:
        seed_defaults(db)
    finally:
        db.close()


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("select 1"))
    return {"status": "ok", "application": settings.app_name, "version": settings.app_version}


@app.get("/api/about")
def about():
    return {
        "product": "DS Replace",
        "brand": "Defined Solutions",
        "version": settings.app_version,
        "purpose": "Any-to-any VM migration planning and tracking platform.",
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
    return schemas.UserPublic(id=user.id, username=user.username, role=user.role, is_active=user.is_active == "true")


@app.get("/api/users", response_model=list[schemas.UserPublic])
def list_users(db: Session = Depends(get_db), _admin: models.LocalUser = Depends(admin_user)):
    return [
        schemas.UserPublic(id=user.id, username=user.username, role=user.role, is_active=user.is_active == "true")
        for user in db.query(models.LocalUser).order_by(models.LocalUser.username).all()
    ]


@app.post("/api/users", response_model=schemas.UserPublic, status_code=201)
def create_user(payload: schemas.UserCreate, db: Session = Depends(get_db), _admin: models.LocalUser = Depends(admin_user)):
    if db.query(models.LocalUser).filter(models.LocalUser.username == payload.username).first():
        raise HTTPException(409, "Username already exists")
    user = models.LocalUser(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active="true" if payload.is_active else "false",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.put("/api/users/{user_id}", response_model=schemas.UserPublic)
def update_user(user_id: int, payload: schemas.UserUpdate, db: Session = Depends(get_db), _admin: models.LocalUser = Depends(admin_user)):
    user = db.get(models.LocalUser, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if payload.password:
        user.password_hash = hash_password(payload.password)
    user.role = payload.role
    user.is_active = "true" if payload.is_active else "false"
    db.commit()
    db.refresh(user)
    return user


@app.get("/api/dashboard", response_model=schemas.DashboardSummary)
def dashboard(db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    projects = db.query(models.MigrationProject).count()
    vms = db.query(models.VmInventory).all()
    status_counts = Counter(vm.current_status for vm in vms)
    migrated = status_counts["Validation completed"] + status_counts["Cutover completed"]
    failed = status_counts["Failed"] + status_counts["Rolled back"] + status_counts["Blocked"]
    planned = sum(status_counts[s] for s in ["Assessed", "Ready for migration", "Replication prepared", "Cutover scheduled"])
    progress = int((migrated / len(vms)) * 100) if vms else 0
    return schemas.DashboardSummary(
        total_projects=projects,
        vms_discovered=len(vms),
        vms_planned=planned,
        vms_migrated=migrated,
        vms_failed_or_blocked=failed,
        progress_percent=progress,
        by_status=dict(status_counts),
    )


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
    return query.order_by(models.ConnectorProfile.created_at.desc()).all()


@app.post("/api/connectors", response_model=schemas.Connector, status_code=201)
def create_connector(payload: schemas.ConnectorCreate, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    connector = models.ConnectorProfile(**payload.model_dump())
    db.add(connector)
    db.commit()
    db.refresh(connector)
    return connector


@app.put("/api/connectors/{connector_id}", response_model=schemas.Connector)
def update_connector(connector_id: int, payload: schemas.ConnectorCreate, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    connector = db.get(models.ConnectorProfile, connector_id)
    if not connector:
        raise HTTPException(404, "Connector not found")
    for key, value in payload.model_dump().items():
        setattr(connector, key, value)
    db.commit()
    db.refresh(connector)
    return connector


@app.post("/api/connectors/{connector_id}/validate", response_model=schemas.ConnectorValidationResult)
def validate_connector(connector_id: int, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    connector = db.get(models.ConnectorProfile, connector_id)
    if not connector:
        raise HTTPException(404, "Connector not found")
    if connector.connector_type == "KVM":
        result = validate_kvm(connector.endpoint, connector.username, connector.credential_reference)
    elif connector.connector_type in {"VMware ESXi / vCenter", "VMware ESXi", "vCenter"}:
        result = validate_vcenter(connector.endpoint, connector.username, connector.credential_reference)
    else:
        result = None
    if result is None:
        connector.status = "Unsupported"
        message = f"No validation engine for {connector.connector_type}"
        commands: list[str] = []
        status = "Unsupported"
    else:
        connector.status = "Validated" if result.ok else "Validation failed"
        message = result.message
        commands = result.commands
        status = connector.status
    db.commit()
    db.refresh(connector)
    return schemas.ConnectorValidationResult(connector=schemas.Connector.model_validate(connector), status=status, message=message, commands=commands)


@app.post("/api/connectors/{connector_id}/discover", response_model=schemas.DiscoveryRun)
def discover_connector(connector_id: int, payload: schemas.DiscoveryRequest, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
    connector = db.get(models.ConnectorProfile, connector_id)
    if not connector:
        raise HTTPException(404, "Connector not found")
    if connector.connector_type == "KVM":
        result = discover_kvm(connector.endpoint, connector.username, connector.credential_reference)
    elif connector.connector_type in {"VMware ESXi / vCenter", "VMware ESXi", "vCenter"}:
        result = discover_vcenter(connector.endpoint, connector.username, connector.credential_reference)
    else:
        result = None
    if result is None:
        run = models.DiscoveryRun(connector_id=connector.id, status="Unsupported", message=f"No discovery engine for {connector.connector_type}", records_json="[]", commands_json="[]")
    else:
        imported = 0
        if result.ok and payload.import_to_project_id:
            imported = import_discovered_vms(db, payload.import_to_project_id, payload.target_platform or "Unassigned", result.records)
        message = result.message + (f"; imported {imported} VMs" if imported else "")
        run = models.DiscoveryRun(
            connector_id=connector.id,
            status="Completed" if result.ok else "Failed",
            message=message,
            records_json=json.dumps(result.records),
            commands_json=json.dumps(result.commands),
        )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


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
            existing.current_status = "Discovered"
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
    wave = models.MigrationWave(**payload.model_dump())
    db.add(wave)
    db.commit()
    db.refresh(wave)
    return wave


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


@app.put("/api/settings", response_model=schemas.AppSettings)
def update_settings(payload: schemas.SettingsBase, db: Session = Depends(get_db), _user: models.LocalUser = Depends(current_user)):
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
        target.endpoint,
        target.username,
        target.credential_reference,
        payload.vm_name,
        payload.target_datastore,
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
