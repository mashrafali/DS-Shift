import xml.etree.ElementTree as ET
from pathlib import Path
import sys
from types import SimpleNamespace

import host_migrations
from host_migrations import (
    STAGING_ROOT,
    append_migrated_vm_log,
    cleanup_plan_stage_directory,
    ensure_libguestfs_ready,
    launchgrid_provision,
    normalize_kvm_interfaces,
    ovf_descriptor,
    parse_domain_xml,
    remove_source_vcenter_vm,
    run,
    safe_name,
    shifted_artifact_base_name,
    shifted_target_name,
    transient_secret_descriptor,
    virt_v2v_env,
    vpx_uri,
)


class Connector:
    endpoint = "https://vcenter.example.test/sdk"
    port = 443
    username = "administrator@vsphere.local"


class Workload:
    id = 1
    vm_name = "source-vm"
    external_id = "vm-101"
    host_name = "esxi01.example.test"
    details = {"datacenter": "Primary DC", "compute_resource": "Compute Cluster"}


def test_parse_domain_xml_extracts_file_disks_and_resources():
    parsed = parse_domain_xml(
        """
        <domain>
          <memory unit="MiB">4096</memory>
          <vcpu>4</vcpu>
          <devices>
            <disk type="file" device="disk"><source file="/images/vm.qcow2"/><target dev="vda"/></disk>
            <disk type="file" device="cdrom"><source file="/images/os.iso"/><target dev="sda"/></disk>
            <interface type="bridge"/>
          </devices>
        </domain>
        """
    )
    assert parsed["cpu"] == 4
    assert parsed["memory_bytes"] == 4096 * 1024 * 1024
    assert parsed["disks"] == [{"path": "/images/vm.qcow2", "target": "vda"}]
    assert parsed["interfaces"] == 1


def test_ovf_descriptor_references_stream_optimized_disk():
    descriptor = ovf_descriptor(
        "target-vm",
        2,
        2 * 1024**3,
        [{"name": "target-vm-disk1.vmdk", "size": 1024, "capacity": 4096}],
        1,
    )
    assert 'ovf:href="target-vm-disk1.vmdk"' in descriptor
    assert "streamOptimized" in descriptor
    assert "<rasd:VirtualQuantity>2</rasd:VirtualQuantity>" in descriptor
    assert "<rasd:Connection>VM Network</rasd:Connection>" in descriptor


def test_vpx_uri_uses_connector_and_discovery_metadata():
    uri = vpx_uri(Connector(), Workload(), {})
    assert uri == (
        "vpx://administrator%40vsphere.local@vcenter.example.test:443/"
        "Primary%20DC/Compute%20Cluster/esxi01.example.test?no_verify=1"
    )


def test_safe_name_rejects_empty_values():
    assert safe_name("VM migration 01") == "VM-migration-01"


def test_virt_v2v_env_enables_guestfs_debugging(monkeypatch):
    monkeypatch.delenv("LIBGUESTFS_DEBUG", raising=False)
    monkeypatch.delenv("LIBGUESTFS_TRACE", raising=False)
    monkeypatch.delenv("LIBGUESTFS_BACKEND", raising=False)

    env = virt_v2v_env()

    assert env["LIBGUESTFS_DEBUG"] == "1"
    assert env["LIBGUESTFS_TRACE"] == "1"
    assert env["LIBGUESTFS_BACKEND"] == "direct"
    assert env["LIBGUESTFS_CACHEDIR"] == "/var/tmp"


def test_ensure_libguestfs_ready_requires_test_tool(monkeypatch):
    monkeypatch.setattr("host_migrations.shutil.which", lambda name: None)

    try:
        ensure_libguestfs_ready(timeout=1)
    except RuntimeError as exc:
        assert "libguestfs-test-tool" in str(exc)
    else:
        raise AssertionError("ensure_libguestfs_ready should fail when the test tool is unavailable")


def test_transient_secret_descriptor_uses_ephemeral_fd():
    with transient_secret_descriptor("TopSecret123", "vmware-pass") as (path, fd):
        assert path == f"/proc/self/fd/{fd}"
        with open(path, encoding="utf-8") as handle:
            assert handle.read() == "TopSecret123"


def test_run_drains_child_output_while_process_is_running():
    output = run(
        [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('e' * 200000); sys.stderr.flush(); sys.stdout.write('done')",
        ],
        timeout=10,
    )

    assert output == "done"


def test_shifted_artifact_base_name_replaces_migrated_suffix():
    assert shifted_artifact_base_name("test-vm-migrated") == "test-vm-shifted"
    assert shifted_artifact_base_name("test-vm") == "test-vm-shifted"
    assert shifted_artifact_base_name("test-vm-shifted") == "test-vm-shifted"


def test_shifted_target_name_defaults_to_shifted_suffix():
    assert shifted_target_name("test-vm") == "test-vm-shifted"
    assert shifted_target_name("test-vm", "custom-name") == "custom-name"


def test_append_migrated_vm_log_and_cleanup_plan_stage_directory(tmp_path, monkeypatch):
    monkeypatch.setattr("host_migrations.STAGING_ROOT", tmp_path)
    stage_dir = tmp_path / "Plan-Test-Plan" / "vm-01"
    stage_dir.mkdir(parents=True)

    workload = type("Workload", (), {"id": 7, "vm_name": "vm-01"})()

    append_migrated_vm_log(11, "Test Plan", workload, "vm-01-shifted", "VMware ESXi / vCenter")
    cleanup_plan_stage_directory(11, "Test Plan")

    log_path = tmp_path / "migrated-vms.log"
    assert log_path.exists()
    assert "plan=Test Plan" in log_path.read_text(encoding="utf-8")
    assert "target=vm-01-shifted" in log_path.read_text(encoding="utf-8")
    assert not (tmp_path / "Plan-Test-Plan").exists()


