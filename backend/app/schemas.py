from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


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


class ConnectorBase(BaseModel):
    name: str
    connector_category: str
    connector_type: str
    endpoint: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    credential_reference: Optional[str] = None
    environment: Optional[str] = None
    status: str = "Not validated"
    notes: Optional[str] = None


class ConnectorCreate(ConnectorBase):
    pass


class Connector(ConnectorBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime


class ConnectorValidationResult(BaseModel):
    connector: Connector
    status: str
    message: str
    commands: list[str] = []


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
    project_id: Optional[int] = None
    connector_id: Optional[int] = None
    external_id: Optional[str] = None
    host_name: Optional[str] = None
    wave_id: Optional[int] = None
    vm_name: str
    source_platform: str
    target_platform: str
    cpu: int
    memory_gb: int
    disk_gb: int
    os_type: Optional[str] = None
    ip_address: Optional[str] = None
    details_json: str = "{}"
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
    total_plans: int
    vms_discovered: int
    vms_planned: int
    vms_migrated: int
    vms_failed_or_blocked: int
    progress_percent: int
    by_status: dict[str, int]


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    username: str
    role: str
    is_active: bool = True
    profile_photo: Optional[str] = None


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "operator"
    is_active: bool = True
    profile_photo: Optional[str] = None


class UserUpdate(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    profile_photo: Optional[str] = None


class SettingsBase(BaseModel):
    product_name: str = "DS Shift"
    company_name: str = "Defined Solutions"
    default_timezone: str = "Asia/Riyadh"
    retention_days: int = 365
    maintenance_window: Optional[str] = None
    banner_message: Optional[str] = None


class AppSettings(SettingsBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime


class DiscoveryRequest(BaseModel):
    import_to_project_id: Optional[int] = None
    target_platform: Optional[str] = None


class DiscoveryRun(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    connector_id: int
    status: str
    message: Optional[str] = None
    records_json: Optional[str] = None
    commands_json: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class HostInventory(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    connector_id: int
    host_key: str
    host_name: str
    platform: str
    endpoint: Optional[str] = None
    environment: Optional[str] = None
    status: str
    cpu: int
    memory_gb: int
    vm_count: int
    vms_json: str
    details_json: str
    last_discovered_at: datetime
    created_at: datetime
    updated_at: datetime


class MigrationJobCreate(BaseModel):
    source_connector_id: int
    target_connector_id: int
    vm_name: str
    target_name: Optional[str] = None
    target_datastore: Optional[str] = None
    migration_type: str = "Connector migration"


class MigrationJob(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source_connector_id: int
    target_connector_id: int
    vm_name: str
    target_name: Optional[str] = None
    migration_type: str
    status: str
    message: Optional[str] = None
    runbook_json: Optional[str] = None
    commands_json: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class MigrationPlanCreate(BaseModel):
    name: str
    vm_ids: list[int]
    target_connector_id: int
    target_datastore: Optional[str] = None
    notes: Optional[str] = None
    execution_options: dict = Field(default_factory=dict)


class MigrationPlanUpdate(BaseModel):
    name: str
    target_connector_id: int
    target_datastore: Optional[str] = None
    notes: Optional[str] = None
    execution_options: dict = Field(default_factory=dict)


class MigrationLaunch(BaseModel):
    confirmation: str


class MigrationPlan(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    source_connector_id: int
    target_connector_id: int
    migration_type: str
    vm_ids_json: str
    target_datastore: Optional[str] = None
    status: str
    notes: Optional[str] = None
    execution_options_json: str
    spark_job_id: Optional[int] = None
    results_json: str
    executed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
