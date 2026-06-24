from __future__ import annotations

import os
import subprocess
import shlex
from pathlib import Path
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from fastapi import FastAPI, HTTPException
import paramiko
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
    target_storage_pool: str | None = None
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
    autostart: bool = False
    guest_os_id: str = "otherGuest64"
    boot_firmware: str = "bios"
    libvirt_xml_path: str | None = None


def credential_value(reference: str | None) -> str | None:
    if not reference or not reference.startswith("env:"):
        return None
    return os.getenv(reference.split(":", 1)[1])


def connector_password(connector: Connector) -> str | None:
    if connector.credential_payload.get("password"):
        return str(connector.credential_payload["password"])
    return credential_value(connector.credential_reference)


def ssh_parts(connector: Connector) -> tuple[str, int, str]:
    endpoint = connector.endpoint
    if not endpoint:
        raise RuntimeError("KVM target connector requires an endpoint")
    parsed = urlparse(endpoint)
    if parsed.scheme.startswith("qemu+ssh"):
        host = parsed.hostname or ""
        user = parsed.username or connector.username or "root"
        port = parsed.port or 22
    else:
        host_port = endpoint.replace("https://", "").replace("http://", "").split("/")[0]
        host = host_port.split(":", 1)[0]
        user = connector.username or (endpoint.split("@", 1)[0] if "@" in endpoint else "root")
        port = int(host_port.split(":", 1)[1]) if ":" in host_port and host_port.rsplit(":", 1)[1].isdigit() else 22
    if not host:
        raise RuntimeError("KVM target connector host could not be parsed from endpoint")
    return host, port, user


def ssh_client(connector: Connector) -> paramiko.SSHClient:
    host, port, user = ssh_parts(connector)
    password = connector_password(connector)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        port=port,
        username=user,
        password=password,
        look_for_keys=not bool(password),
        allow_agent=not bool(password),
        timeout=15,
        banner_timeout=15,
        auth_timeout=15,
    )
    return client


def ssh_exec(client: paramiko.SSHClient, command: str, timeout: int = 1800) -> tuple[str, str]:
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    code = stdout.channel.recv_exit_status()
    output = stdout.read().decode(errors="replace")
    error = stderr.read().decode(errors="replace")
    if code:
        raise RuntimeError(error.strip() or output.strip() or f"Remote command exited with status {code}")
    return output, error


def discover_kvm_machine_types(client: paramiko.SSHClient) -> list[str]:
    output, _ = ssh_exec(client, "virsh capabilities", timeout=30)
    machines: list[str] = []
    try:
        root = ET.fromstring(output)
        for machine in root.findall(".//machine"):
            if machine.text:
                value = machine.text.strip()
                if value and value not in machines:
                    machines.append(value)
            canonical = machine.get("canonical")
            if canonical and canonical not in machines:
                machines.append(canonical)
    except ET.ParseError:
        pass
    if machines:
        return machines
    fallback, _ = ssh_exec(client, "qemu-kvm -machine help 2>/dev/null || /usr/libexec/qemu-kvm -machine help", timeout=30)
    for line in fallback.splitlines():
        name = line.split(maxsplit=1)[0] if line.strip() else ""
        if name and name not in {"Supported", "machines"} and name not in machines:
            machines.append(name)
    return machines


def select_kvm_machine_type(requested: str | None, supported: list[str]) -> str | None:
    if requested and requested in supported:
        return requested
    for candidate in ("q35", "pc-q35-rhel9.8.0", "pc-q35-rhel9.6.0", "pc-q35-rhel9.4.0", "pc-q35-rhel9.2.0", "pc-q35-rhel9.0.0"):
        if candidate in supported:
            return candidate
    for candidate in supported:
        if candidate.startswith("pc-q35"):
            return candidate
    for candidate in ("pc", "pc-i440fx-rhel7.6.0"):
        if candidate in supported:
            return candidate
    return supported[0] if supported else requested


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


