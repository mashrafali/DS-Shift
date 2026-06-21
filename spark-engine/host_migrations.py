from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
from urllib.parse import quote, urlparse
import xml.etree.ElementTree as ET

import httpx
import paramiko
from pyVim.connect import Disconnect, SmartConnect
from pyVmomi import vim


VMWARE_TYPES = {"VMware ESXi / vCenter", "VMware ESXi", "vCenter"}
STAGING_ROOT = Path(os.getenv("DS_SHIFT_STAGING_ROOT", "/DS-Shift-Staging"))
LAUNCHGRID_URL = os.getenv("LAUNCHGRID_URL", "http://launchgrid:8300").rstrip("/")


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


def run(command: list[str], *, env: dict | None = None, timeout: int = 7200) -> str:
    completed = subprocess.run(command, capture_output=True, text=True, env=env, timeout=timeout, check=False)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or f"{command[0]} exited with status {completed.returncode}")
    return completed.stdout


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.")
    if not cleaned:
        raise RuntimeError("Target VM name contains no usable characters")
    return cleaned[:80]


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
        "guest_os_id": workload.details.get("guest_os_id") or "otherGuest64",
        "boot_firmware": workload.details.get("boot_firmware") or metadata.get("boot_firmware") or "bios",
    }
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


def converted_vmdk_layout(stage_path: Path, target_name: str, disks: list[dict]) -> list[dict]:
    layout = []
    for index, disk in enumerate(disks, start=1):
        local_path = stage_path / f"{target_name}-disk{index}.vmdk"
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


def find_vcenter_vm(connector, workload):
    password = connector_password(connector)
    parsed = urlparse(connector.endpoint if "://" in (connector.endpoint or "") else f"https://{connector.endpoint}")
    if not parsed.hostname or not connector.username or not password:
        raise RuntimeError("vCenter connector credentials are unavailable")
    service_instance = SmartConnect(
        host=parsed.hostname,
        port=parsed.port or connector.port or 443,
        user=connector.username,
        pwd=password,
        disableSslCertValidation=True,
        connectionPoolTimeout=30,
    )
    try:
        content = service_instance.RetrieveContent()
        view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
        try:
            for vm_obj in view.view:
                if vm_obj._moId == workload.external_id or vm_obj.name == workload.vm_name:
                    return {
                        "name": vm_obj.name,
                        "power_state": str(vm_obj.runtime.powerState),
                        "host_name": vm_obj.runtime.host.name if vm_obj.runtime.host else None,
                    }
        finally:
            view.Destroy()
    finally:
        Disconnect(service_instance)
    raise RuntimeError(f"VM {workload.vm_name} was not found in vCenter")


