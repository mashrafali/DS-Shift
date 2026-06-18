from __future__ import annotations

import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from xml.sax.saxutils import escape

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


app = FastAPI(title="DS Shift LaunchGrid", version="0.1")


class Connector(BaseModel):
    id: int
    name: str
    connector_category: str
    connector_type: str
    endpoint: str | None = None
    port: int | None = None
    username: str | None = None
    target_network: str | None = None
    target_datastore: str | None = None
    target_vdc_name: str | None = None
    target_compute_name: str | None = None
    credential_reference: str | None = None
    credential_payload: dict = Field(default_factory=dict)


class ConvertedDisk(BaseModel):
    local_path: str
    capacity_bytes: int


class ProvisionRequest(BaseModel):
    target_connector: Connector
    vm_name: str
    cpu: int
    memory_bytes: int
    disks: list[ConvertedDisk]
    power_on: bool = False
    guest_os_id: str = "otherGuest64"
    boot_firmware: str = "bios"


def credential_value(reference: str | None) -> str | None:
    if not reference or not reference.startswith("env:"):
        return None
    return os.getenv(reference.split(":", 1)[1])


def connector_password(connector: Connector) -> str | None:
    if connector.credential_payload.get("password"):
        return str(connector.credential_payload["password"])
    return credential_value(connector.credential_reference)


def run(command: list[str], *, env: dict[str, str], timeout: int = 1800) -> str:
    completed = subprocess.run(command, capture_output=True, text=True, env=env, timeout=timeout, check=False)
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"{command[0]} exited with status {completed.returncode}"
        raise RuntimeError(f"{' '.join(command)} failed: {detail}")
    return completed.stdout.strip()


def govc_environment(connector: Connector) -> dict[str, str]:
    password = connector_password(connector)
    if not connector.endpoint or not connector.username or not password:
        raise RuntimeError("VMware target connector requires endpoint, username, and password")
    parsed = urlparse(connector.endpoint if "://" in connector.endpoint else f"https://{connector.endpoint}")
    env = os.environ.copy()
    env.update(
        {
            "GOVC_URL": f"https://{parsed.hostname}:{parsed.port or connector.port or 443}",
            "GOVC_USERNAME": connector.username,
            "GOVC_PASSWORD": password,
            "GOVC_INSECURE": "1",
        }
    )
    if connector.target_vdc_name:
        env["GOVC_DATACENTER"] = connector.target_vdc_name
    if connector.target_datastore:
        env["GOVC_DATASTORE"] = connector.target_datastore
    if connector.target_network:
        env["GOVC_NETWORK"] = connector.target_network
    return env


def import_placement(env: dict[str, str], compute_name: str) -> list[str]:
    host_path = run(["govc", "find", "-type", "h", "-name", compute_name], env=env, timeout=30)
    if host_path:
        return ["-host", host_path.splitlines()[0].strip()]
    cluster_path = run(["govc", "find", "-type", "c", "-name", compute_name], env=env, timeout=30)
    if cluster_path:
        return ["-pool", f"{cluster_path.splitlines()[0].strip()}/Resources"]
    raise RuntimeError(f"Target cluster or host {compute_name} was not found")


def rollback_vm(env: dict[str, str], vm_name: str, remote_dir: str) -> None:
    try:
        run(["govc", "vm.destroy", vm_name], env=env, timeout=60)
    except Exception:
        pass
    try:
        run(["govc", "datastore.rm", remote_dir], env=env, timeout=60)
    except Exception:
        pass


