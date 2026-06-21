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
    assert commands == ["qemu-img info --output json '/var/lib/libvirt/images/test.qcow2'"]
