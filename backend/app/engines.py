from __future__ import annotations

import os
import re
import ssl
import subprocess
from dataclasses import dataclass
import json
import math
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import paramiko
from pyVim.connect import Disconnect, SmartConnect
from pyVmomi import vim


@dataclass
class EngineResult:
    ok: bool
    message: str
    records: list[dict]
    commands: list[str]
    raw: str = ""


def _run(command: list[str], timeout: int = 45, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, timeout=timeout, env=env, check=False)


def _credential_from_env(reference: str | None) -> str | None:
    if reference and reference.startswith("env:"):
        return os.getenv(reference.split(":", 1)[1])
    return None


def _password_value(reference: str | None, credential_payload: dict | None = None) -> str | None:
    if credential_payload and credential_payload.get("password"):
        return str(credential_payload["password"])
    return _credential_from_env(reference)


def _ssh_parts(endpoint: str | None, username: str | None) -> tuple[str, int, str]:
    if not endpoint:
        raise ValueError("Connector endpoint is required")
    parsed = urlparse(endpoint)
    if parsed.scheme.startswith("qemu+ssh"):
        host = parsed.hostname or ""
        user = parsed.username or username or "root"
        port = parsed.port or 22
    else:
        host_port = endpoint.replace("https://", "").replace("http://", "").split("/")[0]
        host = host_port.split(":", 1)[0]
        user = username or (endpoint.split("@", 1)[0] if "@" in endpoint else "root")
        port = int(host_port.split(":", 1)[1]) if ":" in host_port and host_port.rsplit(":", 1)[1].isdigit() else 22
    if not host:
        raise ValueError("Connector host could not be parsed from endpoint")
    return host, port, user


def _ssh_client(endpoint: str | None, username: str | None, credential_reference: str | None, credential_payload: dict | None = None) -> paramiko.SSHClient:
    host, port, user = _ssh_parts(endpoint, username)
    password = _password_value(credential_reference, credential_payload)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        port=port,
        username=user,
        password=password,
        look_for_keys=not bool(password),
        allow_agent=not bool(password),
        timeout=10,
        banner_timeout=10,
        auth_timeout=10,
    )
    return client


def _ssh_exec(client: paramiko.SSHClient, command: str, timeout: int = 30) -> tuple[int, str, str]:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    code = stdout.channel.recv_exit_status()
    return code, stdout.read().decode(errors="replace"), stderr.read().decode(errors="replace")


def _kvm_firmware(xml_text: str) -> str:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return "bios"
    os_node = root.find("./os")
    if os_node is None:
        return "bios"
    type_node = os_node.find("./type")
    firmware_attr = (type_node.get("firmware") or "").lower() if type_node is not None else ""
    if firmware_attr in {"efi", "uefi"}:
        return "efi"
    loader = os_node.find("./loader")
    loader_type = (loader.get("type") or "").lower() if loader is not None else ""
    loader_path = (loader.text or "").lower() if loader is not None and loader.text else ""
    if loader_type == "pflash" or "ovmf" in loader_path:
        return "efi"
    return "bios"


def validate_kvm(endpoint: str | None, username: str | None, credential_reference: str | None = None, credential_payload: dict | None = None) -> EngineResult:
    commands = ["SSH connect", "virsh list --all --name"]
    try:
        with _ssh_client(endpoint, username, credential_reference, credential_payload) as client:
            code, out, err = _ssh_exec(client, "virsh list --all --name", timeout=15)
            if code != 0:
                return EngineResult(False, "KVM validation failed. SSH works, but virsh access failed.", [], commands, err + out)
            return EngineResult(True, "KVM connector validated. SSH and virsh are reachable.", [], commands, out)
    except Exception as exc:
        return EngineResult(False, f"KVM validation failed: {exc}", [], commands)


