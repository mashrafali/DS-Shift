from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import signal
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time
from urllib.parse import quote, urlparse
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

import httpx
import paramiko
from pyVim.connect import Disconnect, SmartConnect
from pyVmomi import vim


VMWARE_TYPES = {"VMware ESXi / vCenter", "VMware ESXi", "vCenter"}
STAGING_ROOT = Path(os.getenv("DS_SHIFT_STAGING_ROOT", "/DS-Shift-Staging"))
LAUNCHGRID_URL = os.getenv("LAUNCHGRID_URL", "http://launchgrid:8300").rstrip("/")
PREFLIGHT_PHASES = (
    "source_reachability",
    "destination_reachability",
    "destination_connector_data",
    "blockers",
)
_register_child_process = None
_cancel_requested = None


def set_execution_hooks(register_child_process=None, cancel_requested=None) -> None:
    global _register_child_process, _cancel_requested
    _register_child_process = register_child_process
    _cancel_requested = cancel_requested


def credential_value(reference: str | None) -> str | None:
    if not reference or not reference.startswith("env:"):
        return None
    return os.getenv(reference.split(":", 1)[1])


def connector_password(connector) -> str | None:
    if getattr(connector, "credential_payload", None) and connector.credential_payload.get("password"):
        return str(connector.credential_payload["password"])
    return credential_value(connector.credential_reference)


def ssh_parts(connector) -> tuple[str, int, str]:
    parsed = urlparse(connector.endpoint or "")
    if parsed.scheme.startswith("qemu+ssh"):
        return parsed.hostname or "", parsed.port or connector.port or 22, parsed.username or connector.username or "root"
    host_port = (connector.endpoint or "").replace("https://", "").replace("http://", "").split("/")[0]
    host = host_port.rsplit(":", 1)[0] if host_port.rsplit(":", 1)[-1].isdigit() and ":" in host_port else host_port
    return host, connector.port or 22, connector.username or "root"


@contextmanager
def ssh_client(connector):
    host, port, username = ssh_parts(connector)
    if not host:
        raise RuntimeError(f"{connector.name} does not contain a usable host")
    password = connector_password(connector)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        port=port,
        username=username,
        password=password,
        look_for_keys=not bool(password),
        allow_agent=not bool(password),
        timeout=15,
        banner_timeout=15,
        auth_timeout=15,
    )
    try:
        yield client
    finally:
        client.close()


def ssh_exec(client: paramiko.SSHClient, command: str, timeout: int = 60) -> tuple[str, str]:
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    code = stdout.channel.recv_exit_status()
    output = stdout.read().decode(errors="replace")
    error = stderr.read().decode(errors="replace")
    if code:
        raise RuntimeError(error.strip() or output.strip() or f"Remote command exited with status {code}")
    return output, error


def run(command: list[str], *, env: dict | None = None, timeout: int = 7200, pass_fds: tuple[int, ...] = ()) -> str:
    if _cancel_requested and _cancel_requested():
        raise RuntimeError("Execution cancelled by operator")
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        pass_fds=pass_fds,
        start_new_session=True,
    )
    if _register_child_process:
        _register_child_process(process.pid)
    started = time.monotonic()
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    while True:
        if _cancel_requested and _cancel_requested():
            try:
                os.killpg(process.pid, signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=10)
                stdout_chunks.append(stdout or "")
                stderr_chunks.append(stderr or "")
            except Exception:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except Exception:
                    pass
                try:
                    stdout, stderr = process.communicate(timeout=10)
                    stdout_chunks.append(stdout or "")
                    stderr_chunks.append(stderr or "")
                except Exception:
                    pass
            raise RuntimeError("Execution cancelled by operator")
        if timeout and time.monotonic() - started > timeout:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=10)
                stdout_chunks.append(stdout or "")
                stderr_chunks.append(stderr or "")
            except Exception:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except Exception:
                    pass
                try:
                    stdout, stderr = process.communicate(timeout=10)
                    stdout_chunks.append(stdout or "")
                    stderr_chunks.append(stderr or "")
                except Exception:
                    pass
            raise RuntimeError(f"{command[0]} timed out after {timeout} seconds")
        try:
            stdout, stderr = process.communicate(timeout=0.5)
            stdout_chunks.append(stdout or "")
            stderr_chunks.append(stderr or "")
            code = process.returncode
            if code:
                combined_stderr = "".join(stderr_chunks).strip()
                combined_stdout = "".join(stdout_chunks).strip()
                raise RuntimeError(combined_stderr or combined_stdout or f"{command[0]} exited with status {code}")
            return "".join(stdout_chunks)
        except subprocess.TimeoutExpired:
            continue


def virt_v2v_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("LIBGUESTFS_BACKEND", "direct")
    env.setdefault("LIBGUESTFS_CACHEDIR", "/var/tmp")
    env.setdefault("LIBGUESTFS_DEBUG", "1")
    env.setdefault("LIBGUESTFS_TRACE", "1")
    return env


def ensure_libguestfs_ready(timeout: int = 300) -> None:
    tool = shutil.which("libguestfs-test-tool")
    if not tool:
        raise RuntimeError("libguestfs-test-tool is not installed in the Spark Engine container")
    run([tool], env=virt_v2v_env(), timeout=timeout)


def libguestfs_runtime_summary() -> str:
    required = {
        "virt-v2v": shutil.which("virt-v2v"),
        "libguestfs-test-tool": shutil.which("libguestfs-test-tool"),
        "qemu-system-x86_64": shutil.which("qemu-system-x86_64"),
        "supermin": shutil.which("supermin"),
    }
    missing = [name for name, path in required.items() if not path]
    if missing:
        raise RuntimeError(f"Missing runtime dependency: {', '.join(missing)}")
    return ", ".join(f"{name}={path}" for name, path in required.items())


@contextmanager
def transient_secret_descriptor(secret: str, name: str = "ds-shift-secret"):
    fd = os.memfd_create(name)
    try:
        os.write(fd, secret.encode())
        os.lseek(fd, 0, os.SEEK_SET)
        yield f"/proc/self/fd/{fd}", fd
    finally:
        os.close(fd)


def normalize_kvm_interfaces(root: ET.ElementTree, target_bridge: str | None) -> int:
    if not target_bridge:
        return 0
    rewired = 0
    for interface in root.findall("./devices/interface"):
        interface.set("type", "bridge")
        for child in list(interface):
            if child.tag in {"source", "virtualport", "filterref", "backenddomain"}:
                interface.remove(child)
        ET.SubElement(interface, "source", {"bridge": target_bridge})
        model = interface.find("model")
        if model is None:
            ET.SubElement(interface, "model", {"type": "virtio"})
        rewired += 1
    return rewired


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.")
    if not cleaned:
        raise RuntimeError("Target VM name contains no usable characters")
    return cleaned[:80]


def shifted_artifact_base_name(target_name: str) -> str:
    if target_name.endswith("-migrated"):
        return f"{target_name[:-9]}-shifted"
    if target_name.endswith("-shifted"):
        return target_name
    return f"{target_name}-shifted"


def shifted_target_name(vm_name: str, override: str | None = None) -> str:
    if override:
        return safe_name(override)
    return safe_name(f"{vm_name}-shifted")


