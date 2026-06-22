from pathlib import Path

from launchgrid_app import Connector, ConvertedDisk, ProvisionRequest, absolute_inventory_path, import_placement, normalize_kvm_xml, ovf_descriptor, request_memory_mb, validate_request


def test_request_memory_mb_rounds_down_to_whole_megabytes():
    assert request_memory_mb(2 * 1024**3) == 2048


def test_absolute_inventory_path_expands_relative_paths_with_datacenter():
    assert absolute_inventory_path({"GOVC_DATACENTER": "TESTING-DC"}, "./host/TESTING-CLUSTER") == "/TESTING-DC/host/TESTING-CLUSTER"


def test_import_placement_uses_host_when_present(monkeypatch):
    def fake_run(command, *, env, timeout=1800):
        if command[:4] == ["govc", "find", "-type", "h"]:
            return "./host/esxi01"
        return ""

    monkeypatch.setattr("launchgrid_app.run", fake_run)
    assert import_placement({"GOVC_DATACENTER": "TESTING-DC"}, "esxi01") == ["-host", "/TESTING-DC/host/esxi01"]


def test_import_placement_uses_cluster_root_resource_pool(monkeypatch):
    def fake_run(command, *, env, timeout=1800):
        if command[:4] == ["govc", "find", "-type", "h"]:
            return ""
        if command[:4] == ["govc", "find", "-type", "c"]:
            return "./host/TESTING-CLUSTER"
        raise AssertionError(command)

    monkeypatch.setattr("launchgrid_app.run", fake_run)
    assert import_placement({"GOVC_DATACENTER": "TESTING-DC"}, "TESTING-CLUSTER") == ["-pool", "/TESTING-DC/host/TESTING-CLUSTER/Resources"]


def test_ovf_descriptor_includes_stream_optimized_disks_and_efi_config(tmp_path):
    disk_path = tmp_path / "target-vm-disk1.vmdk"
    disk_path.write_bytes(b"descriptor")
    descriptor = ovf_descriptor(
        "target-vm",
        2,
        2 * 1024**3,
        [ConvertedDisk(local_path=str(disk_path), capacity_bytes=4096)],
        "Prod-Network",
        "uefi",
        "otherGuest64",
    )
    assert 'ovf:href="target-vm-disk1.vmdk"' in descriptor
    assert "streamOptimized" in descriptor
    assert "<rasd:Connection>Prod-Network</rasd:Connection>" in descriptor
    assert 'vmw:key="firmware" vmw:value="efi"' in descriptor
    assert "<rasd:ResourceSubType>LsiLogic</rasd:ResourceSubType>" in descriptor


def test_validate_request_accepts_kvm_target_with_connector_scoped_settings(tmp_path):
    xml_path = tmp_path / "guest.xml"
    xml_path.write_text("<domain><devices/></domain>", encoding="utf-8")
    disk_path = tmp_path / "guest.qcow2"
    disk_path.write_bytes(b"qcow2")
    request = ProvisionRequest(
        target_connector=Connector(
            id=1,
            name="kvm-target",
            connector_category="target",
            connector_type="KVM",
            endpoint="qemu+ssh://root@kvm/system",
            username="root",
            target_network="br11",
            target_storage_pool="default",
        ),
        vm_name="guest-shifted",
        cpu=2,
        memory_bytes=2 * 1024**3,
        disks=[ConvertedDisk(local_path=str(disk_path), capacity_bytes=4096)],
        libvirt_xml_path=str(xml_path),
    )

    validate_request(request)


def test_normalize_kvm_xml_rewrites_disks_and_bridge(tmp_path):
    xml_path = tmp_path / "source.xml"
    xml_path.write_text(
        """
        <domain>
          <name>old-name</name>
          <uuid>deadbeef</uuid>
          <os>
            <type arch="x86_64" machine="pc-q35-7.2">hvm</type>
            <nvram>/var/lib/libvirt/qemu/nvram/old.fd</nvram>
          </os>
          <devices>
            <disk type="file" device="disk">
              <driver name="qemu" type="raw"/>
              <source file="/tmp/source.qcow2"/>
              <target dev="vda" bus="virtio"/>
            </disk>
            <interface type="network">
              <source network="MGMT-Services"/>
              <model type="e1000"/>
              <virtualport type="openvswitch"/>
            </interface>
          </devices>
        </domain>
        """,
        encoding="utf-8",
    )
    disk_path = tmp_path / "guest.qcow2"
    disk_path.write_bytes(b"qcow2")

    rendered = normalize_kvm_xml(
        xml_path,
        "guest-shifted",
        [ConvertedDisk(local_path=str(disk_path), capacity_bytes=4096)],
        "/var/lib/libvirt/images",
        "br12",
    )

    assert "<name>guest-shifted</name>" in rendered
    assert "deadbeef" not in rendered
    assert "/var/lib/libvirt/images/guest.qcow2" in rendered
    assert 'type="bridge"' in rendered
    assert 'bridge="br12"' in rendered
    assert "MGMT-Services" not in rendered