def discover_kvm(endpoint: str | None, username: str | None, credential_reference: str | None = None, credential_payload: dict | None = None) -> EngineResult:
    commands = ["virsh list --all --name"]
    try:
        client = _ssh_client(endpoint, username, credential_reference, credential_payload)
    except Exception as exc:
        return EngineResult(False, f"KVM discovery failed. SSH access is not available: {exc}", [], commands)
    records: list[dict] = []
    try:
        code, listed, err = _ssh_exec(client, "virsh list --all --name", timeout=20)
        if code != 0:
            return EngineResult(False, "KVM discovery failed. virsh access is not available.", [], commands, err + listed)
        for name in [line.strip() for line in listed.splitlines() if line.strip()]:
            command = (
                f"virsh dominfo {name!r}; "
                f"echo __XML__; virsh dumpxml {name!r}; "
                f"echo __BLK__; virsh domblklist {name!r}; "
                f"echo __IF__; virsh domiflist {name!r}; "
                f"echo __ADDR__; virsh domifaddr {name!r} || true"
            )
            code, text, detail_err = _ssh_exec(client, command, timeout=30)
            commands.append(command)
            if code != 0:
                continue
            cpu = _match_int(text, r"CPU\(s\):\s+(\d+)") or 0
            mem_kib = _match_int(text, r"Max memory:\s+(\d+) KiB") or _match_int(text, r"Used memory:\s+(\d+) KiB") or 0
            state = _match_text(text, r"State:\s+(.+)") or "unknown"
            os_type = _kvm_os_name(text)
            xml_text = _section(text, "__XML__", "__BLK__")
            disks = _parse_kvm_disks(_section(text, "__BLK__", "__IF__"))
            _populate_kvm_disk_sizes(client, disks, commands)
            ip_address = _match_text(_section(text, "__ADDR__", None), r"ipv4\s+([0-9.]+)/")
            records.append(
                {
                    "external_id": _match_text(xml_text, r"<uuid>([^<]+)</uuid>") or name,
                    "vm_name": name,
                    "host_name": _match_text(text, r"(?m)^Host:\s+(.+)$"),
                    "source_platform": "KVM",
                    "cpu": cpu,
                    "memory_gb": round(mem_kib / 1024 / 1024),
                    "disk_gb": sum(disk.get("size_gb", 0) for disk in disks),
                    "os_type": os_type,
                    "ip_address": ip_address,
                    "current_status": "Discovered",
                    "power_state": state,
                    "boot_firmware": _kvm_firmware(xml_text),
                    "disks": disks,
                    "nics": _parse_kvm_nics(_section(text, "__IF__", "__ADDR__")),
                    "raw_error": detail_err,
                }
            )
        return EngineResult(True, f"Discovered {len(records)} KVM VMs", records, commands, listed)
    finally:
        client.close()


def _vc_host(endpoint: str | None) -> str | None:
    if not endpoint:
        return None
    parsed = urlparse(endpoint if "://" in endpoint else f"https://{endpoint}")
    return parsed.hostname


def _vc_connect(endpoint: str | None, username: str | None, credential_reference: str | None, credential_payload: dict | None = None):
    host = _vc_host(endpoint)
    password = _password_value(credential_reference, credential_payload)
    if not host or not username or not password:
        raise ValueError("vCenter validation requires endpoint, username, and an env: credential reference available in the backend container")
    context = ssl._create_unverified_context()
    return SmartConnect(host=host, user=username, pwd=password, sslContext=context, connectionPoolTimeout=20)


def validate_vcenter(endpoint: str | None, username: str | None, credential_reference: str | None, credential_payload: dict | None = None) -> EngineResult:
    commands = ["pyvmomi SmartConnect", "Retrieve ServiceContent.About"]
    try:
        service_instance = _vc_connect(endpoint, username, credential_reference, credential_payload)
        try:
            about = service_instance.RetrieveContent().about
            return EngineResult(True, f"vCenter connector validated. Connected to {about.fullName}.", [], commands)
        finally:
            Disconnect(service_instance)
    except Exception as exc:
        return EngineResult(False, f"vCenter validation failed: {exc}", [], commands)


