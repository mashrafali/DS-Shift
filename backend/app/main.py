from collections import Counter

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import models, schemas
from .config import settings
from .database import Base, engine, get_db

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


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)


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


@app.get("/api/dashboard", response_model=schemas.DashboardSummary)
def dashboard(db: Session = Depends(get_db)):
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
def list_projects(db: Session = Depends(get_db)):
    return db.query(models.MigrationProject).order_by(models.MigrationProject.created_at.desc()).all()


@app.post("/api/projects", response_model=schemas.Project, status_code=201)
def create_project(payload: schemas.ProjectCreate, db: Session = Depends(get_db)):
    project = models.MigrationProject(**payload.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@app.get("/api/projects/{project_id}", response_model=schemas.Project)
def get_project(project_id: int, db: Session = Depends(get_db)):
    project = db.get(models.MigrationProject, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


@app.put("/api/projects/{project_id}", response_model=schemas.Project)
def update_project(project_id: int, payload: schemas.ProjectCreate, db: Session = Depends(get_db)):
    project = db.get(models.MigrationProject, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    for key, value in payload.model_dump().items():
        setattr(project, key, value)
    db.commit()
    db.refresh(project)
    return project


@app.delete("/api/projects/{project_id}", status_code=204)
def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = db.get(models.MigrationProject, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    db.delete(project)
    db.commit()
    return Response(status_code=204)


@app.get("/api/platforms", response_model=list[schemas.Platform])
def list_platforms(db: Session = Depends(get_db)):
    return db.query(models.PlatformProfile).order_by(models.PlatformProfile.name).all()


@app.post("/api/platforms", response_model=schemas.Platform, status_code=201)
def create_platform(payload: schemas.PlatformCreate, db: Session = Depends(get_db)):
    platform = models.PlatformProfile(**payload.model_dump())
    db.add(platform)
    db.commit()
    db.refresh(platform)
    return platform


@app.get("/api/waves", response_model=list[schemas.Wave])
def list_waves(db: Session = Depends(get_db)):
    return db.query(models.MigrationWave).order_by(models.MigrationWave.created_at.desc()).all()


@app.post("/api/waves", response_model=schemas.Wave, status_code=201)
def create_wave(payload: schemas.WaveCreate, db: Session = Depends(get_db)):
    wave = models.MigrationWave(**payload.model_dump())
    db.add(wave)
    db.commit()
    db.refresh(wave)
    return wave


@app.get("/api/vms", response_model=list[schemas.Vm])
def list_vms(db: Session = Depends(get_db)):
    return db.query(models.VmInventory).order_by(models.VmInventory.created_at.desc()).all()


@app.post("/api/vms", response_model=schemas.Vm, status_code=201)
def create_vm(payload: schemas.VmCreate, db: Session = Depends(get_db)):
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
def update_vm_status(vm_id: int, payload: schemas.StatusUpdate, db: Session = Depends(get_db)):
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
def vm_history(vm_id: int, db: Session = Depends(get_db)):
    return (
        db.query(models.VmStatusHistory)
        .filter(models.VmStatusHistory.vm_id == vm_id)
        .order_by(models.VmStatusHistory.changed_at.desc())
        .all()
    )


@app.get("/api/reports/readiness")
def readiness_report(db: Session = Depends(get_db)):
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
