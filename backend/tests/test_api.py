import base64
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models, schemas
from app.connector_client import normalize_connector_type, validate_connector_platform
from app.database import Base
from app.main import (
    app,
    create_migration_plan,
    delete_connector,
    delete_user,
    execute_migration_plan,
    hash_password,
    seed_defaults,
    sync_discovered_hosts,
    sync_discovered_vms,
    update_user,
    validate_profile_photo,
)
from app.service_status import display_name, unavailable_statuses


def test_about():
    client = TestClient(app)
    response = client.get("/api/about")
    assert response.status_code == 200
    assert response.json()["product"] == "DS Shift"


def test_profile_photo_validation():
    png = b"\x89PNG\r\n\x1a\n" + b"profile-photo"
    encoded = base64.b64encode(png).decode()
    photo = f"data:image/png;base64,{encoded}"

    assert validate_profile_photo(photo) == photo

    with pytest.raises(HTTPException, match="does not match"):
        validate_profile_photo(f"data:image/png;base64,{base64.b64encode(b'not-an-image').decode()}")


def test_seed_defaults_rebrands_existing_settings():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        db.add(models.AppSetting(product_name="DS Replace"))
        db.commit()

        seed_defaults(db)

        assert db.query(models.AppSetting).one().product_name == "DS Shift"


def test_connector_platform_validation_and_aliases():
    assert normalize_connector_type("AWS") == "Amazon Web Services"
    assert validate_connector_platform("cloud", "Azure") == "Microsoft Azure"
    assert validate_connector_platform("host", "Nutanix AHV") == "Nutanix AHV"

    with pytest.raises(ValueError, match="Unsupported cloud connector type"):
        validate_connector_platform("cloud", "Other Cloud")


def test_admin_cannot_delete_or_deactivate_self():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        admin = models.LocalUser(
            username="admin",
            password_hash=hash_password("password"),
            role="admin",
            is_active="true",
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)

        with pytest.raises(HTTPException, match="delete your own"):
            delete_user(admin.id, db, admin)

        with pytest.raises(HTTPException, match="demote or deactivate"):
            update_user(admin.id, schemas.UserUpdate(is_active=False), db, admin)


def test_service_status_fallback():
    result = unavailable_statuses("monitor unavailable")

    assert display_name("cloud-connector-engine") == "Cloud-Connector-Engine"
    assert [row["service"] for row in result["services"]] == [
        "backend",
        "cloud-connector-engine",
        "database",
        "frontend",
        "host-connector-engine",
        "reverse-proxy",
    ]
    assert all(row["status"] == "DOWN" for row in result["services"])


def test_host_discovery_sync_and_connector_delete():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        connector = models.ConnectorProfile(
            name="KVM Lab",
            connector_category="host",
            connector_type="KVM",
            endpoint="qemu+ssh://root@kvm/system",
        )
        db.add(connector)
        db.commit()
        db.refresh(connector)

        count = sync_discovered_hosts(
            db,
            connector,
            [{"host_key": "kvm", "host_name": "kvm", "platform": "KVM", "cpu": 16, "memory_gb": 64}],
            [{"vm_name": "vm-01", "host_key": "kvm", "host_name": "kvm", "cpu": 2}],
        )

        host = db.query(models.HostInventory).one()
        assert count == 1
        assert host.host_name == "kvm"
        assert host.vm_count == 1
        assert json.loads(host.vms_json)[0]["vm_name"] == "vm-01"

        delete_connector(connector.id, db, None)
        assert db.query(models.ConnectorProfile).count() == 0
        assert db.query(models.HostInventory).count() == 0


def test_discovery_inventory_and_migration_plan_execution(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        source = models.ConnectorProfile(name="KVM Source", connector_category="host", connector_type="KVM")
        target = models.ConnectorProfile(name="vCenter Target", connector_category="host", connector_type="VMware ESXi / vCenter")
        db.add_all([source, target])
        db.commit()
        db.refresh(source)
        db.refresh(target)

        assert sync_discovered_vms(
            db,
            source,
            [{"vm_name": "vm-01", "external_id": "vm-101", "host_name": "kvm01", "source_platform": "KVM", "cpu": 4, "memory_gb": 8, "os_type": "Linux"}],
        ) == 1
        vm = db.query(models.VmInventory).one()
        assert vm.project_id is None
        assert vm.connector_id == source.id
        assert vm.external_id == "vm-101"
        assert vm.host_name == "kvm01"

        duplicate_name_records = [
            {"vm_name": "vm-01", "external_id": "vm-101", "host_name": "kvm01", "source_platform": "KVM"},
            {"vm_name": "vm-01", "external_id": "vm-102", "host_name": "kvm01", "source_platform": "KVM"},
        ]
        assert sync_discovered_vms(db, source, duplicate_name_records) == 2
        assert db.query(models.VmInventory).count() == 2
        assert sync_discovered_vms(db, source, duplicate_name_records) == 2
        assert db.query(models.VmInventory).count() == 2

        plan = create_migration_plan(
            schemas.MigrationPlanCreate(name="Plan 1", vm_ids=[vm.id], target_connector_id=target.id),
            db,
            None,
        )
        assert json.loads(plan.vm_ids_json) == [vm.id]
        assert plan.status == "Draft"

        monkeypatch.setattr(
            "app.main.build_kvm_to_esxi_preflight",
            lambda *args: type("Result", (), {"ok": True, "message": "Preflight passed", "records": [], "commands": []})(),
        )
        executed = execute_migration_plan(plan.id, db, None)

        assert executed.status == "Preflight ready"
        assert executed.executed_at is not None
        assert db.get(models.VmInventory, vm.id).current_status == "Ready for migration"