def discover_vcenter(endpoint: str | None, username: str | None, credential_reference: str | None, credential_payload: dict | None = None) -> EngineResult:
    commands = ["pyvmomi SmartConnect", "ContainerView VirtualMachine"]
    try:
        service_instance = _vc_connect(endpoint, username, credential_reference, credential_payload)
    except Exception as exc:
        return EngineResult(False, f"vCenter discovery failed: {exc}", [], commands)
    records: list[dict] = []
    try:
        content = service_instance.RetrieveContent()
        view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
        try:
            for vm_obj in view.view[:250]:
                summary = vm_obj.summary
                config = summary.config
                runtime = summary.runtime
                guest = summary.guest
                disks = []
                for device in vm_obj.config.hardware.device if vm_obj.config and vm_obj.config.hardware else []:
                    if isinstance(device, vim.vm.device.VirtualDisk):
                        disks.append({"label": device.deviceInfo.label, "size_gb": round((device.capacityInKB or 0) / 1024 / 1024)})
                records.append(
                    {
                        "external_id": getattr(config, "instanceUuid", None) or getattr(vm_obj, "_moId", None),
                        "vm_name": config.name,
                        "host_name": getattr(getattr(runtime, "host", None), "name", None),
                        "source_platform": "VMware ESXi / vCenter",
                        "cpu": config.numCpu or 0,
                        "memory_gb": round((config.memorySizeMB or 0) / 1024),
                        "disk_gb": sum(disk["size_gb"] for disk in disks),
                        "os_type": config.guestFullName or "Unknown",
                        "ip_address": guest.ipAddress,
                        "current_status": "Discovered",
                        "power_state": str(runtime.powerState),
                        "boot_firmware": (getattr(vm_obj.config, "firmware", None) or "bios").lower(),
                        "disks": disks,
                        "path": _inventory_path(vm_obj),
                    }
                )
        finally:
            view.Destroy()
        return EngineResult(True, f"Discovered {len(records)} vCenter VMs", records, commands)
    finally:
        Disconnect(service_instance)


def build_kvm_to_esxi_preflight(
    source_endpoint: str | None,
    source_username: str | None,
    source_credential_reference: str | None,
    source_credential_payload: dict | None,
    target_endpoint: str | None,
    target_username: str | None,
    target_credential_reference: str | None,
    target_credential_payload: dict | None,
    vm_name: str,
    target_datastore: str | None,
) -> EngineResult:
    source_uri = source_endpoint or "qemu+ssh://root@kvm/system"
    commands = [
        "KVM SSH/virsh source validation",
        "vCenter pyvmomi target validation",
        "Spark Engine kvm-vcenter-ova adapter",
    ]
    runbook = [
        {"step": "Validate source KVM connector", "command": "SSH connect and run virsh list --all --name"},
        {"step": "Validate target vCenter connector", "command": "pyvmomi SmartConnect and retrieve ServiceContent.About"},
        {"step": "Inspect source VM state", "command": f"virsh -c {source_uri} domstate {vm_name}"},
        {"step": "Inspect source VM disks", "command": f"virsh -c {source_uri} domblklist {vm_name}"},
        {
            "step": "Package and import",
            "command": "Spark Engine converts disks with qemu-img, packages an OVA, and imports it with govc",
        },
        {"step": "Validate target VM inventory", "command": f"pyvmomi/govc vm lookup for {vm_name}"},
    ]

    source_check = validate_kvm(source_endpoint, source_username, source_credential_reference, source_credential_payload)
    target_check = validate_vcenter(target_endpoint, target_username, target_credential_reference, target_credential_payload)
    records = [
        {"check": "source_kvm", "ok": source_check.ok, "message": source_check.message},
        {"check": "target_vcenter", "ok": target_check.ok, "message": target_check.message},
    ]

    if source_check.ok:
        try:
            with _ssh_client(source_endpoint, source_username, source_credential_reference, source_credential_payload) as client:
                code, state, err = _ssh_exec(client, f"virsh domstate {vm_name!r}", timeout=15)
                records.append({"check": "source_vm_state", "ok": code == 0, "message": state.strip() if code == 0 else err.strip()})
                code, disks, err = _ssh_exec(client, f"virsh domblklist {vm_name!r}", timeout=15)
                records.append({"check": "source_vm_disks", "ok": code == 0, "message": disks.strip() if code == 0 else err.strip()})
        except Exception as exc:
            records.append({"check": "source_vm_inspection", "ok": False, "message": str(exc)})

    records.append(
        {
            "check": "execution_adapter",
            "ok": True,
            "message": "Spark Engine kvm-vcenter-ova execution adapter is implemented.",
        }
    )

    failed = [record for record in records if not record["ok"]]
    if failed:
        return EngineResult(False, "Migration test preflight blocked. Source or target validation failed.", records + runbook, commands)
    return EngineResult(
        True,
        "Connector preflight passed. Spark Engine provides the KVM-to-vCenter packaging and import adapter.",
        records + runbook,
        commands,
    )