def stage_plan_directory_name(plan_id: int, plan_name: str | None = None) -> str:
    if plan_name:
        return f"Plan-{safe_name(plan_name)}"
    return f"plan-{plan_id}"


def parse_domain_xml(xml_text: str) -> dict:
    root = ET.fromstring(xml_text)
    memory = int(root.findtext("memory", "1048576"))
    unit = (root.find("memory").get("unit", "KiB") if root.find("memory") is not None else "KiB").lower()
    multipliers = {"b": 1, "bytes": 1, "kib": 1024, "mib": 1024**2, "gib": 1024**3}
    memory_bytes = memory * multipliers.get(unit, 1024)
    disks = []
    for disk in root.findall("./devices/disk[@device='disk']"):
        source = disk.find("source")
        target = disk.find("target")
        path = source.get("file") if source is not None else None
        if path:
            disks.append({"path": path, "target": target.get("dev", f"sd{chr(97 + len(disks))}") if target is not None else f"sd{chr(97 + len(disks))}"})
    os_node = root.find("./os")
    type_node = os_node.find("./type") if os_node is not None else None
    loader = os_node.find("./loader") if os_node is not None else None
    firmware_attr = (type_node.get("firmware") or "").lower() if type_node is not None else ""
    loader_type = (loader.get("type") or "").lower() if loader is not None else ""
    loader_path = (loader.text or "").lower() if loader is not None and loader.text else ""
    firmware = "efi" if firmware_attr in {"efi", "uefi"} or loader_type == "pflash" or "ovmf" in loader_path else "bios"
    return {
        "cpu": int(root.findtext("vcpu", "1")),
        "memory_bytes": memory_bytes,
        "disks": disks,
        "interfaces": len(root.findall("./devices/interface")),
        "boot_firmware": firmware,
    }


