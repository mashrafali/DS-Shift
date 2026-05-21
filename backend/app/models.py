from datetime import datetime

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
