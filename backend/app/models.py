from datetime import datetime, timedelta

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MigrationProject(TimestampMixin, Base):
    __tablename__ = "migration_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    customer_name: Mapped[str] = mapped_column(String(160))
    source_platform: Mapped[str] = mapped_column(String(80))
    target_platform: Mapped[str] = mapped_column(String(80))
    migration_type: Mapped[str] = mapped_column(String(80))
    planned_start_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    planned_cutover_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(60), default="Planning")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    vms: Mapped[list["VmInventory"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    waves: Mapped[list["MigrationWave"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class PlatformProfile(TimestampMixin, Base):
    __tablename__ = "platform_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    platform_type: Mapped[str] = mapped_column(String(80))
    endpoint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    environment: Mapped[str | None] = mapped_column(String(80), nullable=True)
    credential_reference: Mapped[str | None] = mapped_column(String(180), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ConnectorProfile(TimestampMixin, Base):
    __tablename__ = "connector_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    connector_category: Mapped[str] = mapped_column(String(40), index=True)
    connector_type: Mapped[str] = mapped_column(String(80))
    endpoint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    username: Mapped[str | None] = mapped_column(String(160), nullable=True)
    credential_reference: Mapped[str | None] = mapped_column(String(180), nullable=True)
    environment: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(60), default="Not validated")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class MigrationWave(TimestampMixin, Base):
    __tablename__ = "migration_waves"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("migration_projects.id", ondelete="CASCADE"))
    wave_name: Mapped[str] = mapped_column(String(160), index=True)
    planned_window: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[str] = mapped_column(String(60), default="Planned")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped[MigrationProject] = relationship(back_populates="waves")
    vms: Mapped[list["VmInventory"]] = relationship(back_populates="wave")


class VmInventory(TimestampMixin, Base):
    __tablename__ = "vm_inventory"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("migration_projects.id", ondelete="CASCADE"))
    wave_id: Mapped[int | None] = mapped_column(ForeignKey("migration_waves.id"), nullable=True)
    vm_name: Mapped[str] = mapped_column(String(160), index=True)
    source_platform: Mapped[str] = mapped_column(String(80))
    target_platform: Mapped[str] = mapped_column(String(80))
    cpu: Mapped[int] = mapped_column(Integer)
    memory_gb: Mapped[int] = mapped_column(Integer)
    disk_gb: Mapped[int] = mapped_column(Integer)
    os_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    application_owner: Mapped[str | None] = mapped_column(String(160), nullable=True)
    criticality: Mapped[str] = mapped_column(String(40), default="Medium")
    migration_wave: Mapped[str | None] = mapped_column(String(120), nullable=True)
    current_status: Mapped[str] = mapped_column(String(80), default="Discovered")

    project: Mapped[MigrationProject] = relationship(back_populates="vms")
    wave: Mapped[MigrationWave | None] = relationship(back_populates="vms")
    history: Mapped[list["VmStatusHistory"]] = relationship(back_populates="vm", cascade="all, delete-orphan")


class VmStatusHistory(Base):
    __tablename__ = "vm_status_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    vm_id: Mapped[int] = mapped_column(ForeignKey("vm_inventory.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(80))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    vm: Mapped[VmInventory] = relationship(back_populates="history")


class LocalUser(TimestampMixin, Base):
    __tablename__ = "local_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(40), default="admin")
    is_active: Mapped[str] = mapped_column(String(8), default="true")
    profile_photo: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("local_users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow() + timedelta(hours=12))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AppSetting(TimestampMixin, Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_name: Mapped[str] = mapped_column(String(120), default="DS Replace")
    company_name: Mapped[str] = mapped_column(String(160), default="Defined Solutions")
    default_timezone: Mapped[str] = mapped_column(String(80), default="Asia/Riyadh")
    retention_days: Mapped[int] = mapped_column(Integer, default=365)
    maintenance_window: Mapped[str | None] = mapped_column(String(160), nullable=True)
    banner_message: Mapped[str | None] = mapped_column(String(255), nullable=True)


class DiscoveryRun(TimestampMixin, Base):
    __tablename__ = "discovery_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    connector_id: Mapped[int] = mapped_column(ForeignKey("connector_profiles.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(60), default="Pending")
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    records_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    commands_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class MigrationJob(TimestampMixin, Base):
    __tablename__ = "migration_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_connector_id: Mapped[int] = mapped_column(ForeignKey("connector_profiles.id"))
    target_connector_id: Mapped[int] = mapped_column(ForeignKey("connector_profiles.id"))
    vm_name: Mapped[str] = mapped_column(String(160), index=True)
    target_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    migration_type: Mapped[str] = mapped_column(String(80), default="KVM to ESXi")
    status: Mapped[str] = mapped_column(String(60), default="Preflight")
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    runbook_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    commands_json: Mapped[str | None] = mapped_column(Text, nullable=True)
