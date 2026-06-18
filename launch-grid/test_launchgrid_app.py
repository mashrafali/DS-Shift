from pathlib import Path

from launchgrid_app import ConvertedDisk, import_placement, ovf_descriptor, request_memory_mb


def test_request_memory_mb_rounds_down_to_whole_megabytes():
    assert request_memory_mb(2 * 1024**3) == 2048


def test_import_placement_uses_host_when_present(monkeypatch):
    def fake_run(command, *, env, timeout=1800):
        if command[:4] == ["govc", "find", "-type", "h"]:
            return "./host/esxi01"
        return ""

    monkeypatch.setattr("launchgrid_app.run", fake_run)
    assert import_placement({}, "esxi01") == ["-host", "./host/esxi01"]


def test_import_placement_uses_cluster_root_resource_pool(monkeypatch):
    def fake_run(command, *, env, timeout=1800):
        if command[:4] == ["govc", "find", "-type", "h"]:
            return ""
        if command[:4] == ["govc", "find", "-type", "c"]:
            return "./host/TESTING-CLUSTER"
        raise AssertionError(command)

    monkeypatch.setattr("launchgrid_app.run", fake_run)
    assert import_placement({}, "TESTING-CLUSTER") == ["-pool", "./host/TESTING-CLUSTER/Resources"]


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