def test_normalize_kvm_interfaces_rebinds_to_target_bridge():
    root = ET.ElementTree(
        ET.fromstring(
            """
            <domain>
              <devices>
                <interface type="network">
                  <mac address="52:54:00:11:22:33"/>
                  <source network="MGMT-Services"/>
                  <model type="e1000"/>
                  <virtualport type="openvswitch"/>
                </interface>
              </devices>
            </domain>
            """
        )
    )

    rewired = normalize_kvm_interfaces(root, "br11")

    interface = root.find("./devices/interface")
    source = interface.find("source") if interface is not None else None
    assert rewired == 1
    assert interface is not None
    assert interface.get("type") == "bridge"
    assert source is not None
    assert source.get("bridge") == "br11"
    assert source.get("network") is None
    assert interface.find("virtualport") is None


def test_launchgrid_provision_passes_kvm_connector_scoped_target_settings(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"ok": True}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, json):
            captured["url"] = url
            captured["payload"] = json
            return FakeResponse()

    request = type(
        "Request",
        (),
        {
            "target_connector": type(
                "Connector",
                (),
                {
                    "id": 9,
                    "name": "kvm-target",
                    "connector_category": "target",
                    "connector_type": "KVM",
                    "endpoint": "qemu+ssh://root@kvm/system",
                    "port": 22,
                    "username": "root",
                    "target_network": "br11",
                    "target_datastore": None,
                    "target_storage_pool": "default",
                    "target_vdc_name": None,
                    "target_compute_name": None,
                    "credential_reference": "env:KVM_PASSWORD",
                    "credential_payload": {},
                },
            )(),
            "options": {"power_on": True, "autostart": True},
        },
    )()
    workload = type("Workload", (), {"details": {"guest_os_id": "otherGuest64", "boot_firmware": "efi"}})()

    monkeypatch.setattr("host_migrations.httpx.Client", FakeClient)

    result = launchgrid_provision(
        request,
        workload,
        "guest-shifted",
        {
            "cpu": 4,
            "memory_bytes": 8 * 1024**3,
            "boot_firmware": "efi",
            "libvirt_xml_path": "/DS-Shift-Staging/Plan-test/guest/guest-shifted.xml",
        },
        [{"local_path": "/DS-Shift-Staging/Plan-test/guest/guest-shifted-sda.qcow2", "capacity_bytes": 4096}],
    )

    assert result == {"ok": True}
    assert captured["url"].endswith("/provision")
    assert captured["payload"]["target_connector"]["connector_type"] == "KVM"
    assert captured["payload"]["target_connector"]["target_storage_pool"] == "default"
    assert captured["payload"]["target_connector"]["target_network"] == "br11"
    assert captured["payload"]["power_on"] is True
    assert captured["payload"]["autostart"] is True
    assert captured["payload"]["libvirt_xml_path"].endswith("guest-shifted.xml")


def test_remove_source_vcenter_vm_destroys_powered_off_match(monkeypatch):
    destroyed = {"called": False}

    class FakeTask:
        info = SimpleNamespace(state=host_migrations.vim.TaskInfo.State.success, error=None)

    class FakeVM:
        _moId = "vm-101"
        name = "source-vm"
        runtime = SimpleNamespace(powerState="poweredOff")

        def Destroy_Task(self):
            destroyed["called"] = True
            return FakeTask()

    class FakeView:
        view = [FakeVM()]

        def Destroy(self):
            pass

    class FakeContent:
        rootFolder = object()
        viewManager = SimpleNamespace(CreateContainerView=lambda *args: FakeView())

    class FakeServiceInstance:
        def RetrieveContent(self):
            return FakeContent()

    connector = type(
        "Connector",
        (),
        {
            "endpoint": "https://vcenter.example.test/sdk",
            "port": 443,
            "username": "administrator@vsphere.local",
            "credential_reference": None,
            "credential_payload": {"password": "secret"},
        },
    )()

    monkeypatch.setattr("host_migrations.SmartConnect", lambda **kwargs: FakeServiceInstance())
    monkeypatch.setattr("host_migrations.Disconnect", lambda service_instance: None)

    remove_source_vcenter_vm(connector, Workload())

    assert destroyed["called"] is True


def test_remove_source_vcenter_vm_rejects_powered_on_source(monkeypatch):
    destroyed = {"called": False}

    class FakeVM:
        _moId = "vm-101"
        name = "source-vm"
        runtime = SimpleNamespace(powerState="poweredOn")

        def Destroy_Task(self):
            destroyed["called"] = True

    class FakeView:
        view = [FakeVM()]

        def Destroy(self):
            pass

    class FakeContent:
        rootFolder = object()
        viewManager = SimpleNamespace(CreateContainerView=lambda *args: FakeView())

    class FakeServiceInstance:
        def RetrieveContent(self):
            return FakeContent()

    connector = type(
        "Connector",
        (),
        {
            "endpoint": "https://vcenter.example.test/sdk",
            "port": 443,
            "username": "administrator@vsphere.local",
            "credential_reference": None,
            "credential_payload": {"password": "secret"},
        },
    )()

    monkeypatch.setattr("host_migrations.SmartConnect", lambda **kwargs: FakeServiceInstance())
    monkeypatch.setattr("host_migrations.Disconnect", lambda service_instance: None)

    try:
        remove_source_vcenter_vm(connector, Workload())
    except RuntimeError as exc:
        assert "VM is poweredOn" in str(exc)
    else:
        raise AssertionError("powered-on source VM should not be destroyed")

    assert destroyed["called"] is False
