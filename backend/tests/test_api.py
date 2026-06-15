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
    create_connector,
    create_wave,
    delete_connector,
    delete_user,
    decrypt_connector_secret,
    connector_public,
    execute_migration_plan,
    hash_password,
    launch_migration_plan,
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


def test_connector_secret_storage_and_public_shape():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        payload = schemas.ConnectorCreate(
            name="vCenter Lab",
            connector_category="host",
            connector_type="VMware ESXi / vCenter",
            endpoint="https://vcsa.test.local/sdk",
            username="administrator@vsphere.local",
            password="P@ssw0rd",
        )
        created = create_connector(payload, db, None)
        stored = db.query(models.ConnectorProfile).one()

        assert created.has_stored_secret is True
        assert created.credential_reference is None
        assert created.username == "administrator@vsphere.local"
        assert decrypt_connector_secret(stored)["password"] == "P@ssw0rd"
        assert connector_public(stored).has_stored_secret is True


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

    assert display_name("cloud-connector") == "Cloud-Connector"
    assert [row["service"] for row in result["services"]] == [
        "backend",
        "cloud-connector",
        "database",
        "frontend",
        "host-connector",
        "reverse-proxy",
        "spark-engine",
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
        assert sync_discovered_vms(
            db,
            connector,
            [{"vm_name": "vm-01", "external_id": "vm-01", "host_name": "kvm", "source_platform": "KVM", "cpu": 2, "memory_gb": 4}],
        ) == 1

        host = db.query(models.HostInventory).one()
        assert count == 1
        assert host.host_name == "kvm"
        assert host.vm_count == 1
        assert json.loads(host.vms_json)[0]["vm_name"] == "vm-01"
        assert db.query(models.VmInventory).count() == 1

        delete_connector(connector.id, db, None)
        assert db.query(models.ConnectorProfile).count() == 0
        assert db.query(models.HostInventory).count() == 0
        assert db.query(models.VmInventory).count() == 0


def test_connector_delete_removes_legacy_migration_jobs_but_blocks_plans():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        source = models.ConnectorProfile(name="KVM Source", connector_category="host", connector_type="KVM", endpoint="qemu+ssh://root@kvm/system")
        target = models.ConnectorProfile(name="vCenter Target", connector_category="host", connector_type="VMware ESXi / vCenter", endpoint="https://vcsa.test.local/sdk")
        db.add_all([source, target])
        db.commit()
        db.refresh(source)
        db.refresh(target)

        db.add(
            models.MigrationJob(
                source_connector_id=source.id,
                target_connector_id=target.id,
                vm_name="legacy-preflight-vm",
                status="Blocked",
            )
        )
        db.commit()

        delete_connector(source.id, db, None)
        assert db.query(models.ConnectorProfile).filter(models.ConnectorProfile.id == source.id).count() == 0
        assert db.query(models.MigrationJob).count() == 0

    with Session(engine) as db:
        source = models.ConnectorProfile(name="KVM Source 2", connector_category="host", connector_type="KVM", endpoint="qemu+ssh://root@kvm2/system")
        target = models.ConnectorProfile(name="vCenter Target 2", connector_category="host", connector_type="VMware ESXi / vCenter", endpoint="https://vcsa2.test.local/sdk")
        db.add_all([source, target])
        db.flush()
        vm = models.VmInventory(vm_name="vm-01", source_platform="KVM", target_platform="VMware ESXi / vCenter", cpu=2, memory_gb=4, disk_gb=50, connector_id=source.id)
        db.add(vm)
        db.flush()
        db.add(
            models.MigrationPlan(
                name="Protected Plan",
                source_connector_id=source.id,
                target_connector_id=target.id,
                migration_type="KVM to VMware ESXi / vCenter",
                vm_ids_json=json.dumps([vm.id]),
            )
        )
        db.commit()

        with pytest.raises(HTTPException, match="migration plan"):
            delete_connector(source.id, db, None)


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
        assert json.loads(vm.details_json)["external_id"] == "vm-101"

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
            "app.main.create_spark_job",
            lambda payload: {"id": 42, "plan_id": payload["plan_id"], "status": "Queued", "adapter": "test"},
        )
        launched = launch_migration_plan(
            plan.id,
            schemas.MigrationLaunch(confirmation=plan.name),
            db,
            models.LocalUser(username="admin", password_hash="unused", role="admin", is_active="true"),
        )
        assert launched["job"]["id"] == 42
        assert db.get(models.MigrationPlan, plan.id).spark_job_id == 42
        plan.status = "Draft"
        db.commit()

        monkeypatch.setattr(
            "app.main.preflight_spark_job",
            lambda payload: {
                "ok": True,
                "adapter": "kvm-vcenter-ova",
                "checks": [
                    {"check": "adapter", "ok": True, "message": "Migration adapter is ready"},
                    {"check": "source_vm_state", "vm_name": "vm-01", "ok": True, "message": "shut off"},
                ],
            },
        )
        executed = execute_migration_plan(plan.id, db, None)

        assert executed.status == "Preflight ready"
        assert executed.executed_at is not None
        assert db.get(models.VmInventory, vm.id).current_status == "Ready for migration"


def test_connector_defaults_and_wave_creation():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        source = models.ConnectorProfile(name="KVM Source", connector_category="host", connector_type="KVM", endpoint="qemu+ssh://root@kvm/system")
        target = models.ConnectorProfile(
            name="vCenter Target",
            connector_category="host",
            connector_type="VMware ESXi / vCenter",
            endpoint="https://vcsa.test.local/sdk",
            target_network="VM Network",
            target_datastore="Datastore01",
            target_vdc_name="TESTING-DC",
        )
        db.add_all([source, target])
        db.flush()
        vm1 = models.VmInventory(vm_name="vm-01", source_platform="KVM", target_platform="VMware ESXi / vCenter", cpu=2, memory_gb=4, disk_gb=50, connector_id=source.id)
        vm2 = models.VmInventory(vm_name="vm-02", source_platform="KVM", target_platform="VMware ESXi / vCenter", cpu=2, memory_gb=4, disk_gb=50, connector_id=source.id)
        db.add_all([vm1, vm2])
        db.commit()
        db.refresh(vm1)
        db.refresh(vm2)

        plan = create_migration_plan(
            schemas.MigrationPlanCreate(name="Wave Plan", vm_ids=[vm1.id, vm2.id], target_connector_id=target.id),
            db,
            None,
        )
        payload = create_wave(
            schemas.WaveCreate(wave_name="Wave 1", plan_ids=[plan.id]),
            db,
            None,
        )

        assert json.loads(payload.plan_ids_json) == [plan.id]
        assert {db.get(models.VmInventory, vm1.id).migration_wave, db.get(models.VmInventory, vm2.id).migration_wave} == {"Wave 1"}