def absolute_inventory_path(env: dict[str, str], inventory_path: str) -> str:
    path = inventory_path.strip()
    if path.startswith("/"):
        return path
    if path.startswith("./"):
        datacenter = (env.get("GOVC_DATACENTER") or "").strip().strip("/")
        if not datacenter:
            raise RuntimeError(f"Cannot normalize inventory path without GOVC_DATACENTER: {inventory_path}")
        return f"/{datacenter}/{path[2:]}"
    return path


def import_placement(env: dict[str, str], compute_name: str) -> list[str]:
    host_path = run(["govc", "find", "-type", "h", "-name", compute_name], env=env, timeout=30)
    if host_path:
        return ["-host", absolute_inventory_path(env, host_path.splitlines()[0].strip())]
    cluster_path = run(["govc", "find", "-type", "c", "-name", compute_name], env=env, timeout=30)
    if cluster_path:
        cluster_inventory_path = absolute_inventory_path(env, cluster_path.splitlines()[0].strip())
        return ["-pool", f"{cluster_inventory_path}/Resources"]
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


def rollback_kvm_vm(client: paramiko.SSHClient, vm_name: str, remote_disk_paths: list[str]) -> None:
    quoted_name = shlex.quote(vm_name)
    for command in [
        f"virsh destroy {quoted_name}",
        f"virsh undefine {quoted_name} --nvram --managed-save --snapshots-metadata --checkpoints-metadata",
        f"virsh undefine {quoted_name}",
    ]:
        try:
            ssh_exec(client, command, timeout=120)
            break
        except Exception:
            continue
    for remote_disk in remote_disk_paths:
        try:
            ssh_exec(client, f"rm -f {shlex.quote(remote_disk)}", timeout=60)
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
      <Item><rasd:Address>0</rasd:Address><rasd:ElementName>SCSI controller 0</rasd:ElementName><rasd:InstanceID>10</rasd:InstanceID><rasd:ResourceSubType>LsiLogic</rasd:ResourceSubType><rasd:ResourceType>6</rasd:ResourceType></Item>
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


def normalize_kvm_xml(
    xml_path: Path,
    vm_name: str,
    disks: list[ConvertedDisk],
    remote_pool_path: str,
    target_bridge: str,
    supported_machine_types: list[str] | None = None,
) -> str:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    name_node = root.find("./name")
    if name_node is None:
        name_node = ET.SubElement(root, "name")
    name_node.text = vm_name
    uuid_node = root.find("./uuid")
    if uuid_node is not None:
        root.remove(uuid_node)
    os_node = root.find("./os")
    if os_node is not None:
        type_node = os_node.find("./type")
        if type_node is not None and supported_machine_types:
            selected_machine = select_kvm_machine_type(type_node.get("machine"), supported_machine_types)
            if selected_machine:
                type_node.set("machine", selected_machine)
        nvram = os_node.find("./nvram")
        if nvram is not None:
            os_node.remove(nvram)
    for index, disk in enumerate(root.findall("./devices/disk[@device='disk']")):
        source = disk.find("./source")
        if source is None:
            source = ET.SubElement(disk, "source")
        local_name = Path(disks[index].local_path).name
        source.attrib.clear()
        source.set("file", f"{remote_pool_path.rstrip('/')}/{local_name}")
        driver = disk.find("./driver")
        if driver is None:
            driver = ET.SubElement(disk, "driver")
        driver.set("name", "qemu")
        driver.set("type", "qcow2")
    for interface in root.findall("./devices/interface"):
        interface.set("type", "bridge")
        for child in list(interface):
            if child.tag in {"source", "virtualport", "filterref", "backenddomain"}:
                interface.remove(child)
        ET.SubElement(interface, "source", {"bridge": target_bridge})
        model = interface.find("./model")
        if model is None:
            ET.SubElement(interface, "model", {"type": "virtio"})
    return ET.tostring(root, encoding="unicode")


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
    if not request.disks:
        raise RuntimeError("At least one converted disk is required for provisioning")
    if connector.connector_type in {"VMware ESXi / vCenter", "VMware ESXi", "vCenter"}:
        if not connector.target_vdc_name:
            raise RuntimeError("Target vDC Name is required on the VMware connector")
        if not connector.target_compute_name:
            raise RuntimeError("Target Cluster Name or Host Name is required on the VMware connector")
        if not connector.target_datastore:
            raise RuntimeError("Target Datastore is required on the VMware connector")
        if not connector.target_network:
            raise RuntimeError("Target Network is required on the VMware connector")
        return
    if connector.connector_type != "KVM":
        raise RuntimeError(f"LaunchGrid does not support target connector type {connector.connector_type}")
    if not connector.target_storage_pool:
        raise RuntimeError("Target storage pool is required on the KVM connector")
    if not connector.target_network:
        raise RuntimeError("Target network bridge is required on the KVM connector")
    if not request.libvirt_xml_path:
        raise RuntimeError("libvirt XML path is required for KVM provisioning")