def preflight_kvm_to_vcenter(request) -> list[dict]:
    target_datastore = request.target_connector.target_datastore
    target_network = request.options.get("target_network") or request.target_connector.target_network
    target_vdc_name = request.options.get("target_datacenter") or request.options.get("target_vdc_name") or request.target_connector.target_vdc_name
    target_compute_name = request.options.get("target_compute_name") or request.target_connector.target_compute_name
    checks = [
        {"check": "qemu_img", "ok": bool(shutil.which("qemu-img")), "message": shutil.which("qemu-img") or "qemu-img is not installed"},
        {"check": "govc", "ok": bool(shutil.which("govc")), "message": shutil.which("govc") or "govc is not installed"},
    ]
    try:
        staging_root = ensure_staging_root()
        checks.append({"check": "staging_path", "ok": True, "message": str(staging_root)})
    except Exception as exc:
        checks.append({"check": "staging_path", "ok": False, "message": str(exc)})
    try:
        env = govc_environment(request.target_connector, request.options)
        about = json.loads(run(["govc", "about", "-json"], env=env, timeout=30))
        checks.append({"check": "target_vcenter", "ok": True, "message": about.get("About", {}).get("FullName", "vCenter reachable")})
        if not target_datastore:
            raise RuntimeError("Target datastore is not configured on the target connector")
        run(["govc", "datastore.info", target_datastore], env=env, timeout=30)
        checks.append({"check": "target_datastore", "ok": True, "message": target_datastore})
        if not target_network:
            raise RuntimeError("Target network is not configured on the target connector")
        network = run(["govc", "find", "-type", "n", "-name", target_network], env=env, timeout=30).strip()
        if not network:
            raise RuntimeError(f"Target network {target_network} was not found")
        checks.append({"check": "target_network", "ok": True, "message": target_network})
        if target_vdc_name:
            datacenter = run(["govc", "find", "-type", "d", "-name", target_vdc_name], env=env, timeout=30).strip()
            if not datacenter:
                raise RuntimeError(f"Target vDC name {target_vdc_name} was not found")
            checks.append({"check": "target_vdc_name", "ok": True, "message": target_vdc_name})
        else:
            raise RuntimeError("Target vDC name is not configured on the target connector")
        if not target_compute_name:
            raise RuntimeError("Target Cluster Name or Host Name is not configured on the target connector")
        compute_kind, compute_path = find_compute_path(env, target_compute_name)
        checks.append({"check": "target_compute_name", "ok": True, "message": f"{target_compute_name} ({compute_kind}: {compute_path})"})
    except Exception as exc:
        checks.append({"check": "target_vcenter", "ok": False, "message": str(exc)})
    try:
        with ssh_client(request.source_connector) as client:
            for workload in request.workloads:
                state, _ = ssh_exec(client, f"virsh domstate {shlex.quote(workload.vm_name)}", timeout=20)
                stopped = state.strip().lower() in {"shut off", "shutoff"}
                checks.append({"check": "source_vm_state", "vm_name": workload.vm_name, "ok": stopped, "message": state.strip()})
                xml_text, _ = ssh_exec(client, f"virsh dumpxml {shlex.quote(workload.vm_name)}", timeout=20)
                disks = parse_domain_xml(xml_text)["disks"]
                checks.append({"check": "source_vm_disks", "vm_name": workload.vm_name, "ok": bool(disks), "message": f"{len(disks)} disk(s) found"})
    except Exception as exc:
        checks.append({"check": "source_kvm", "ok": False, "message": str(exc)})
    return checks


def preflight_vcenter_to_kvm(request) -> list[dict]:
    checks = [{"check": "virt_v2v", "ok": bool(shutil.which("virt-v2v")), "message": shutil.which("virt-v2v") or "virt-v2v is not installed"}]
    target_storage_pool = request.target_connector.target_storage_pool or request.options.get("target_storage_pool")
    target_network = request.target_connector.target_network or request.options.get("target_network")
    try:
        staging_root = ensure_staging_root()
        checks.append({"check": "staging_path", "ok": True, "message": str(staging_root)})
    except Exception as exc:
        checks.append({"check": "staging_path", "ok": False, "message": str(exc)})
    try:
        password = connector_password(request.source_connector)
        if not password:
            raise RuntimeError("vCenter password is unavailable")
        stage_path = temporary_stage_directory("ds-shift-v2v-preflight-")
        try:
            password_path = stage_path / "vcenter-password"
            password_path.write_text(password, encoding="utf-8")
            password_path.chmod(0o600)
            for workload in request.workloads:
                vm_info = find_vcenter_vm(request.source_connector, workload)
                stopped = vm_info["power_state"].lower() == "poweredoff"
                checks.append({"check": "source_vm_state", "vm_name": workload.vm_name, "ok": stopped, "message": vm_info["power_state"]})
                if stopped and shutil.which("virt-v2v"):
                    run(
                        [
                            "virt-v2v",
                            "-ic", vpx_uri(request.source_connector, workload, request.options),
                            "-ip", str(password_path),
                            workload.vm_name,
                            "--print-source",
                        ],
                        timeout=120,
                    )
                    checks.append({"check": "virt_v2v_source", "vm_name": workload.vm_name, "ok": True, "message": "virt-v2v read the source VM metadata"})
        finally:
            shutil.rmtree(stage_path, ignore_errors=True)
    except Exception as exc:
        checks.append({"check": "source_vcenter", "ok": False, "message": str(exc)})
    try:
        if not target_storage_pool:
            raise RuntimeError("Target KVM storage pool is not configured on the target connector")
        with ssh_client(request.target_connector) as client:
            pool_xml, _ = ssh_exec(client, f"virsh pool-dumpxml {shlex.quote(target_storage_pool)}", timeout=20)
            pool_path = ET.fromstring(pool_xml).findtext("./target/path")
            if not pool_path:
                raise RuntimeError("Target storage pool has no filesystem path")
            ssh_exec(client, f"test -d {shlex.quote(pool_path)} && test -w {shlex.quote(pool_path)}", timeout=20)
            checks.append({"check": "target_kvm_pool", "ok": True, "message": f"{target_storage_pool}: {pool_path}"})
            if target_network:
                ssh_exec(client, f"ip link show {shlex.quote(target_network)}", timeout=20)
                checks.append({"check": "target_kvm_network", "ok": True, "message": target_network})
    except Exception as exc:
        checks.append({"check": "target_kvm", "ok": False, "message": str(exc)})
    return checks


