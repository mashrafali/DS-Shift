import xml.etree.ElementTree as ET

from host_migrations import ensure_libguestfs_ready, normalize_kvm_interfaces, ovf_descriptor, parse_domain_xml, safe_name, shifted_artifact_base_name, transient_secret_descriptor, virt_v2v_env, vpx_uri


class Connector:
    endpoint = "https://vcenter.example.test/sdk"
    port = 443
    username = "administrator@vsphere.local"


class Workload:
    vm_name = "source-vm"
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


def test_shifted_artifact_base_name_replaces_migrated_suffix():
    assert shifted_artifact_base_name("test-vm-migrated") == "test-vm-shifted"
    assert shifted_artifact_base_name("test-vm") == "test-vm-shifted"


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
