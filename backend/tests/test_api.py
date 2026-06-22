import base64
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models, schemas
from app.connector_client import normalize_connector_type, validate_connector_platform
from app.database import Base
from app.main import (
    apply_spark_job,
    app,
    migration_plan_execution,
    migration_plan_execution_payload,
    raw_dashboard_summary,
    create_migration_plan,
    create_connector,
    create_wave,
    delete_wave,
    delete_connector,
    delete_user,
    decrypt_connector_secret,
    connector_public,
    continue_migration_plan,
    execute_migration_plan,
    execute_wave,
    force_stop_migration_plan,
    hash_password,
    launch_migration_plan,
    seed_defaults,
    run_migration_plan_preflight,
    sync_discovered_hosts,
    sync_discovered_vms,
    update_user,
    update_wave,
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


def test_kvm_connector_public_shape_includes_target_pool():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        connector = models.ConnectorProfile(
            name="KVM Target",
            connector_category="host",
            connector_type="KVM",
            endpoint="qemu+ssh://root@kvm/system",
            target_storage_pool="default",
            target_network="br11",
        )
        db.add(connector)
        db.commit()
        db.refresh(connector)

        public = connector_public(connector)

        assert public.target_storage_pool == "default"
        assert public.target_network == "br11"


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
        "launchgrid",
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

        phase_checks = {
            "source_reachability": [{"phase": "source_reachability", "check": "source_vm_state", "vm_name": "vm-01", "ok": True, "message": "shut off"}],
            "destination_reachability": [{"phase": "destination_reachability", "check": "target_vcenter", "ok": True, "message": "vCenter reachable"}],
            "destination_connector_data": [{"phase": "destination_connector_data", "check": "target_datastore", "ok": True, "message": "ESX2-SSD"}],
            "blockers": [{"phase": "blockers", "check": "qemu_img", "ok": True, "message": "/usr/bin/qemu-img"}],
        }

        monkeypatch.setattr("app.main.preflight_spark_job", lambda payload, phase=None: {"ok": True, "adapter": "kvm-vcenter-ova", "checks": phase_checks[phase]})
        run_migration_plan_preflight(plan.id, "admin", db)

        refreshed_plan = db.get(models.MigrationPlan, plan.id)
        assert refreshed_plan.status == "Preflight ready"
        assert refreshed_plan.executed_at is not None
        assert "Source reachability" in refreshed_plan.results_json
        assert "Plan summary" in refreshed_plan.results_json
        assert db.get(models.VmInventory, vm.id).current_status == "Ready for migration"


def test_run_migration_plan_preflight_stops_on_failed_phase_and_marks_later_phases_skipped(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        source = models.ConnectorProfile(name="vCenter Source", connector_category="host", connector_type="VMware ESXi / vCenter")
        target = models.ConnectorProfile(name="KVM Target", connector_category="host", connector_type="KVM")
        db.add_all([source, target])
        db.flush()
        vm = models.VmInventory(
            connector_id=source.id,
            vm_name="vm-01",
            source_platform="VMware ESXi / vCenter",
            target_platform="KVM",
            cpu=2,
            memory_gb=4,
            disk_gb=40,
            current_status="Discovered",
        )
        db.add(vm)
        db.flush()
        plan = models.MigrationPlan(
            name="Blocked Plan",
            source_connector_id=source.id,
            target_connector_id=target.id,
            migration_type="VMware ESXi / vCenter to KVM",
            vm_ids_json=json.dumps([vm.id]),
            status="Draft",
        )
        db.add(plan)
        db.commit()

        calls = []
        phase_checks = {
            "source_reachability": [{"phase": "source_reachability", "check": "source_vm_state", "vm_name": "vm-01", "ok": True, "message": "poweredOff"}],
            "destination_reachability": [{"phase": "destination_reachability", "check": "target_kvm", "ok": False, "message": "ssh timeout"}],
        }

        def fake_preflight(_payload, phase=None):
            calls.append(phase)
            return {"ok": all(check["ok"] for check in phase_checks[phase]), "adapter": "vcenter-kvm-virt-v2v", "checks": phase_checks[phase]}

        monkeypatch.setattr("app.main.preflight_spark_job", fake_preflight)

        run_migration_plan_preflight(plan.id, "admin", db)

        refreshed_plan = db.get(models.MigrationPlan, plan.id)
        rows = json.loads(refreshed_plan.results_json)

        assert calls == ["source_reachability", "destination_reachability"]
        assert refreshed_plan.status == "Blocked"
        assert next(row for row in rows if row["key"] == "destination-reachability")["status"] == "Failed"
        assert next(row for row in rows if row["key"] == "destination-connector-data")["status"] == "Skipped"
        assert next(row for row in rows if row["key"] == "blockers")["status"] == "Skipped"


def test_migration_plan_execution_uses_stored_preflight_summary_without_spark_job():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        source = models.ConnectorProfile(name="vCenter Source", connector_category="host", connector_type="VMware ESXi / vCenter")
        target = models.ConnectorProfile(name="KVM Target", connector_category="host", connector_type="KVM")
        db.add_all([source, target])
        db.flush()
        plan = models.MigrationPlan(
            name="Plan preflight",
            source_connector_id=source.id,
            target_connector_id=target.id,
            migration_type="VMware ESXi / vCenter to KVM",
            vm_ids_json="[]",
            status="Blocked",
            results_json=json.dumps([
                {"kind": "task", "key": "source-reachability", "title": "Source reachability", "status": "Succeeded", "progress": 20, "message": "Source reachability passed"},
                {"kind": "task", "key": "plan-summary", "title": "Plan summary", "status": "Failed", "progress": 100, "message": "Preflight blocked: source vm state: poweredOn"},
            ]),
        )
        db.add(plan)
        db.commit()
        db.refresh(plan)

        execution = migration_plan_execution(plan.id, db, None)

        assert execution["job"]["message"] == "Preflight blocked: source vm state: poweredOn"


def test_sync_discovered_vms_updates_vm_name_when_external_id_matches():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        connector = models.ConnectorProfile(
            name="vCenter Source",
            connector_category="host",
            connector_type="VMware ESXi / vCenter",
            endpoint="https://vcsa.test.local/sdk",
        )
        db.add(connector)
        db.flush()
        vm = models.VmInventory(
            connector_id=connector.id,
            external_id="vm-500",
            host_name="esx01.test.local",
            vm_name="old-name",
            source_platform="VMware ESXi / vCenter",
            target_platform="Unassigned",
            cpu=4,
            memory_gb=8,
            disk_gb=120,
            os_type="Ubuntu Linux (64-bit)",
            ip_address="192.168.12.40",
            details_json=json.dumps({"path": "/Datacenter/vm/old-name"}),
            current_status="Discovered",
        )
        db.add(vm)
        db.commit()

        assert sync_discovered_vms(
            db,
            connector,
            [{
                "external_id": "vm-500",
                "vm_name": "new-name",
                "host_name": "esx01.test.local",
                "source_platform": "VMware ESXi / vCenter",
                "cpu": 4,
                "memory_gb": 8,
                "disk_gb": 120,
                "os_type": "Ubuntu Linux (64-bit)",
                "ip_address": "192.168.12.40",
                "path": "/Datacenter/vm/new-name",
            }],
        ) == 1

        db.refresh(vm)
        assert vm.vm_name == "new-name"
        assert vm.external_id == "vm-500"
        assert json.loads(vm.details_json)["path"] == "/Datacenter/vm/new-name"
        assert db.query(models.VmInventory).count() == 1


def test_sync_discovered_vms_adopts_legacy_vm_row_when_external_id_was_missing():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        connector = models.ConnectorProfile(
            name="vCenter Source",
            connector_category="host",
            connector_type="VMware ESXi / vCenter",
            endpoint="https://vcsa.test.local/sdk",
        )
        db.add(connector)
        db.flush()
        legacy = models.VmInventory(
            connector_id=connector.id,
            external_id=None,
            host_name=None,
            vm_name="legacy-name",
            source_platform="VMware ESXi / vCenter",
            target_platform="Unassigned",
            cpu=4,
            memory_gb=8,
            disk_gb=120,
            os_type="Ubuntu Linux (64-bit)",
            ip_address="192.168.12.40",
            details_json=json.dumps({"path": "/Datacenter/vm/legacy-name"}),
            current_status="Discovered",
        )
        db.add(legacy)
        db.commit()

        assert sync_discovered_vms(
            db,
            connector,
            [{
                "external_id": "vm-900",
                "vm_name": "renamed-vm",
                "host_name": "esx01.test.local",
                "source_platform": "VMware ESXi / vCenter",
                "cpu": 4,
                "memory_gb": 8,
                "disk_gb": 120,
                "os_type": "Ubuntu Linux (64-bit)",
                "ip_address": "192.168.12.40",
                "path": "/Datacenter/vm/renamed-vm",
            }],
        ) == 1

        db.refresh(legacy)
        assert legacy.vm_name == "renamed-vm"
        assert legacy.external_id == "vm-900"
        assert legacy.host_name == "esx01.test.local"
        assert db.query(models.VmInventory).count() == 1


def test_sync_discovered_vms_prunes_stale_inventory_for_connector():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        connector = models.ConnectorProfile(
            name="vCenter Source",
            connector_category="host",
            connector_type="VMware ESXi / vCenter",
            endpoint="https://vcsa.test.local/sdk",
        )
        other_connector = models.ConnectorProfile(
            name="Other Source",
            connector_category="host",
            connector_type="KVM",
            endpoint="qemu+ssh://root@kvm/system",
        )
        db.add_all([connector, other_connector])
        db.flush()
        stale_vm = models.VmInventory(
            connector_id=connector.id,
            external_id="vm-100",
            host_name="esx01.test.local",
            vm_name="deleted-vm",
            source_platform="VMware ESXi / vCenter",
            target_platform="Unassigned",
            cpu=2,
            memory_gb=4,
            disk_gb=40,
            os_type="Linux",
            current_status="Discovered",
        )
        renamed_vm = models.VmInventory(
            connector_id=connector.id,
            external_id="vm-200",
            host_name="esx01.test.local",
            vm_name="old-name",
            source_platform="VMware ESXi / vCenter",
            target_platform="Unassigned",
            cpu=4,
            memory_gb=8,
            disk_gb=120,
            os_type="Ubuntu Linux (64-bit)",
            current_status="Discovered",
        )
        unaffected_vm = models.VmInventory(
            connector_id=other_connector.id,
            external_id="vm-300",
            host_name="kvm",
            vm_name="other-connector-vm",
            source_platform="KVM",
            target_platform="Unassigned",
            cpu=2,
            memory_gb=4,
            disk_gb=40,
            os_type="Linux",
            current_status="Discovered",
        )
        db.add_all([stale_vm, renamed_vm, unaffected_vm])
        db.commit()

        assert sync_discovered_vms(
            db,
            connector,
            [{
                "external_id": "vm-200",
                "vm_name": "new-name",
                "host_name": "esx01.test.local",
                "source_platform": "VMware ESXi / vCenter",
                "cpu": 4,
                "memory_gb": 8,
                "disk_gb": 120,
                "os_type": "Ubuntu Linux (64-bit)",
                "ip_address": "192.168.12.40",
                "path": "/Datacenter/vm/new-name",
            }],
        ) == 1

        assert db.get(models.VmInventory, stale_vm.id) is None
        refreshed_vm = db.get(models.VmInventory, renamed_vm.id)
        assert refreshed_vm is not None
        assert refreshed_vm.vm_name == "new-name"
        assert db.get(models.VmInventory, unaffected_vm.id) is not None
        assert db.query(models.VmInventory).count() == 2


def test_continue_migration_plan_reuses_preserved_staging(monkeypatch, tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        source = models.ConnectorProfile(name="KVM Source", connector_category="host", connector_type="KVM")
        target = models.ConnectorProfile(name="vCenter Target", connector_category="host", connector_type="VMware ESXi / vCenter")
        db.add_all([source, target])
        db.flush()
        vm = models.VmInventory(vm_name="vm-01", source_platform="KVM", target_platform="VMware ESXi / vCenter", cpu=4, memory_gb=8, disk_gb=80, connector_id=source.id)
        db.add(vm)
        db.flush()
        plan = models.MigrationPlan(
            name="Resume Plan",
            source_connector_id=source.id,
            target_connector_id=target.id,
            migration_type="KVM to VMware ESXi / vCenter",
            vm_ids_json=json.dumps([vm.id]),
            status="Failed",
            results_json=json.dumps([{"vm_id": vm.id, "vm_name": vm.vm_name, "ok": False, "can_resume": True, "message": "Provisioning failed after conversion"}]),
        )
        db.add(plan)
        db.commit()
        db.refresh(plan)

        stage_path = tmp_path / f"plan-{plan.id}" / f"vm-{vm.id}-vm-01"
        stage_path.mkdir(parents=True)
        (stage_path / "vm-01-shifted-disk1.vmdk").write_text("converted", encoding="utf-8")

        monkeypatch.setattr("app.main.STAGING_ROOT", tmp_path)
        monkeypatch.setattr(
            "app.main.create_spark_job",
            lambda payload: {"id": 77, "plan_id": payload["plan_id"], "status": "Queued", "adapter": "kvm-vcenter-ova"},
        )

        response = continue_migration_plan(
            plan.id,
            schemas.MigrationContinue(confirmation=plan.name),
            db,
            models.LocalUser(username="admin", password_hash="unused", role="admin", is_active="true"),
        )

        assert response["job"]["id"] == 77
        assert response["resume"]["allowed"] is True
        assert response["resume"]["workloads"][0]["stage_path"] == str(stage_path)
        assert db.get(models.MigrationPlan, plan.id).spark_job_id == 77


def test_apply_spark_job_logs_completed_migrations_and_cleans_plan_staging(monkeypatch, tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        source = models.ConnectorProfile(name="VMware Source", connector_category="host", connector_type="VMware ESXi / vCenter")
        target = models.ConnectorProfile(name="KVM Target", connector_category="host", connector_type="KVM")
        db.add_all([source, target])
        db.flush()
        vm = models.VmInventory(
            vm_name="vm-01",
            source_platform="VMware ESXi / vCenter",
            target_platform="KVM",
            cpu=2,
            memory_gb=4,
            disk_gb=50,
            connector_id=source.id,
            current_status="Migration in progress",
        )
        db.add(vm)
        db.flush()
        plan = models.MigrationPlan(
            name="Prod Wave 1",
            source_connector_id=source.id,
            target_connector_id=target.id,
            migration_type="VMware ESXi / vCenter to KVM",
            vm_ids_json=json.dumps([vm.id]),
            status="Running",
        )
        db.add(plan)
        db.commit()
        db.refresh(plan)

        monkeypatch.setattr("app.main.STAGING_ROOT", tmp_path)
        plan_dir = tmp_path / "Plan-Prod-Wave-1" / "vm-01"
        plan_dir.mkdir(parents=True)
        (plan_dir / "vm-01-shifted-disk1.qcow2").write_text("converted", encoding="utf-8")

        apply_spark_job(
            db,
            plan,
            {
                "id": 321,
                "status": "Succeeded",
                "result": [
                    {
                        "vm_id": vm.id,
                        "vm_name": vm.vm_name,
                        "ok": True,
                        "target_name": "vm-01-migrated",
                        "message": "Provisioned on KVM",
                    }
                ],
            },
        )
        db.commit()

        vm_row = db.get(models.VmInventory, vm.id)
        history = (
            db.query(models.VmStatusHistory)
            .filter(models.VmStatusHistory.vm_id == vm.id)
            .order_by(models.VmStatusHistory.changed_at.desc())
            .first()
        )
        log_path = tmp_path / "migrated-vms.log"
        log_text = log_path.read_text(encoding="utf-8")

        assert vm_row.current_status == "Cutover completed"
        assert history is not None
        assert "Migration completed at" in (history.note or "")
        assert log_path.exists()
        assert "plan=Prod Wave 1" in log_text
        assert "vm=vm-01" in log_text
        assert not (tmp_path / "Plan-Prod-Wave-1").exists()


def test_apply_spark_job_marks_plan_canceled_without_mutating_vm_status():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        source = models.ConnectorProfile(name="VMware Source", connector_category="host", connector_type="VMware ESXi / vCenter")
        target = models.ConnectorProfile(name="KVM Target", connector_category="host", connector_type="KVM")
        db.add_all([source, target])
        db.flush()
        vm = models.VmInventory(
            vm_name="vm-01",
            source_platform="VMware ESXi / vCenter",
            target_platform="KVM",
            cpu=2,
            memory_gb=4,
            disk_gb=50,
            connector_id=source.id,
            current_status="Migration in progress",
        )
        db.add(vm)
        db.flush()
        plan = models.MigrationPlan(
            name="Canceled Plan",
            source_connector_id=source.id,
            target_connector_id=target.id,
            migration_type="VMware ESXi / vCenter to KVM",
            vm_ids_json=json.dumps([vm.id]),
            status="Running",
            spark_job_id=555,
        )
        db.add(plan)
        db.commit()

        apply_spark_job(
            db,
            plan,
            {
                "id": 555,
                "status": "Canceled",
                "message": "Execution was force-stopped by an operator",
                "result": [{"kind": "vm_result", "ok": False, "message": "Execution was force-stopped by an operator"}],
            },
        )
        db.commit()
        db.refresh(plan)
        db.refresh(vm)

        assert plan.status == "Canceled"
        assert vm.current_status == "Migration in progress"


def test_force_stop_migration_plan_cancels_running_spark_job(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        source = models.ConnectorProfile(name="KVM Source", connector_category="host", connector_type="KVM")
        target = models.ConnectorProfile(name="vCenter Target", connector_category="host", connector_type="VMware ESXi / vCenter")
        db.add_all([source, target])
        db.flush()
        vm = models.VmInventory(vm_name="vm-01", source_platform="KVM", target_platform="VMware ESXi / vCenter", cpu=4, memory_gb=8, disk_gb=80, connector_id=source.id, current_status="Migration in progress")
        db.add(vm)
        db.flush()
        plan = models.MigrationPlan(
            name="Running Plan",
            source_connector_id=source.id,
            target_connector_id=target.id,
            migration_type="KVM to VMware ESXi / vCenter",
            vm_ids_json=json.dumps([vm.id]),
            status="Running",
            spark_job_id=77,
        )
        db.add(plan)
        db.commit()
        db.refresh(plan)

        monkeypatch.setattr(
            "app.main.cancel_spark_job",
            lambda job_id: {
                "id": job_id,
                "status": "Canceled",
                "message": "Execution was force-stopped by an operator",
                "result": [{"kind": "vm_result", "ok": False, "message": "Execution was force-stopped by an operator"}],
            },
        )

        response = force_stop_migration_plan(
            plan.id,
            schemas.MigrationForceStop(confirmation=plan.name),
            db,
            models.LocalUser(username="admin", password_hash="unused", role="admin", is_active="true"),
        )

        assert response["job"]["status"] == "Canceled"
        assert response["stopped_by"] == "admin"
        assert db.get(models.MigrationPlan, plan.id).status == "Canceled"


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


def test_connector_defaults_include_kvm_target_storage_pool():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        source = models.ConnectorProfile(name="VMware Source", connector_category="host", connector_type="VMware ESXi / vCenter")
        target = models.ConnectorProfile(
            name="KVM Target",
            connector_category="host",
            connector_type="KVM",
            target_storage_pool="default",
            target_network="br11",
        )
        db.add_all([source, target])
        db.flush()
        vm = models.VmInventory(vm_name="vm-01", source_platform="VMware ESXi / vCenter", target_platform="KVM", cpu=2, memory_gb=4, disk_gb=50, connector_id=source.id)
        db.add(vm)
        db.flush()

        plan = create_migration_plan(
            schemas.MigrationPlanCreate(name="VMware to KVM", vm_ids=[vm.id], target_connector_id=target.id),
            db,
            None,
        )
        execution_payload = migration_plan_execution_payload(
            plan,
            source,
            target,
            [vm],
            "admin",
            live=False,
        )

        assert execution_payload["options"]["target_storage_pool"] == "default"
        assert execution_payload["options"]["target_network"] == "br11"


def test_dashboard_summary_counts_planned_vms_from_plan_membership():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        source = models.ConnectorProfile(name="VMware Source", connector_category="host", connector_type="VMware ESXi / vCenter")
        target = models.ConnectorProfile(name="KVM Target", connector_category="host", connector_type="KVM")
        db.add_all([source, target])
        db.flush()
        vm1 = models.VmInventory(vm_name="vm-01", source_platform="VMware ESXi / vCenter", target_platform="KVM", cpu=2, memory_gb=4, disk_gb=50, connector_id=source.id, current_status="Discovered")
        vm2 = models.VmInventory(vm_name="vm-02", source_platform="VMware ESXi / vCenter", target_platform="KVM", cpu=2, memory_gb=4, disk_gb=50, connector_id=source.id, current_status="Blocked")
        vm3 = models.VmInventory(vm_name="vm-03", source_platform="VMware ESXi / vCenter", target_platform="KVM", cpu=2, memory_gb=4, disk_gb=50, connector_id=source.id, current_status="Validation completed")
        db.add_all([vm1, vm2, vm3])
        db.flush()
        db.add_all(
            [
                models.MigrationPlan(
                    name="Plan 1",
                    source_connector_id=source.id,
                    target_connector_id=target.id,
                    migration_type="VMware ESXi / vCenter to KVM",
                    vm_ids_json=json.dumps([vm1.id, vm2.id]),
                ),
                models.MigrationPlan(
                    name="Plan 2",
                    source_connector_id=source.id,
                    target_connector_id=target.id,
                    migration_type="VMware ESXi / vCenter to KVM",
                    vm_ids_json=json.dumps([vm2.id, vm3.id]),
                ),
            ]
        )
        db.commit()

        summary = raw_dashboard_summary(db)

        assert summary.total_plans == 2
        assert summary.vms_discovered == 3
        assert summary.vms_planned == 3
        assert summary.vms_migrated == 1
        assert summary.vms_failed_or_blocked == 1


def test_dashboard_summary_uses_live_absolute_counts():
    summary = schemas.DashboardSummary(
        total_plans=5,
        vms_discovered=136,
        vms_planned=4,
        vms_migrated=2,
        vms_failed_or_blocked=1,
        progress_percent=1,
        by_status={"Discovered": 130, "Validation completed": 2, "Blocked": 1},
    )

    assert summary.vms_discovered == 136
    assert summary.total_plans == 5
    assert summary.vms_planned == 4
    assert summary.vms_migrated == 2
    assert summary.vms_failed_or_blocked == 1


def test_wave_update_delete_and_execute(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        source = models.ConnectorProfile(name="KVM Source", connector_category="host", connector_type="KVM", endpoint="qemu+ssh://root@kvm/system")
        target = models.ConnectorProfile(name="vCenter Target", connector_category="host", connector_type="VMware ESXi / vCenter", endpoint="https://vcsa.test.local/sdk")
        db.add_all([source, target])
        db.flush()
        vm1 = models.VmInventory(vm_name="vm-01", source_platform="KVM", target_platform="VMware ESXi / vCenter", cpu=2, memory_gb=4, disk_gb=50, connector_id=source.id)
        vm2 = models.VmInventory(vm_name="vm-02", source_platform="KVM", target_platform="VMware ESXi / vCenter", cpu=2, memory_gb=4, disk_gb=50, connector_id=source.id)
        db.add_all([vm1, vm2])
        db.flush()
        plan1 = create_migration_plan(
            schemas.MigrationPlanCreate(name="Wave Plan 1", vm_ids=[vm1.id], target_connector_id=target.id),
            db,
            None,
        )
        plan2 = create_migration_plan(
            schemas.MigrationPlanCreate(name="Wave Plan 2", vm_ids=[vm2.id], target_connector_id=target.id),
            db,
            None,
        )
        wave = create_wave(
            schemas.WaveCreate(wave_name="Wave A", planned_window="Tonight", plan_ids=[plan1.id]),
            db,
            None,
        )

        assert db.get(models.VmInventory, vm1.id).wave_id == wave.id
        assert db.get(models.VmInventory, vm1.id).migration_wave == "Wave A"
        assert db.get(models.VmInventory, vm2.id).wave_id is None

        updated = update_wave(
            wave.id,
            schemas.WaveUpdate(wave_name="Wave B", planned_window="Tomorrow", notes="Updated", plan_ids=[plan2.id]),
            db,
            None,
        )
        assert updated.wave_name == "Wave B"
        assert json.loads(updated.plan_ids_json) == [plan2.id]
        assert db.get(models.VmInventory, vm1.id).wave_id is None
        assert db.get(models.VmInventory, vm2.id).wave_id == wave.id
        assert db.get(models.VmInventory, vm2.id).migration_wave == "Wave B"

        job_ids = iter([101, 102])

        monkeypatch.setattr(
            "app.main.create_spark_job",
            lambda payload: {"id": next(job_ids), "plan_id": payload["plan_id"], "status": "Queued", "adapter": "test"},
        )
        admin = models.LocalUser(username="admin", password_hash="unused", role="admin", is_active="true")
        execution = execute_wave(wave.id, schemas.WaveExecution(confirmation="Wave B"), db, admin)
        assert execution["wave"].status == "Queued"
        assert [row["plan"].id for row in execution["jobs"]] == [plan2.id]
        assert execution["jobs"][0]["job"]["id"] == 101
        assert db.get(models.MigrationPlan, plan2.id).spark_job_id == 101

        delete_wave(wave.id, db, None)
        assert db.query(models.MigrationWave).count() == 0
        assert db.get(models.VmInventory, vm2.id).wave_id is None
        assert db.get(models.VmInventory, vm2.id).migration_wave is None