def provision_vmware(request: ProvisionRequest) -> dict:
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


def provision_kvm(request: ProvisionRequest) -> dict:
    connector = request.target_connector
    local_xml = Path(request.libvirt_xml_path or "")
    if not local_xml.exists():
        raise RuntimeError(f"Converted libvirt XML is missing: {local_xml}")
    for disk in request.disks:
        local_path = Path(disk.local_path)
        if not local_path.exists():
            raise RuntimeError(f"Converted disk is missing: {local_path}")
    command_log: list[str] = []
    client = ssh_client(connector)
    sftp = client.open_sftp()
    remote_disk_paths: list[str] = []
    remote_xml = f"/tmp/ds-shift-{request.vm_name}.xml"
    try:
        pool_xml, _ = ssh_exec(client, f"virsh pool-dumpxml {shlex.quote(connector.target_storage_pool)}", timeout=30)
        pool_path = ET.fromstring(pool_xml).findtext("./target/path")
        if not pool_path:
            raise RuntimeError("Target storage pool has no filesystem path")
        ssh_exec(client, f"! virsh dominfo {shlex.quote(request.vm_name)} >/dev/null 2>&1", timeout=20)
        supported_machine_types = discover_kvm_machine_types(client)
        rendered_xml = normalize_kvm_xml(local_xml, request.vm_name, request.disks, pool_path, connector.target_network, supported_machine_types)
        command_log.append(f"selected KVM machine type from target capabilities: {select_kvm_machine_type(None, supported_machine_types) or 'unchanged'}")
        for disk in request.disks:
            local_path = Path(disk.local_path)
            remote_path = f"{pool_path.rstrip('/')}/{local_path.name}"
            command_log.append(f"upload {local_path} -> {remote_path}")
            sftp.put(str(local_path), remote_path)
            remote_disk_paths.append(remote_path)
        tmp_xml = local_xml.parent / f"{request.vm_name}.launchgrid.xml"
        try:
            tmp_xml.write_text(rendered_xml, encoding="utf-8")
            command_log.append(f"upload {tmp_xml} -> {remote_xml}")
            sftp.put(str(tmp_xml), remote_xml)
        finally:
            tmp_xml.unlink(missing_ok=True)
        define_command = f"virsh define {shlex.quote(remote_xml)}"
        command_log.append(define_command)
        ssh_exec(client, define_command, timeout=120)
        if request.autostart:
            autostart_command = f"virsh autostart {shlex.quote(request.vm_name)}"
            command_log.append(autostart_command)
            ssh_exec(client, autostart_command, timeout=60)
        if request.power_on:
            start_command = f"virsh start {shlex.quote(request.vm_name)}"
            command_log.append(start_command)
            ssh_exec(client, start_command, timeout=120)
        return {"ok": True, "vm_name": request.vm_name, "disk_paths": remote_disk_paths, "commands": command_log}
    except Exception:
        rollback_kvm_vm(client, request.vm_name, remote_disk_paths)
        raise
    finally:
        try:
            sftp.remove(remote_xml)
        except Exception:
            pass
        sftp.close()
        client.close()


@app.get("/health")
def health():
    return {"status": "ok", "service": "LaunchGrid"}


@app.post("/provision")
def provision(request: ProvisionRequest):
    try:
        validate_request(request)
        if request.target_connector.connector_type in {"VMware ESXi / vCenter", "VMware ESXi", "vCenter"}:
            return provision_vmware(request)
        return provision_kvm(request)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
