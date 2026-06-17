from pathlib import Path

from launchgrid_app import resolve_imported_disk_path


def test_resolve_imported_disk_path_prefers_exact_filename(monkeypatch):
    monkeypatch.setattr(
        "launchgrid_app.run",
        lambda command, *, env, timeout=1800: "\n".join(
            [
                "DS-Shift/test-vm/test-vm-disk1-flat.vmdk",
                "DS-Shift/test-vm/test-vm-disk1.vmdk",
            ]
        ),
    )
    resolved = resolve_imported_disk_path({}, "DS-Shift/test-vm", Path("/tmp/test-vm-disk1.vmdk"))
    assert resolved == "DS-Shift/test-vm/test-vm-disk1.vmdk"


def test_resolve_imported_disk_path_accepts_single_vmdk_candidate(monkeypatch):
    monkeypatch.setattr(
        "launchgrid_app.run",
        lambda command, *, env, timeout=1800: "DS-Shift/test-vm/uploaded-disk.vmdk",
    )
    resolved = resolve_imported_disk_path({}, "DS-Shift/test-vm", Path("/tmp/test-vm-disk1.vmdk"))
    assert resolved == "DS-Shift/test-vm/uploaded-disk.vmdk"