def execute_kvm_to_vcenter(request, reporter=None) -> list[dict]:
    results = []
    with ssh_client(request.source_connector) as client:
        sftp = client.open_sftp()
        try:
            for workload in request.workloads:
                target_name = safe_name(request.options.get("target_name") or f"{workload.vm_name}-migrated")
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
                        for index, disk in enumerate(metadata["disks"], start=1):
                            source_path = stage_path / f"source-{index}{Path(disk['path']).suffix or '.img'}"
                            target_path = stage_path / f"{target_name}-disk{index}.vmdk"
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
                    shutil.rmtree(stage_path, ignore_errors=True)
                except Exception as exc:
                    if reporter:
                        reporter.task(f"{workload.id}-provision", f"{workload.vm_name}: provision target VM", "Failed", 88, str(exc))
                    results.append(stage_failure_result(workload, target_name, stage_path, str(exc), can_resume=stage_path.exists()))
        finally:
            sftp.close()
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
    password = connector_password(request.source_connector)
    if not password:
        raise RuntimeError("vCenter password is unavailable")
    target_storage_pool = request.target_connector.target_storage_pool or request.options.get("target_storage_pool")
    target_network = request.target_connector.target_network or request.options.get("target_network")
    if not target_storage_pool:
        raise RuntimeError("Target KVM storage pool is not configured on the target connector")
    with ssh_client(request.target_connector) as client:
        pool_xml, _ = ssh_exec(client, f"virsh pool-dumpxml {shlex.quote(target_storage_pool)}")
        pool_path = ET.fromstring(pool_xml).findtext("./target/path")
        if not pool_path:
            raise RuntimeError("Target storage pool has no filesystem path")
        sftp = client.open_sftp()
        try:
            for workload in request.workloads:
                target_name = safe_name(request.options.get("target_name") or f"{workload.vm_name}-migrated")
                stage_path = preserved_stage_directory(request.plan_id, workload.id, workload.vm_name, request.plan_name)
                try:
                    resume_from_stage = bool(request.options.get("resume_from_stage"))
                    if reporter:
                        reporter.task(f"{workload.id}-inspect", f"{workload.vm_name}: inspect source VM", "Running", 15, f"Inspecting vCenter source VM {workload.vm_name}")
                    vm_info = find_vcenter_vm(request.source_connector, workload)
                    if vm_info["power_state"].lower() != "poweredoff":
                        raise RuntimeError(f"{workload.vm_name} must be powered off before virt-v2v conversion")
                    ssh_exec(client, f"! virsh dominfo {shlex.quote(target_name)} >/dev/null 2>&1", timeout=20)
                    if reporter:
                        reporter.task(f"{workload.id}-inspect", f"{workload.vm_name}: inspect source VM", "Completed", 25, f"Verified powered-off source and free target name {target_name}")
                    if resume_from_stage:
                        candidate_paths = candidate_stage_directories(request.plan_id, workload.id, workload.vm_name, request.plan_name)
                        stage_path = next((path for path in candidate_paths if path.exists()), candidate_paths[0])
                        stage_path.mkdir(parents=True, exist_ok=True)
                    else:
                        stage_path = stage_directory(request.plan_id, workload.id, workload.vm_name, request.plan_name)
                    local_disks = []
                    output = ""
                    password_path = stage_path / "vcenter-password"
                    xml_path = stage_path / f"{target_name}.xml"
                    if resume_from_stage and xml_path.exists():
                        if reporter:
                            reporter.task(f"{workload.id}-reuse", f"{workload.vm_name}: reuse staged conversion", "Completed", 70, f"Reusing preserved virt-v2v artifacts from {stage_path}")
                    else:
                        if resume_from_stage and reporter:
                            reporter.task(
                                f"{workload.id}-reuse",
                                f"{workload.vm_name}: reuse staged conversion",
                                "Running",
                                28,
                                f"Preserved conversion artifacts are missing from {stage_path}; rebuilding conversion before continuing",
                            )
                        password_path.write_text(password, encoding="utf-8")
                        password_path.chmod(0o600)
                        if reporter:
                            reporter.task(f"{workload.id}-convert", f"{workload.vm_name}: convert with virt-v2v", "Running", 55, f"Converting {workload.vm_name} with virt-v2v in shared staging {stage_path}")
                        command = [
                            "virt-v2v",
                            "-ic", vpx_uri(request.source_connector, workload, request.options),
                            "-ip", str(password_path),
                            workload.vm_name,
                            "-o", "local",
                            "-os", str(stage_path),
                            "-of", "qcow2",
                            "-on", target_name,
                            "--root", request.options.get("root_selection", "first"),
                        ]
                        if target_network:
                            command.extend(["--network", target_network])
                        output = run(command, timeout=int(request.options.get("timeout", 14400)))
                        if reporter:
                            reporter.task(f"{workload.id}-convert", f"{workload.vm_name}: convert with virt-v2v", "Completed", 70, f"virt-v2v created local conversion artifacts for {target_name} in shared staging")
                    if not xml_path.exists():
                        raise RuntimeError("virt-v2v did not generate target libvirt XML")
                    root = ET.parse(xml_path)
                    if reporter:
                        reporter.task(f"{workload.id}-transfer", f"{workload.vm_name}: transfer converted disks", "Running", 82, f"Uploading converted disks into storage pool {target_storage_pool}")
                    for disk in root.findall("./devices/disk[@device='disk']"):
                        source = disk.find("source")
                        if source is None or not source.get("file"):
                            continue
                        local_path = Path(source.get("file"))
                        remote_path = f"{pool_path.rstrip('/')}/{target_name}-{local_path.name.rsplit('-', 1)[-1]}.qcow2"
                        sftp.put(str(local_path), remote_path)
                        source.set("file", remote_path)
                        local_disks.append(remote_path)
                    root.write(xml_path, encoding="unicode")
                    remote_xml = f"/tmp/ds-shift-{target_name}.xml"
                    sftp.put(str(xml_path), remote_xml)
                    if reporter:
                        reporter.task(f"{workload.id}-transfer", f"{workload.vm_name}: transfer converted disks", "Succeeded", 88, f"Uploaded converted disks and target XML for {target_name}")
                    try:
                        if reporter:
                            reporter.task(f"{workload.id}-define", f"{workload.vm_name}: define target VM", "Running", 94, f"Defining libvirt domain {target_name}")
                        ssh_exec(client, f"virsh define {shlex.quote(remote_xml)}", timeout=30)
                        if request.options.get("autostart"):
                            ssh_exec(client, f"virsh autostart {shlex.quote(target_name)}", timeout=30)
                        if request.options.get("power_on"):
                            ssh_exec(client, f"virsh start {shlex.quote(target_name)}", timeout=60)
                        if reporter:
                            reporter.task(f"{workload.id}-define", f"{workload.vm_name}: define target VM", "Completed", 98, f"Defined libvirt domain {target_name}")
                    except Exception:
                        for remote_disk in local_disks:
                            try:
                                sftp.remove(remote_disk)
                            except OSError:
                                pass
                        raise
                    finally:
                        try:
                            sftp.remove(remote_xml)
                        except OSError:
                            pass
                    results.append({"ok": True, "vm_id": workload.id, "vm_name": workload.vm_name, "target_name": target_name, "target_pool": target_storage_pool, "message": output.strip() or "virt-v2v conversion and libvirt definition completed", "stage_path": str(stage_path), "can_resume": False})
                    shutil.rmtree(stage_path, ignore_errors=True)
                except Exception as exc:
                    if reporter:
                        reporter.task(f"{workload.id}-define", f"{workload.vm_name}: define target VM", "Failed", 82, str(exc))
                    results.append(stage_failure_result(workload, target_name, stage_path, str(exc), can_resume=stage_path.exists()))
        finally:
            sftp.close()
    return results
