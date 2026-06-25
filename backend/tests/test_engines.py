import json

from app import engines


def test_populate_kvm_disk_sizes_reads_virtual_capacity(monkeypatch):
    commands = []
    disks = [{"target": "vda", "source": "/var/lib/libvirt/images/test.qcow2", "size_gb": 0}]

    monkeypatch.setattr(
        engines,
        "_ssh_exec",
        lambda client, command, timeout=20: (
            0,
            json.dumps({"virtual-size": 12 * 1024 * 1024 * 1024}),
            "",
        ),
    )

    engines._populate_kvm_disk_sizes(object(), disks, commands)

    assert disks[0]["size_gb"] == 12
    assert commands == ["detect virtual disk size for /var/lib/libvirt/images/test.qcow2"]


def test_populate_kvm_disk_sizes_falls_back_to_block_device_size(monkeypatch):
    commands = []
    disks = [{"target": "vda", "source": "/dev/vg0/test", "size_gb": 0}]

    def fake_exec(_client, command, timeout=20):
        if "qemu-img info" in command:
            return 1, "", "unsupported"
        if "blockdev --getsize64" in command:
            return 0, str(40 * 1024 * 1024 * 1024), ""
        return 1, "", "not reached"

    monkeypatch.setattr(engines, "_ssh_exec", fake_exec)

    engines._populate_kvm_disk_sizes(object(), disks, commands)

    assert disks[0]["size_gb"] == 40
    assert commands == ["detect virtual disk size for /dev/vg0/test"]


def test_summarize_kvm_discovery_commands_matches_vcenter_style():
    commands = [
        "virsh list --all --name",
        "detect virtual disk size for /var/lib/libvirt/images/vm1.qcow2",
        "detect virtual disk size for /var/lib/libvirt/images/vm2.qcow2",
        "inspected 2 VM(s): dominfo, dumpxml, domblklist, domiflist, domifaddr",
    ]

    assert engines.summarize_discovery_commands("KVM", commands) == [
        "virsh list --all --name",
        "inspected 2 VM(s): dominfo, dumpxml, domblklist, domiflist, domifaddr",
        "detected virtual disk sizes for 2 disk(s)",
    ]