def request_memory_mb(memory_bytes: int) -> int:
    return max(1, memory_bytes // 1024 // 1024)


def ovf_descriptor(
    vm_name: str,
    cpu: int,
    memory_bytes: int,
    disks: list[ConvertedDisk],
    network_name: str,
    boot_firmware: str,
    guest_os_id: str,
) -> str:
    escaped_vm_name = escape(vm_name)
    escaped_network_name = escape(network_name)
    escaped_guest_os_id = escape(guest_os_id or "otherGuest64")
    file_rows: list[str] = []
    disk_rows: list[str] = []
    device_rows: list[str] = []
    for index, disk in enumerate(disks, start=1):
        local_path = Path(disk.local_path)
        file_rows.append(
            f'<File ovf:href="{escape(local_path.name)}" ovf:id="file{index}" ovf:size="{local_path.stat().st_size}"/>'
        )
        disk_rows.append(
            f'<Disk ovf:capacity="{disk.capacity_bytes}" ovf:capacityAllocationUnits="byte" '
            f'ovf:diskId="disk{index}" ovf:fileRef="file{index}" '
            'ovf:format="http://www.vmware.com/interfaces/specifications/vmdk.html#streamOptimized"/>'
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
    firmware = "efi" if str(boot_firmware).lower() in {"efi", "uefi"} else "bios"
    firmware_config = ""
    if firmware == "efi":
        firmware_config = """
      <vmw:Config ovf:required="false" vmw:key="bootOptions.efiSecureBootEnabled" vmw:value="false"/>
      <vmw:Config ovf:required="false" vmw:key="firmware" vmw:value="efi"/>"""
    memory_mb = request_memory_mb(memory_bytes)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Envelope xmlns="http://schemas.dmtf.org/ovf/envelope/1"
 xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1"
 xmlns:rasd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData"
 xmlns:vssd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_VirtualSystemSettingData"
 xmlns:vmw="http://www.vmware.com/schema/ovf">
  <References>{''.join(file_rows)}</References>
  <DiskSection><Info>Virtual disk information</Info>{''.join(disk_rows)}</DiskSection>
  <NetworkSection>
    <Info>Logical networks</Info>
    <Network ovf:name="{escaped_network_name}"><Description>{escaped_network_name}</Description></Network>
  </NetworkSection>
  <VirtualSystem ovf:id="{escaped_vm_name}">
    <Info>DS Shift migrated virtual machine</Info>
    <Name>{escaped_vm_name}</Name>
    <OperatingSystemSection ovf:id="101" vmw:osType="{escaped_guest_os_id}">
      <Info>Guest operating system</Info>
    </OperatingSystemSection>
    <VirtualHardwareSection>
      <Info>Virtual hardware requirements</Info>
      <System>
        <vssd:ElementName>Virtual Hardware Family</vssd:ElementName>
        <vssd:InstanceID>0</vssd:InstanceID>
        <vssd:VirtualSystemIdentifier>{escaped_vm_name}</vssd:VirtualSystemIdentifier>
        <vssd:VirtualSystemType>vmx-13</vssd:VirtualSystemType>
      </System>
      <Item><rasd:AllocationUnits>hertz * 10^6</rasd:AllocationUnits><rasd:ElementName>{cpu} virtual CPU(s)</rasd:ElementName><rasd:InstanceID>1</rasd:InstanceID><rasd:ResourceType>3</rasd:ResourceType><rasd:VirtualQuantity>{cpu}</rasd:VirtualQuantity></Item>
      <Item><rasd:AllocationUnits>byte * 2^20</rasd:AllocationUnits><rasd:ElementName>{memory_mb} MB of memory</rasd:ElementName><rasd:InstanceID>2</rasd:InstanceID><rasd:ResourceType>4</rasd:ResourceType><rasd:VirtualQuantity>{memory_mb}</rasd:VirtualQuantity></Item>
      <Item><rasd:Address>0</rasd:Address><rasd:ElementName>SCSI controller 0</rasd:ElementName><rasd:InstanceID>10</rasd:InstanceID><rasd:ResourceSubType>VirtualLsiLogicSAS</rasd:ResourceSubType><rasd:ResourceType>6</rasd:ResourceType></Item>
      {''.join(device_rows)}
      <Item>
        <rasd:AddressOnParent>7</rasd:AddressOnParent>
        <rasd:AutomaticAllocation>true</rasd:AutomaticAllocation>
        <rasd:Connection>{escaped_network_name}</rasd:Connection>
        <rasd:ElementName>Network adapter 1</rasd:ElementName>
        <rasd:InstanceID>20</rasd:InstanceID>
        <rasd:ResourceSubType>VmxNet3</rasd:ResourceSubType>
        <rasd:ResourceType>10</rasd:ResourceType>
      </Item>{firmware_config}
    </VirtualHardwareSection>
  </VirtualSystem>
</Envelope>
"""


def write_ovf_bundle(request: ProvisionRequest) -> Path:
    bundle_dir = Path(request.disks[0].local_path).resolve().parent
    ovf_path = bundle_dir / f"{request.vm_name}.ovf"
    ovf_path.write_text(
        ovf_descriptor(
            request.vm_name,
            request.cpu,
            request.memory_bytes,
            request.disks,
            request.target_connector.target_network or "VM Network",
            request.boot_firmware,
            request.guest_os_id,
        ),
        encoding="utf-8",
    )
    return ovf_path


def validate_request(request: ProvisionRequest) -> None:
    connector = request.target_connector
    if connector.connector_type not in {"VMware ESXi / vCenter", "VMware ESXi", "vCenter"}:
        raise RuntimeError("LaunchGrid currently provisions only VMware ESXi / vCenter targets")
    if not connector.target_vdc_name:
        raise RuntimeError("Target vDC Name is required on the VMware connector")
    if not connector.target_compute_name:
        raise RuntimeError("Target Cluster Name or Host Name is required on the VMware connector")
    if not connector.target_datastore:
        raise RuntimeError("Target Datastore is required on the VMware connector")
    if not connector.target_network:
        raise RuntimeError("Target Network is required on the VMware connector")
    if not request.disks:
        raise RuntimeError("At least one converted disk is required for provisioning")


@app.get("/health")
def health():
    return {"status": "ok", "service": "LaunchGrid"}


@app.post("/provision")
def provision(request: ProvisionRequest):
    try:
        validate_request(request)
        env = govc_environment(request.target_connector)
        remote_dir = request.vm_name
        command_log: list[str] = []
        for disk in request.disks:
            local_path = Path(disk.local_path)
            if not local_path.exists():
                raise RuntimeError(f"Converted disk is missing: {local_path}")
        ovf_path = write_ovf_bundle(request)
        command_log.append(f"generated OVF bundle {ovf_path}")
        import_command = [
            "govc",
            "import.ovf",
            "-name",
            request.vm_name,
            *import_placement(env, request.target_connector.target_compute_name),
            str(ovf_path),
        ]
        command_log.append(" ".join(import_command))
        try:
            run(import_command, env=env, timeout=7200)
            verify_command = ["govc", "vm.info", request.vm_name]
            command_log.append(" ".join(verify_command))
            run(verify_command, env=env, timeout=120)
            if request.power_on:
                power_command = ["govc", "vm.power", "-on", request.vm_name]
                command_log.append(" ".join(power_command))
                run(power_command, env=env, timeout=300)
        except Exception:
            rollback_vm(env, request.vm_name, remote_dir)
            raise
        return {
            "ok": True,
            "vm_name": request.vm_name,
            "disk_paths": [f"{remote_dir}/{Path(disk.local_path).name}" for disk in request.disks],
            "commands": command_log,
        }
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