def _match_int(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else None


def _match_text(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else None


def _kvm_os_name(text: str) -> str:
    xml_text = _section(text, "__XML__", "__BLK__")
    libosinfo_id = _match_text(xml_text, r"<libosinfo:os id=\"([^\"]+)\"")
    if libosinfo_id:
        parts = libosinfo_id.rstrip("/").split("/")
        if len(parts) >= 2:
            vendor = parts[-2]
            version = parts[-1]
            vendor_label = {
                "rhel": "Red Hat Enterprise Linux",
                "ubuntu": "Ubuntu",
                "centos-stream": "CentOS Stream",
                "centos": "CentOS",
                "rocky": "Rocky Linux",
                "almalinux": "AlmaLinux",
                "debian": "Debian",
                "fedora": "Fedora",
                "opensuse": "openSUSE",
                "sles": "SUSE Linux Enterprise Server",
                "sle": "SUSE Linux Enterprise",
                "win": "Windows",
                "windows": "Windows",
            }.get(vendor, vendor.replace("-", " ").title())
            return f"{vendor_label} {version}"
        return libosinfo_id
    return _match_text(text, r"OS Type:\s+(.+)") or "Unknown"


def _section(text: str, start: str, end: str | None) -> str:
    if start not in text:
        return ""
    section = text.split(start, 1)[1]
    if end and end in section:
        section = section.split(end, 1)[0]
    return section


def _parse_kvm_disks(text: str) -> list[dict]:
    disks = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] not in {"Target", "-"} and parts[0].startswith(("vd", "sd", "hd")):
            disks.append({"target": parts[0], "source": parts[-1], "size_gb": 0})
    return disks


def _populate_kvm_disk_sizes(client: paramiko.SSHClient, disks: list[dict], commands: list[str]) -> None:
    for disk in disks:
        source = disk.get("source")
        if not source or source in {"-", "none"}:
            continue
        command = f"qemu-img info --output json {source!r}"
        commands.append(command)
        code, out, _ = _ssh_exec(client, command, timeout=20)
        if code != 0:
            continue
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            continue
        virtual_size = int(payload.get("virtual-size") or 0)
        if virtual_size > 0:
            disk["size_gb"] = max(1, math.ceil(virtual_size / 1024 / 1024 / 1024))


def _parse_kvm_nics(text: str) -> list[dict]:
    nics = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].startswith("vnet"):
            nics.append({"interface": parts[0], "type": parts[1], "source": parts[2], "model": parts[3], "mac": parts[4]})
    return nics


def _inventory_path(obj) -> str:
    names = []
    current = obj
    while current:
        name = getattr(current, "name", None)
        if name:
            names.append(name)
        current = getattr(current, "parent", None)
    return "/" + "/".join(reversed(names))