def ovf_descriptor(vm_name: str, cpu: int, memory_bytes: int, disks: list[dict], interfaces: int) -> str:
    file_rows = []
    disk_rows = []
    device_rows = []
    for index, disk in enumerate(disks, start=1):
        file_rows.append(
            f'<File ovf:href="{disk["name"]}" ovf:id="file{index}" ovf:size="{disk["size"]}"/>'
        )
        disk_rows.append(
            f'<Disk ovf:capacity="{disk["capacity"]}" ovf:capacityAllocationUnits="byte" '
            f'ovf:diskId="disk{index}" ovf:fileRef="file{index}" ovf:format="http://www.vmware.com/interfaces/specifications/vmdk.html#streamOptimized"/>'
        )
        device_rows.append(
            f"""<Item>
        <rasd:AddressOnParent>{index - 1}</rasd:AddressOnParent>
        <rasd:ElementName>Hard disk {index}</rasd:ElementName>
        <rasd:HostResource>ovf:/disk/disk{index}</rasd:HostResource>
        <rasd:InstanceID>{10 + index}</rasd:InstanceID>
        <rasd:Parent>10</rasd:Parent>
        <rasd:ResourceType>17</rasd:ResourceType>
      </Item>"""
        )
    network_section = '<Network ovf:name="VM Network"><Description>VM network</Description></Network>' if interfaces else ""
    nic_item = """<Item>
        <rasd:AddressOnParent>7</rasd:AddressOnParent>
        <rasd:AutomaticAllocation>true</rasd:AutomaticAllocation>
        <rasd:Connection>VM Network</rasd:Connection>
        <rasd:ElementName>Network adapter 1</rasd:ElementName>
        <rasd:InstanceID>20</rasd:InstanceID>
        <rasd:ResourceSubType>VmxNet3</rasd:ResourceSubType>
        <rasd:ResourceType>10</rasd:ResourceType>
      </Item>""" if interfaces else ""
    memory_mb = max(1, memory_bytes // 1024 // 1024)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Envelope xmlns="http://schemas.dmtf.org/ovf/envelope/1"
 xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1"
 xmlns:rasd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData"
 xmlns:vssd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_VirtualSystemSettingData"
 xmlns:vmw="http://www.vmware.com/schema/ovf">
  <References>{''.join(file_rows)}</References>
  <DiskSection><Info>Virtual disk information</Info>{''.join(disk_rows)}</DiskSection>
  <NetworkSection><Info>Logical networks</Info>{network_section}</NetworkSection>
  <VirtualSystem ovf:id="{vm_name}">
    <Info>DS Shift migrated virtual machine</Info>
    <Name>{vm_name}</Name>
    <OperatingSystemSection ovf:id="101" vmw:osType="otherGuest64"><Info>Guest operating system</Info></OperatingSystemSection>
    <VirtualHardwareSection>
      <Info>Virtual hardware requirements</Info>
      <System>
        <vssd:ElementName>Virtual Hardware Family</vssd:ElementName>
        <vssd:InstanceID>0</vssd:InstanceID>
        <vssd:VirtualSystemIdentifier>{vm_name}</vssd:VirtualSystemIdentifier>
        <vssd:VirtualSystemType>vmx-13</vssd:VirtualSystemType>
      </System>
      <Item><rasd:AllocationUnits>hertz * 10^6</rasd:AllocationUnits><rasd:ElementName>{cpu} virtual CPU(s)</rasd:ElementName><rasd:InstanceID>1</rasd:InstanceID><rasd:ResourceType>3</rasd:ResourceType><rasd:VirtualQuantity>{cpu}</rasd:VirtualQuantity></Item>
      <Item><rasd:AllocationUnits>byte * 2^20</rasd:AllocationUnits><rasd:ElementName>{memory_mb} MB of memory</rasd:ElementName><rasd:InstanceID>2</rasd:InstanceID><rasd:ResourceType>4</rasd:ResourceType><rasd:VirtualQuantity>{memory_mb}</rasd:VirtualQuantity></Item>
      <Item><rasd:Address>0</rasd:Address><rasd:ElementName>SCSI controller 0</rasd:ElementName><rasd:InstanceID>10</rasd:InstanceID><rasd:ResourceSubType>VirtualLsiLogicSAS</rasd:ResourceSubType><rasd:ResourceType>6</rasd:ResourceType></Item>
      {''.join(device_rows)}
      {nic_item}
    </VirtualHardwareSection>
  </VirtualSystem>
</Envelope>
"""


def govc_environment(connector, options: dict) -> dict:
    password = connector_password(connector)
    if not connector.endpoint or not connector.username or not password:
        raise RuntimeError("vCenter connector requires endpoint, username, and an available env: password reference")
    parsed = urlparse(connector.endpoint if "://" in connector.endpoint else f"https://{connector.endpoint}")
    env = os.environ.copy()
    env.update(
        {
            "GOVC_URL": f"https://{parsed.hostname}:{parsed.port or connector.port or 443}",
            "GOVC_USERNAME": connector.username,
            "GOVC_PASSWORD": password,
            "GOVC_INSECURE": "1" if options.get("insecure", True) else "0",
        }
    )
    enriched_options = {
        **({"target_datacenter": connector.target_vdc_name} if getattr(connector, "target_vdc_name", None) else {}),
        **({"target_datastore": connector.target_datastore} if getattr(connector, "target_datastore", None) else {}),
        **({"target_network": connector.target_network} if getattr(connector, "target_network", None) else {}),
        **options,
    }
    mappings = {
        "target_datacenter": "GOVC_DATACENTER",
        "target_datastore": "GOVC_DATASTORE",
        "target_network": "GOVC_NETWORK",
        "target_resource_pool": "GOVC_RESOURCE_POOL",
        "target_folder": "GOVC_FOLDER",
    }
    for option, variable in mappings.items():
        if enriched_options.get(option):
            env[variable] = str(enriched_options[option])
    return env


def ensure_staging_root() -> Path:
    STAGING_ROOT.mkdir(parents=True, exist_ok=True)
    if not STAGING_ROOT.is_dir():
        raise RuntimeError(f"Staging path {STAGING_ROOT} is not a directory")
    if not os.access(STAGING_ROOT, os.W_OK):
        raise RuntimeError(f"Staging path {STAGING_ROOT} is not writable")
    return STAGING_ROOT


def stage_directory(plan_id: int, workload_id: int, vm_name: str, plan_name: str | None = None) -> Path:
    base = ensure_staging_root() / stage_plan_directory_name(plan_id, plan_name) / safe_name(vm_name)
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True, exist_ok=True)
    return base


def preserved_stage_directory(plan_id: int, workload_id: int, vm_name: str, plan_name: str | None = None) -> Path:
    base = ensure_staging_root() / stage_plan_directory_name(plan_id, plan_name) / safe_name(vm_name)
    base.mkdir(parents=True, exist_ok=True)
    return base


def candidate_stage_directories(plan_id: int, workload_id: int, vm_name: str, plan_name: str | None = None) -> list[Path]:
    preferred = ensure_staging_root() / stage_plan_directory_name(plan_id, plan_name) / safe_name(vm_name)
    legacy = ensure_staging_root() / stage_plan_directory_name(plan_id, None) / f"vm-{workload_id}-{safe_name(vm_name)}"
    paths: list[Path] = []
    seen: set[str] = set()
    for path in [preferred, legacy]:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths


def temporary_stage_directory(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(ensure_staging_root())))


def find_compute_path(env: dict, compute_name: str) -> tuple[str, str]:
    host_path = run(["govc", "find", "-type", "h", "-name", compute_name], env=env, timeout=30).strip()
    if host_path:
        return "host", host_path.splitlines()[0].strip()
    cluster_path = run(["govc", "find", "-type", "c", "-name", compute_name], env=env, timeout=30).strip()
    if cluster_path:
        return "cluster", cluster_path.splitlines()[0].strip()
    raise RuntimeError(f"Target cluster or host {compute_name} was not found")


def launchgrid_provision(request, workload, target_name: str, metadata: dict, disks: list[dict]) -> dict:
    payload = {
        "target_connector": {
            "id": request.target_connector.id,
            "name": request.target_connector.name,
            "connector_category": request.target_connector.connector_category,
            "connector_type": request.target_connector.connector_type,
            "endpoint": request.target_connector.endpoint,
            "port": request.target_connector.port,
            "username": request.target_connector.username,
            "target_network": request.options.get("target_network") or request.target_connector.target_network,
            "target_datastore": request.target_connector.target_datastore,
            "target_storage_pool": request.target_connector.target_storage_pool,
            "target_vdc_name": request.options.get("target_datacenter") or request.options.get("target_vdc_name") or request.target_connector.target_vdc_name,
            "target_compute_name": request.options.get("target_compute_name") or request.target_connector.target_compute_name,
            "credential_reference": request.target_connector.credential_reference,
            "credential_payload": getattr(request.target_connector, "credential_payload", {}) or {},
        },
        "vm_name": target_name,
        "cpu": metadata["cpu"],
        "memory_bytes": metadata["memory_bytes"],
        "disks": [
            {"local_path": disk["local_path"], "capacity_bytes": disk["capacity_bytes"]}
            for disk in disks
        ],
        "power_on": bool(request.options.get("power_on")),
        "autostart": bool(request.options.get("autostart")),
        "guest_os_id": workload.details.get("guest_os_id") or "otherGuest64",
        "boot_firmware": workload.details.get("boot_firmware") or metadata.get("boot_firmware") or "bios",
    }
    if metadata.get("libvirt_xml_path"):
        payload["libvirt_xml_path"] = metadata["libvirt_xml_path"]
    timeout = httpx.Timeout(connect=15.0, write=7200.0, read=7200.0, pool=30.0)
    with httpx.Client(timeout=timeout) as client:
        response = client.post(f"{LAUNCHGRID_URL}/provision", json=payload)
    if response.status_code >= 400:
        detail = response.text.strip()
        try:
            detail = response.json().get("detail", detail)
        except Exception:
            pass
        raise RuntimeError(f"LaunchGrid provisioning failed: {detail}")
    return response.json()


def source_vcenter_environment(connector, workload) -> dict[str, str]:
    password = connector_password(connector)
    parsed = urlparse(connector.endpoint if "://" in (connector.endpoint or "") else f"https://{connector.endpoint}")
    if not parsed.hostname or not connector.username or not password:
        raise RuntimeError("vCenter connector credentials are unavailable")
    env = os.environ.copy()
    env.update(
        {
            "GOVC_URL": f"https://{parsed.hostname}:{parsed.port or connector.port or 443}",
            "GOVC_USERNAME": connector.username,
            "GOVC_PASSWORD": password,
            "GOVC_INSECURE": "1",
        }
    )
    datacenter = workload.details.get("datacenter")
    if datacenter:
        env["GOVC_DATACENTER"] = str(datacenter)
    return env


def parse_vmware_datastore_path(path: str) -> tuple[str, str]:
    match = re.match(r"^\[(?P<datastore>[^\]]+)\]\s+(?P<relative>.+)$", (path or "").strip())
    if not match:
        raise RuntimeError(f"Unsupported VMware datastore path: {path}")
    return match.group("datastore").strip(), match.group("relative").strip()


def vcenter_vm_disk_layout(connector, workload) -> list[dict]:
    env = source_vcenter_environment(connector, workload)
    payload = json.loads(run(["govc", "device.info", "-json", "-vm", workload.vm_name, "disk-*"], env=env, timeout=60))
    layout = []
    for index, device in enumerate(payload.get("devices", []), start=1):
        backing = device.get("backing") or {}
        backing_file = backing.get("fileName")
        if not backing_file:
            continue
        datastore, relative_path = parse_vmware_datastore_path(backing_file)
        descriptor_name = Path(relative_path).name
        if descriptor_name.lower().endswith(".vmdk"):
            flat_name = f"{descriptor_name[:-5]}-flat.vmdk"
        else:
            flat_name = f"{descriptor_name}-flat.vmdk"
        folder = str(Path(relative_path).parent).replace("\\", "/")
        if folder == ".":
            folder = ""
        flat_relative_path = f"{folder}/{flat_name}".lstrip("/")
        layout.append(
            {
                "index": index,
                "datastore": datastore,
                "descriptor_path": relative_path,
                "descriptor_name": descriptor_name,
                "flat_path": flat_relative_path,
                "flat_name": flat_name,
                "capacity_bytes": int(device.get("capacityInBytes") or 0),
            }
        )
    if not layout:
        raise RuntimeError(f"{workload.vm_name} does not expose downloadable VMDK backing files")
    return layout


def render_kvm_domain_xml(vm_name: str, cpu: int, memory_bytes: int, disks: list[dict], boot_firmware: str) -> str:
    loader = ""
    firmware_attr = ""
    if str(boot_firmware).lower() in {"efi", "uefi"}:
        loader = "\n    <loader readonly='yes' type='pflash'>/usr/share/OVMF/OVMF_CODE.fd</loader>"
        firmware_attr = " firmware='efi'"
    disk_rows = []
    for index, disk in enumerate(disks):
        target_dev = f"vd{chr(97 + index)}"
        disk_rows.append(
            f"""    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='{escape(disk["local_path"])}'/>
      <target dev='{target_dev}' bus='virtio'/>
    </disk>"""
        )
    memory_kib = max(262144, int(memory_bytes // 1024))
    return f"""<domain type='kvm'>
  <name>{escape(vm_name)}</name>
  <memory unit='KiB'>{memory_kib}</memory>
  <currentMemory unit='KiB'>{memory_kib}</currentMemory>
  <vcpu>{max(1, int(cpu))}</vcpu>
  <os>
    <type arch='x86_64' machine='pc-q35-8.2'{firmware_attr}>hvm</type>{loader}
    <boot dev='hd'/>
  </os>
  <features>
    <acpi/>
    <apic/>
  </features>
  <cpu mode='host-model'/>
  <devices>
{''.join(disk_rows)}
    <interface type='bridge'>
      <source bridge='placeholder'/>
      <model type='virtio'/>
    </interface>
    <graphics type='vnc' autoport='yes' listen='0.0.0.0'/>
    <console type='pty'/>
  </devices>
</domain>
"""


def converted_vmdk_layout(stage_path: Path, target_name: str, disks: list[dict]) -> list[dict]:
    layout = []
    artifact_base = shifted_artifact_base_name(target_name)
    for index, disk in enumerate(disks, start=1):
        local_path = stage_path / f"{artifact_base}-disk{index}.vmdk"
        capacity = disk.get("capacity_bytes")
        if capacity is None and local_path.exists():
            try:
                capacity = int(json.loads(run(["qemu-img", "info", "--output=json", str(local_path)]))["virtual-size"])
            except Exception:
                capacity = local_path.stat().st_size
        compatible = False
        if local_path.exists():
            try:
                descriptor = local_path.read_bytes()[:65536].decode("latin-1", errors="ignore")
                version_match = re.search(r'ddb\.virtualHWVersion\s*=\s*"(\d+)"', descriptor)
                create_type_match = re.search(r'createType="([^"]+)"', descriptor)
                hardware_version = int(version_match.group(1)) if version_match else 0
                create_type = create_type_match.group(1) if create_type_match else ""
                compatible = hardware_version >= 13 and create_type == "streamOptimized"
            except Exception:
                compatible = False
        layout.append(
            {
                "local_path": str(local_path),
                "capacity_bytes": int(capacity or 0),
                "exists": local_path.exists(),
                "compatible": compatible,
            }
        )
    return layout


def stage_failure_result(workload, target_name: str, stage_path: Path, message: str, *, can_resume: bool) -> dict:
    return {
        "ok": False,
        "vm_id": workload.id,
        "vm_name": workload.vm_name,
        "target_name": target_name,
        "message": message,
        "stage_path": str(stage_path),
        "can_resume": can_resume,
        "resume_hint": "Use Continue after correcting the blocking issue to reuse the preserved staged artifacts" if can_resume else "Relaunch will rebuild staging because reusable converted artifacts are not available",
    }


def append_migrated_vm_log(plan_id: int, plan_name: str | None, workload, target_name: str, migration_type: str) -> None:
    ensure_staging_root()
    log_path = STAGING_ROOT / "migrated-vms.log"
    timestamp = datetime.utcnow().isoformat() + "Z"
    plan_label = plan_name or f"plan-{plan_id}"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(
            f"{timestamp} | plan={plan_label} | vm_id={workload.id} | vm={workload.vm_name} | target={target_name} | migration={migration_type}\n"
        )


def cleanup_plan_stage_directory(plan_id: int, plan_name: str | None) -> None:
    shutil.rmtree(ensure_staging_root() / stage_plan_directory_name(plan_id, plan_name), ignore_errors=True)


def remove_source_kvm_vm(client: paramiko.SSHClient, vm_name: str) -> None:
    quoted = shlex.quote(vm_name)
    try:
        ssh_exec(client, f"virsh undefine --remove-all-storage {quoted}", timeout=180)
        return
    except Exception as primary_error:
        try:
            ssh_exec(client, f"virsh undefine --nvram --managed-save --snapshots-metadata --checkpoints-metadata --remove-all-storage {quoted}", timeout=180)
            return
        except Exception:
            raise RuntimeError(f"Provisioning completed, but source cleanup failed for {vm_name}: {primary_error}") from primary_error


def connect_vcenter(connector):
    password = connector_password(connector)
    parsed = urlparse(connector.endpoint if "://" in (connector.endpoint or "") else f"https://{connector.endpoint}")
    if not parsed.hostname or not connector.username or not password:
        raise RuntimeError("vCenter connector credentials are unavailable")
    return SmartConnect(
        host=parsed.hostname,
        port=parsed.port or connector.port or 443,
        user=connector.username,
        pwd=password,
        disableSslCertValidation=True,
        connectionPoolTimeout=30,
    )


def iter_vcenter_vms(service_instance):
    content = service_instance.RetrieveContent()
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
    try:
        for vm_obj in view.view:
            yield vm_obj
    finally:
        view.Destroy()


def vcenter_vm_matches(vm_obj, workload) -> bool:
    external_id = getattr(workload, "external_id", None)
    return (external_id and getattr(vm_obj, "_moId", None) == external_id) or vm_obj.name == workload.vm_name


def wait_for_vcenter_task(task, timeout: int = 600) -> None:
    started = time.monotonic()
    while task.info.state not in {vim.TaskInfo.State.success, vim.TaskInfo.State.error}:
        if timeout and time.monotonic() - started > timeout:
            raise RuntimeError(f"vCenter task timed out after {timeout} seconds")
        time.sleep(2)
    if task.info.state == vim.TaskInfo.State.error:
        error = getattr(task.info, "error", None)
        raise RuntimeError(getattr(error, "msg", None) or str(error) or "vCenter task failed")


def remove_source_vcenter_vm(connector, workload) -> None:
    service_instance = connect_vcenter(connector)
    try:
        for vm_obj in iter_vcenter_vms(service_instance):
            if not vcenter_vm_matches(vm_obj, workload):
                continue
            power_state = str(vm_obj.runtime.powerState).lower()
            if power_state != "poweredoff":
                raise RuntimeError(f"Provisioning completed, but source cleanup failed for {vm_obj.name}: VM is {vm_obj.runtime.powerState}")
            wait_for_vcenter_task(vm_obj.Destroy_Task(), timeout=600)
            return
    finally:
        Disconnect(service_instance)
    raise RuntimeError(f"Provisioning completed, but source cleanup failed: VM {workload.vm_name} was not found in vCenter")


def find_vcenter_vm(connector, workload):
    service_instance = connect_vcenter(connector)
    try:
        for vm_obj in iter_vcenter_vms(service_instance):
            if vcenter_vm_matches(vm_obj, workload):
                memory_mb = int(getattr(vm_obj.config.hardware, "memoryMB", 1024) or 1024) if getattr(vm_obj, "config", None) and getattr(vm_obj.config, "hardware", None) else 1024
                return {
                    "name": vm_obj.name,
                    "power_state": str(vm_obj.runtime.powerState),
                    "host_name": vm_obj.runtime.host.name if vm_obj.runtime.host else None,
                    "cpu": int(getattr(vm_obj.config.hardware, "numCPU", 1) or 1) if getattr(vm_obj, "config", None) and getattr(vm_obj.config, "hardware", None) else 1,
                    "memory_bytes": memory_mb * 1024 * 1024,
                    "guest_os_id": getattr(vm_obj.config, "guestId", None) if getattr(vm_obj, "config", None) else None,
                    "boot_firmware": getattr(vm_obj.config, "firmware", None) if getattr(vm_obj, "config", None) else None,
                }
    finally:
        Disconnect(service_instance)
    raise RuntimeError(f"VM {workload.vm_name} was not found in vCenter")


def _kvm_to_vcenter_source_checks(request) -> list[dict]:
    checks = []
    try:
        with ssh_client(request.source_connector) as client:
            for workload in request.workloads:
                state, _ = ssh_exec(client, f"virsh domstate {shlex.quote(workload.vm_name)}", timeout=20)
                stopped = state.strip().lower() in {"shut off", "shutoff"}
                checks.append({"phase": "source_reachability", "check": "source_vm_state", "vm_name": workload.vm_name, "ok": stopped, "message": state.strip()})
                xml_text, _ = ssh_exec(client, f"virsh dumpxml {shlex.quote(workload.vm_name)}", timeout=20)
                disks = parse_domain_xml(xml_text)["disks"]
                checks.append({"phase": "source_reachability", "check": "source_vm_disks", "vm_name": workload.vm_name, "ok": bool(disks), "message": f"{len(disks)} disk(s) found"})
    except Exception as exc:
        checks.append({"phase": "source_reachability", "check": "source_kvm", "ok": False, "message": str(exc)})
    return checks


def _kvm_to_vcenter_destination_reachability_checks(request) -> list[dict]:
    checks = []
    try:
        env = govc_environment(request.target_connector, request.options)
        about = json.loads(run(["govc", "about", "-json"], env=env, timeout=30))
        checks.append({"phase": "destination_reachability", "check": "target_vcenter", "ok": True, "message": about.get("About", {}).get("FullName", "vCenter reachable")})
    except Exception as exc:
        checks.append({"phase": "destination_reachability", "check": "target_vcenter", "ok": False, "message": str(exc)})
    return checks


def _kvm_to_vcenter_destination_connector_checks(request) -> list[dict]:
    target_datastore = request.target_connector.target_datastore
    target_network = request.options.get("target_network") or request.target_connector.target_network
    target_vdc_name = request.options.get("target_datacenter") or request.options.get("target_vdc_name") or request.target_connector.target_vdc_name
    target_compute_name = request.options.get("target_compute_name") or request.target_connector.target_compute_name
    checks = []
    try:
        env = govc_environment(request.target_connector, request.options)
        if not target_datastore:
            raise RuntimeError("Target datastore is not configured on the target connector")
        run(["govc", "datastore.info", target_datastore], env=env, timeout=30)
        checks.append({"phase": "destination_connector_data", "check": "target_datastore", "ok": True, "message": target_datastore})
        if not target_network:
            raise RuntimeError("Target network is not configured on the target connector")
        network = run(["govc", "find", "-type", "n", "-name", target_network], env=env, timeout=30).strip()
        if not network:
            raise RuntimeError(f"Target network {target_network} was not found")
        checks.append({"phase": "destination_connector_data", "check": "target_network", "ok": True, "message": target_network})
        if target_vdc_name:
            datacenter = run(["govc", "find", "-type", "d", "-name", target_vdc_name], env=env, timeout=30).strip()
            if not datacenter:
                raise RuntimeError(f"Target vDC name {target_vdc_name} was not found")
            checks.append({"phase": "destination_connector_data", "check": "target_vdc_name", "ok": True, "message": target_vdc_name})
        else:
            raise RuntimeError("Target vDC name is not configured on the target connector")
        if not target_compute_name:
            raise RuntimeError("Target Cluster Name or Host Name is not configured on the target connector")
        compute_kind, compute_path = find_compute_path(env, target_compute_name)
        checks.append({"phase": "destination_connector_data", "check": "target_compute_name", "ok": True, "message": f"{target_compute_name} ({compute_kind}: {compute_path})"})
    except Exception as exc:
        checks.append({"phase": "destination_connector_data", "check": "target_vcenter", "ok": False, "message": str(exc)})
    return checks


def _kvm_to_vcenter_blocker_checks(_request) -> list[dict]:
    checks = [
        {"phase": "blockers", "check": "qemu_img", "ok": bool(shutil.which("qemu-img")), "message": shutil.which("qemu-img") or "qemu-img is not installed"},
        {"phase": "blockers", "check": "govc", "ok": bool(shutil.which("govc")), "message": shutil.which("govc") or "govc is not installed"},
    ]
    try:
        staging_root = ensure_staging_root()
        checks.append({"phase": "blockers", "check": "staging_path", "ok": True, "message": str(staging_root)})
    except Exception as exc:
        checks.append({"phase": "blockers", "check": "staging_path", "ok": False, "message": str(exc)})
    return checks


def preflight_kvm_to_vcenter(request, phase: str | None = None) -> list[dict]:
    phase = phase or ""
    if phase == "source_reachability":
        return _kvm_to_vcenter_source_checks(request)
    if phase == "destination_reachability":
        return _kvm_to_vcenter_destination_reachability_checks(request)
    if phase == "destination_connector_data":
        return _kvm_to_vcenter_destination_connector_checks(request)
    if phase == "blockers":
        return _kvm_to_vcenter_blocker_checks(request)
    checks = []
    for current in PREFLIGHT_PHASES:
        checks.extend(preflight_kvm_to_vcenter(request, current))
    return checks


def _vcenter_to_kvm_source_checks(request) -> list[dict]:
    checks = []
    try:
        for workload in request.workloads:
            vm_info = find_vcenter_vm(request.source_connector, workload)
            stopped = vm_info["power_state"].lower() == "poweredoff"
            checks.append({"phase": "source_reachability", "check": "source_vm_state", "vm_name": workload.vm_name, "ok": stopped, "message": vm_info["power_state"]})
            checks.append({"phase": "source_reachability", "check": "source_vcenter", "vm_name": workload.vm_name, "ok": True, "message": f"Found VM on {request.source_connector.name}"})
            disks = vcenter_vm_disk_layout(request.source_connector, workload)
            checks.append({"phase": "source_reachability", "check": "source_vm_disks", "vm_name": workload.vm_name, "ok": bool(disks), "message": f"{len(disks)} downloadable VMware disk(s) found"})
    except Exception as exc:
        checks.append({"phase": "source_reachability", "check": "source_vcenter", "ok": False, "message": str(exc)})
    return checks


def _vcenter_to_kvm_destination_reachability_checks(request) -> list[dict]:
    checks = []
    try:
        with ssh_client(request.target_connector) as client:
            host_name, _ = ssh_exec(client, "hostnamectl --static || hostname", timeout=20)
            checks.append({"phase": "destination_reachability", "check": "target_kvm", "ok": True, "message": host_name.strip() or request.target_connector.name})
    except Exception as exc:
        checks.append({"phase": "destination_reachability", "check": "target_kvm", "ok": False, "message": str(exc)})
    return checks


def _vcenter_to_kvm_destination_connector_checks(request) -> list[dict]:
    target_storage_pool = request.target_connector.target_storage_pool or request.options.get("target_storage_pool")
    target_network = request.target_connector.target_network or request.options.get("target_network")
    checks = []
    try:
        if not target_storage_pool:
            raise RuntimeError("Target KVM storage pool is not configured on the target connector")
        with ssh_client(request.target_connector) as client:
            pool_xml, _ = ssh_exec(client, f"virsh pool-dumpxml {shlex.quote(target_storage_pool)}", timeout=20)
            pool_path = ET.fromstring(pool_xml).findtext("./target/path")
            if not pool_path:
                raise RuntimeError("Target storage pool has no filesystem path")
            ssh_exec(client, f"test -d {shlex.quote(pool_path)} && test -w {shlex.quote(pool_path)}", timeout=20)
            checks.append({"phase": "destination_connector_data", "check": "target_kvm_pool", "ok": True, "message": f"{target_storage_pool}: {pool_path}"})
            if target_network:
                ssh_exec(client, f"ip link show {shlex.quote(target_network)}", timeout=20)
                checks.append({"phase": "destination_connector_data", "check": "target_kvm_network", "ok": True, "message": target_network})
    except Exception as exc:
        checks.append({"phase": "destination_connector_data", "check": "target_kvm", "ok": False, "message": str(exc)})
    return checks


def _vcenter_to_kvm_blocker_checks(_request) -> list[dict]:
    checks = [
        {"phase": "blockers", "check": "govc", "ok": bool(shutil.which("govc")), "message": shutil.which("govc") or "govc is not installed"},
        {"phase": "blockers", "check": "qemu_img", "ok": bool(shutil.which("qemu-img")), "message": shutil.which("qemu-img") or "qemu-img is not installed"},
    ]
    try:
        staging_root = ensure_staging_root()
        checks.append({"phase": "blockers", "check": "staging_path", "ok": True, "message": str(staging_root)})
    except Exception as exc:
        checks.append({"phase": "blockers", "check": "staging_path", "ok": False, "message": str(exc)})
    return checks


def preflight_vcenter_to_kvm(request, phase: str | None = None) -> list[dict]:
    phase = phase or ""
    if phase == "source_reachability":
        return _vcenter_to_kvm_source_checks(request)
    if phase == "destination_reachability":
        return _vcenter_to_kvm_destination_reachability_checks(request)
    if phase == "destination_connector_data":
        return _vcenter_to_kvm_destination_connector_checks(request)
    if phase == "blockers":
        return _vcenter_to_kvm_blocker_checks(request)
    checks = []
    for current in PREFLIGHT_PHASES:
        checks.extend(preflight_vcenter_to_kvm(request, current))
    return checks


def execute_kvm_to_vcenter(request, reporter=None) -> list[dict]:
    results = []
    with ssh_client(request.source_connector) as client:
        sftp = client.open_sftp()
        try:
            for workload in request.workloads:
                target_name = shifted_target_name(workload.vm_name, request.options.get("target_name"))
                stage_path = preserved_stage_directory(request.plan_id, workload.id, workload.vm_name, request.plan_name)
                converted_disks = []
                try:
                    resume_from_stage = bool(request.options.get("resume_from_stage"))
                    if reporter:
                        reporter.task(f"{workload.id}-inspect", f"{workload.vm_name}: inspect source VM", "Running", 15, f"Inspecting KVM source VM {workload.vm_name}")
                    state, _ = ssh_exec(client, f"virsh domstate {shlex.quote(workload.vm_name)}")
                    if state.strip().lower() not in {"shut off", "shutoff"}:
                        raise RuntimeError(f"{workload.vm_name} must be shut off before disk conversion")
                    xml_text, _ = ssh_exec(client, f"virsh dumpxml {shlex.quote(workload.vm_name)}")
                    metadata = parse_domain_xml(xml_text)
                    if not metadata["disks"]:
                        raise RuntimeError(f"{workload.vm_name} has no file-backed disks")
                    if reporter:
                        reporter.task(f"{workload.id}-inspect", f"{workload.vm_name}: inspect source VM", "Succeeded", 25, f"Found {len(metadata['disks'])} file-backed source disk(s)")
                    if resume_from_stage:
                        candidate_paths = candidate_stage_directories(request.plan_id, workload.id, workload.vm_name, request.plan_name)
                        stage_path = next((path for path in candidate_paths if path.exists()), candidate_paths[0])
                        stage_path.mkdir(parents=True, exist_ok=True)
                    else:
                        stage_path = stage_directory(request.plan_id, workload.id, workload.vm_name, request.plan_name)
                    reused_converted = False
                    if resume_from_stage:
                        candidate_disks = converted_vmdk_layout(stage_path, target_name, metadata["disks"])
                        if candidate_disks and all(disk["exists"] and disk.get("compatible") for disk in candidate_disks):
                            reused_converted = True
                            converted_disks = [{"local_path": disk["local_path"], "capacity_bytes": disk["capacity_bytes"]} for disk in candidate_disks]
                            if reporter:
                                reporter.task(
                                    f"{workload.id}-reuse",
                                    f"{workload.vm_name}: reuse staged conversion",
                                    "Completed",
                                    82,
                                    f"Reusing preserved converted disks from {stage_path}",
                                )
                        elif reporter:
                            incompatible = [Path(disk["local_path"]).name for disk in candidate_disks if disk.get("exists") and not disk.get("compatible")]
                            reason = (
                                f"Preserved converted disks are incompatible for VMware import ({', '.join(incompatible)}); rebuilding conversion before continuing"
                                if incompatible
                                else f"Preserved converted disks are missing from {stage_path}; rebuilding conversion before continuing"
                            )
                            reporter.task(
                                f"{workload.id}-reuse",
                                f"{workload.vm_name}: reuse staged conversion",
                                "Running",
                                28,
                                reason,
                            )
                    if not reused_converted:
                        artifact_base = shifted_artifact_base_name(target_name)
                        for index, disk in enumerate(metadata["disks"], start=1):
                            source_path = stage_path / f"source-{index}{Path(disk['path']).suffix or '.img'}"
                            target_path = stage_path / f"{artifact_base}-disk{index}.vmdk"
                            if resume_from_stage and source_path.exists():
                                if reporter:
                                    reporter.task(
                                        f"{workload.id}-stage-{index}",
                                        f"{workload.vm_name}: stage source disk {index}",
                                        "Completed",
                                        45,
                                        f"Reusing staged source disk {index} from {source_path}",
                                    )
                            else:
                                if reporter:
                                    reporter.task(
                                        f"{workload.id}-stage-{index}",
                                        f"{workload.vm_name}: stage source disk {index}",
                                        "Running",
                                        35,
                                        f"Copying {disk['path']} into host staging {stage_path}",
                                    )
                                sftp.get(disk["path"], str(source_path))
                                if reporter:
                                    reporter.task(
                                        f"{workload.id}-stage-{index}",
                                        f"{workload.vm_name}: stage source disk {index}",
                                        "Completed",
                                        45,
                                        f"Copied source disk {index} into host staging",
                                    )
                            info = json.loads(run(["qemu-img", "info", "--output=json", str(source_path)]))
                            if reporter:
                                reporter.task(
                                    f"{workload.id}-convert-{index}",
                                    f"{workload.vm_name}: convert disk {index}",
                                    "Running",
                                    60,
                                    f"Converting source disk {index} to stream-optimized VMDK in shared staging",
                                )
                            run(
                                [
                                    "qemu-img",
                                    "convert",
                                    "-p",
                                    "-O",
                                    "vmdk",
                                    "-o",
                                    "adapter_type=lsilogic,subformat=streamOptimized,hwversion=13",
                                    str(source_path),
                                    str(target_path),
                                ]
                            )
                            converted_disks.append({"local_path": str(target_path), "capacity_bytes": int(info["virtual-size"])})
                            if reporter:
                                reporter.task(
                                    f"{workload.id}-convert-{index}",
                                    f"{workload.vm_name}: convert disk {index}",
                                    "Completed",
                                    70,
                                    f"Converted source disk {index} to {target_path.name}",
                                )
                    if reporter:
                        reporter.task(f"{workload.id}-provision", f"{workload.vm_name}: provision target VM", "Running", 88, f"Sending converted disks from {stage_path} to LaunchGrid for VMware provisioning")
                    provisioned = launchgrid_provision(request, workload, target_name, metadata, converted_disks)
                    if reporter:
                        reporter.task(f"{workload.id}-provision", f"{workload.vm_name}: provision target VM", "Completed", 96, f"Provisioned {target_name} on VMware through LaunchGrid")
                    if not request.keep_source_vm:
                        if reporter:
                            reporter.task(f"{workload.id}-cleanup", f"{workload.vm_name}: remove source VM", "Running", 98, f"Removing source KVM VM {workload.vm_name} because Keep Source VM is disabled")
                        remove_source_kvm_vm(client, workload.vm_name)
                        if reporter:
                            reporter.task(f"{workload.id}-cleanup", f"{workload.vm_name}: remove source VM", "Completed", 100, f"Removed source KVM VM {workload.vm_name}")
                    results.append(
                        {
                            "ok": True,
                            "vm_id": workload.id,
                            "vm_name": workload.vm_name,
                            "target_name": target_name,
                            "message": f"Provisioned on VMware using LaunchGrid{'' if request.keep_source_vm else ' and removed the source KVM VM'}",
                            "stage_path": str(stage_path),
                            "can_resume": False,
                            "reused_staging": reused_converted,
                            "details": provisioned,
                        }
                    )
                except Exception as exc:
                    if reporter:
                        reporter.task(f"{workload.id}-provision", f"{workload.vm_name}: provision target VM", "Failed", 88, str(exc))
                    results.append(stage_failure_result(workload, target_name, stage_path, str(exc), can_resume=stage_path.exists()))
        finally:
            sftp.close()
    if results and all(result.get("ok") for result in results):
        for result, workload in zip(results, request.workloads):
            append_migrated_vm_log(request.plan_id, request.plan_name, workload, result.get("target_name") or workload.vm_name, request.target_connector.connector_type)
        cleanup_plan_stage_directory(request.plan_id, request.plan_name)
    return results


def vpx_uri(connector, workload, options: dict) -> str:
    parsed = urlparse(connector.endpoint if "://" in (connector.endpoint or "") else f"https://{connector.endpoint}")
    datacenter = options.get("source_datacenter") or workload.details.get("datacenter")
    compute = options.get("source_compute_resource") or workload.details.get("compute_resource")
    esxi_host = workload.host_name or workload.details.get("host_name")
    if not parsed.hostname or not connector.username or not datacenter or not esxi_host:
        raise RuntimeError(f"{workload.vm_name} requires vCenter host, username, datacenter, and ESXi host metadata")
    user = quote(connector.username, safe="")
    path = [datacenter]
    if compute and compute != esxi_host:
        path.append(compute)
    path.append(esxi_host)
    encoded_path = "/".join(quote(str(part), safe="") for part in path)
    return f"vpx://{user}@{parsed.hostname}:{parsed.port or connector.port or 443}/{encoded_path}?no_verify=1"


def execute_vcenter_to_kvm(request, reporter=None) -> list[dict]:
    results = []
    target_storage_pool = request.target_connector.target_storage_pool or request.options.get("target_storage_pool")
    target_network = request.target_connector.target_network or request.options.get("target_network")
    if not target_storage_pool:
        raise RuntimeError("Target KVM storage pool is not configured on the target connector")
    for workload in request.workloads:
        target_name = shifted_target_name(workload.vm_name, request.options.get("target_name"))
        stage_path = preserved_stage_directory(request.plan_id, workload.id, workload.vm_name, request.plan_name)
        try:
            resume_from_stage = bool(request.options.get("resume_from_stage"))
            if reporter:
                reporter.task(
                    f"{workload.id}-read-source",
                    f"{workload.vm_name}: read from vCenter",
                    "Running",
                    15,
                    f"Reading source VM {workload.vm_name} from vCenter and validating target name {target_name}",
                )
            vm_info = find_vcenter_vm(request.source_connector, workload)
            if vm_info["power_state"].lower() != "poweredoff":
                raise RuntimeError(f"{workload.vm_name} must be powered off before disk download and conversion")
            if reporter:
                reporter.task(
                    f"{workload.id}-read-source",
                    f"{workload.vm_name}: read from vCenter",
                    "Completed",
                    25,
                    f"Read source VM {workload.vm_name} from vCenter and validated target name {target_name}",
                )
            if resume_from_stage:
                candidate_paths = candidate_stage_directories(request.plan_id, workload.id, workload.vm_name, request.plan_name)
                stage_path = next((path for path in candidate_paths if path.exists()), candidate_paths[0])
                stage_path.mkdir(parents=True, exist_ok=True)
            else:
                stage_path = stage_directory(request.plan_id, workload.id, workload.vm_name, request.plan_name)
            output = ""
            xml_path = stage_path / f"{target_name}.xml"
            disk_layout = vcenter_vm_disk_layout(request.source_connector, workload)
            expected_qcow2 = [stage_path / f"{target_name}-disk{disk['index']}.qcow2" for disk in disk_layout]
            if resume_from_stage and xml_path.exists() and all(path.exists() for path in expected_qcow2):
                if reporter:
                    reporter.task(
                        f"{workload.id}-convert-staging",
                        f"{workload.vm_name}: convert into staging",
                        "Completed",
                        70,
                        f"Reusing preserved converted artifacts from {stage_path}",
                    )
            else:
                if resume_from_stage and reporter:
                    reporter.task(
                        f"{workload.id}-convert-staging",
                        f"{workload.vm_name}: convert into staging",
                        "Running",
                        28,
                        f"Preserved converted artifacts are missing from {stage_path}; rebuilding them before continuing",
                    )
                if reporter:
                    reporter.task(
                        f"{workload.id}-convert-staging",
                        f"{workload.vm_name}: convert into staging",
                        "Running",
                        55,
                        f"Downloading {workload.vm_name} source disks from vCenter and converting them into qcow2 artifacts in {stage_path}",
                    )
                env = source_vcenter_environment(request.source_connector, workload)
                timeout = int(request.options.get("timeout", 14400))
                converted_layout = []
                for disk in disk_layout:
                    descriptor_local = stage_path / disk["descriptor_name"]
                    flat_local = stage_path / disk["flat_name"]
                    if resume_from_stage and descriptor_local.exists() and flat_local.exists():
                        pass
                    else:
                        run(
                            ["govc", "datastore.download", "-dc", str(workload.details.get("datacenter") or ""), "-ds", disk["datastore"], disk["descriptor_path"], str(descriptor_local)],
                            env=env,
                            timeout=timeout,
                        )
                        run(
                            ["govc", "datastore.download", "-dc", str(workload.details.get("datacenter") or ""), "-ds", disk["datastore"], disk["flat_path"], str(flat_local)],
                            env=env,
                            timeout=timeout,
                        )
                    converted_local = stage_path / f"{target_name}-disk{disk['index']}.qcow2"
                    if not (resume_from_stage and converted_local.exists()):
                        run(
                            ["qemu-img", "convert", "-p", "-f", "vmdk", "-O", "qcow2", str(descriptor_local), str(converted_local)],
                            timeout=timeout,
                        )
                    try:
                        capacity_bytes = int(json.loads(run(["qemu-img", "info", "--output=json", str(converted_local)]))["virtual-size"])
                    except Exception:
                        capacity_bytes = disk["capacity_bytes"] or converted_local.stat().st_size
                    converted_layout.append({"local_path": str(converted_local), "capacity_bytes": capacity_bytes})
                metadata = {
                    "cpu": int(workload.details.get("cpu") or vm_info.get("cpu") or 1),
                    "memory_bytes": int(vm_info.get("memory_bytes") or int((workload.details.get("memory_gb") or 1) * 1024**3)),
                    "boot_firmware": str(workload.details.get("boot_firmware") or vm_info.get("boot_firmware") or "bios"),
                    "guest_os_id": str(workload.details.get("guest_os_id") or vm_info.get("guest_os_id") or "otherGuest64"),
                }
                xml_path.write_text(
                    render_kvm_domain_xml(target_name, metadata["cpu"], metadata["memory_bytes"], converted_layout, metadata["boot_firmware"]),
                    encoding="utf-8",
                )
                output = f"Downloaded {len(disk_layout)} VMware disk(s) and converted them into qcow2 artifacts for {target_name}"
                if reporter:
                    reporter.task(
                        f"{workload.id}-convert-staging",
                        f"{workload.vm_name}: convert into staging",
                        "Completed",
                        70,
                        f"Downloaded VMware VMDK artifacts and converted them into qcow2 for {target_name} in {stage_path}",
                    )
            if not xml_path.exists():
                raise RuntimeError("Converted staging did not generate target libvirt XML")
            root = ET.parse(xml_path)
            disk_elements = root.findall("./devices/disk[@device='disk']")
            if not disk_elements:
                raise RuntimeError("Converted staging did not generate any disk references in the libvirt XML")
            disk_payload = []
            for disk in disk_elements:
                source = disk.find("source")
                if source is None or not source.get("file"):
                    continue
                local_path = Path(source.get("file"))
                if not local_path.exists():
                    raise RuntimeError(f"Converted disk is missing from staging: {local_path}")
                try:
                    capacity_bytes = int(json.loads(run(["qemu-img", "info", "--output=json", str(local_path)]))["virtual-size"])
                except Exception:
                    capacity_bytes = local_path.stat().st_size
                disk_payload.append({"local_path": str(local_path), "capacity_bytes": capacity_bytes})
            if not disk_payload:
                raise RuntimeError("Converted staging artifacts are missing usable disk files")
            if reporter:
                reporter.task(
                    f"{workload.id}-provision",
                    f"{workload.vm_name}: provision target VM",
                    "Running",
                    82,
                    f"Sending converted disks and generated libvirt XML from {stage_path} to LaunchGrid for KVM provisioning on bridge {target_network}",
                )
            parsed = parse_domain_xml(ET.tostring(root.getroot(), encoding="unicode"))
            metadata = {
                "cpu": max(1, int(root.findtext("./vcpu", "1"))),
                "memory_bytes": parsed.get("memory_bytes", 1024**3),
                "boot_firmware": parsed.get("boot_firmware", "bios"),
                "libvirt_xml_path": str(xml_path),
            }
            workload.details["guest_os_id"] = workload.details.get("guest_os_id") or vm_info.get("guest_os_id") or "otherGuest64"
            provisioned = launchgrid_provision(request, workload, target_name, metadata, disk_payload)
            if reporter:
                reporter.task(
                    f"{workload.id}-provision",
                    f"{workload.vm_name}: provision target VM",
                    "Completed",
                    96,
                    f"Provisioned {target_name} on KVM through LaunchGrid using storage pool {target_storage_pool} and bridge {target_network}",
                )
            if not request.keep_source_vm:
                if reporter:
                    reporter.task(
                        f"{workload.id}-cleanup",
                        f"{workload.vm_name}: remove source VM",
                        "Running",
                        98,
                        f"Removing source vCenter VM {workload.vm_name} because Keep Source VM is disabled",
                    )
                remove_source_vcenter_vm(request.source_connector, workload)
                if reporter:
                    reporter.task(
                        f"{workload.id}-cleanup",
                        f"{workload.vm_name}: remove source VM",
                        "Completed",
                        100,
                        f"Removed source vCenter VM {workload.vm_name}",
                    )
            results.append(
                {
                    "ok": True,
                    "vm_id": workload.id,
                    "vm_name": workload.vm_name,
                    "target_name": target_name,
                    "target_pool": target_storage_pool,
                    "message": (output.strip() or "VMware disk download and qcow2 conversion completed and LaunchGrid provisioned the target VM")
                    + ("" if request.keep_source_vm else " and removed the source vCenter VM"),
                    "stage_path": str(stage_path),
                    "can_resume": False,
                    "details": provisioned,
                }
            )
        except Exception as exc:
            if reporter:
                reporter.task(f"{workload.id}-provision", f"{workload.vm_name}: provision target VM", "Failed", 82, str(exc))
            results.append(stage_failure_result(workload, target_name, stage_path, str(exc), can_resume=stage_path.exists()))
    if results and all(result.get("ok") for result in results):
        for result, workload in zip(results, request.workloads):
            append_migrated_vm_log(request.plan_id, request.plan_name, workload, result.get("target_name") or workload.vm_name, request.target_connector.connector_type)
        cleanup_plan_stage_directory(request.plan_id, request.plan_name)
    return results
