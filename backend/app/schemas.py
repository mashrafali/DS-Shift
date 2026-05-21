from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ProjectBase(BaseModel):
    project_name: str
    customer_name: str
    source_platform: str
    target_platform: str
    migration_type: str
    planned_start_date: Optional[str] = None
    planned_cutover_date: Optional[str] = None
    status: str = "Planning"
    notes: Optional[str] = None


class ProjectCreate(ProjectBase):
    pass


class Project(ProjectBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime


class PlatformBase(BaseModel):
    name: str
    platform_type: str
    endpoint: Optional[str] = None
    environment: Optional[str] = None
    credential_reference: Optional[str] = None
    notes: Optional[str] = None


class PlatformCreate(PlatformBase):
    pass


class Platform(PlatformBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime


class WaveBase(BaseModel):
    project_id: int
    wave_name: str
    planned_window: Optional[str] = None
    status: str = "Planned"
    notes: Optional[str] = None


class WaveCreate(WaveBase):
    pass


class Wave(WaveBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime


class VmBase(BaseModel):
    project_id: int
    wave_id: Optional[int] = None
    vm_name: str
    source_platform: str
    target_platform: str
    cpu: int
    memory_gb: int
    disk_gb: int
    os_type: Optional[str] = None
    ip_address: Optional[str] = None
    application_owner: Optional[str] = None
    criticality: str = "Medium"
    migration_wave: Optional[str] = None
    current_status: str = "Discovered"


class VmCreate(VmBase):
    pass


class Vm(VmBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime


class StatusUpdate(BaseModel):
    status: str
    note: Optional[str] = None


class StatusHistory(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    vm_id: int
    status: str
    note: Optional[str] = None
    changed_at: datetime


class DashboardSummary(BaseModel):
    total_projects: int
    vms_discovered: int
    vms_planned: int
    vms_migrated: int
    vms_failed_or_blocked: int
    progress_percent: int
    by_status: dict[str, int]
