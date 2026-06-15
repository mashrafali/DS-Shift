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

import paramiko
from pyVim.connect import Disconnect, SmartConnect
from pyVmomi import vim


VMWARE_TYPES = {"VMware ESXi / vCenter", "VMware ESXi", "vCenter"}


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
    return {
        "cpu": int(root.findtext("vcpu", "1")),
        "memory_bytes": memory_bytes,
        "disks": disks,
        "interfaces": len(root.findall("./devices/interface")),
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
    mappings = {
        "target_datacenter": "GOVC_DATACENTER",
        "target_datastore": "GOVC_DATASTORE",
        "target_network": "GOVC_NETWORK",
        "target_resource_pool": "GOVC_RESOURCE_POOL",
        "target_folder": "GOVC_FOLDER",
    }
    for option, variable in mappings.items():
        if options.get(option):
            env[variable] = str(options[option])
    return env


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
    checks = [
        {"check": "qemu_img", "ok": bool(shutil.which("qemu-img")), "message": shutil.which("qemu-img") or "qemu-img is not installed"},
        {"check": "govc", "ok": bool(shutil.which("govc")), "message": shutil.which("govc") or "govc is not installed"},
    ]
    try:
        env = govc_environment(request.target_connector, request.options)
        about = json.loads(run(["govc", "about", "-json"], env=env, timeout=30))
        checks.append({"check": "target_vcenter", "ok": True, "message": about.get("About", {}).get("FullName", "vCenter reachable")})
        run(["govc", "datastore.info", request.options["target_datastore"]], env=env, timeout=30)
        checks.append({"check": "target_datastore", "ok": True, "message": request.options["target_datastore"]})
        network = run(["govc", "find", "-type", "n", "-name", request.options["target_network"]], env=env, timeout=30).strip()
        if not network:
            raise RuntimeError(f"Target network {request.options['target_network']} was not found")
        checks.append({"check": "target_network", "ok": True, "message": request.options["target_network"]})
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
    try:
        password = connector_password(request.source_connector)
        if not password:
            raise RuntimeError("vCenter password is unavailable")
        with tempfile.TemporaryDirectory(prefix="ds-shift-v2v-preflight-") as stage:
            password_path = Path(stage) / "vcenter-password"
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
    except Exception as exc:
        checks.append({"check": "source_vcenter", "ok": False, "message": str(exc)})
    try:
        with ssh_client(request.target_connector) as client:
            pool_xml, _ = ssh_exec(client, f"virsh pool-dumpxml {shlex.quote(request.options['target_storage_pool'])}", timeout=20)
            pool_path = ET.fromstring(pool_xml).findtext("./target/path")
            if not pool_path:
                raise RuntimeError("Target storage pool has no filesystem path")
            ssh_exec(client, f"test -d {shlex.quote(pool_path)} && test -w {shlex.quote(pool_path)}", timeout=20)
            checks.append({"check": "target_kvm_pool", "ok": True, "message": f"{request.options['target_storage_pool']}: {pool_path}"})
    except Exception as exc:
        checks.append({"check": "target_kvm", "ok": False, "message": str(exc)})
    return checks


def execute_kvm_to_vcenter(request, reporter=None) -> list[dict]:
    results = []
    env = govc_environment(request.target_connector, request.options)
    with ssh_client(request.source_connector) as client:
        sftp = client.open_sftp()
        try:
            for workload in request.workloads:
                target_name = safe_name(request.options.get("target_name") or f"{workload.vm_name}-migrated")
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
                with tempfile.TemporaryDirectory(prefix="ds-shift-kvm-vc-") as stage:
                    stage_path = Path(stage)
                    ovf_disks = []
                    for index, disk in enumerate(metadata["disks"], start=1):
                        source_path = stage_path / f"source-{index}{Path(disk['path']).suffix or '.img'}"
                        target_path = stage_path / f"{target_name}-disk{index}.vmdk"
                        if reporter:
                            reporter.task(
                                f"{workload.id}-stage-{index}",
                                f"{workload.vm_name}: stage source disk {index}",
                                "Running",
                                35,
                                f"Copying {disk['path']} into Spark staging",
                            )
                        sftp.get(disk["path"], str(source_path))
                        if reporter:
                            reporter.task(
                                f"{workload.id}-stage-{index}",
                                f"{workload.vm_name}: stage source disk {index}",
                                "Succeeded",
                                45,
                                f"Copied source disk {index} into Spark staging",
                            )
                        info = json.loads(run(["qemu-img", "info", "--output=json", str(source_path)]))
                        if reporter:
                            reporter.task(
                                f"{workload.id}-convert-{index}",
                                f"{workload.vm_name}: convert disk {index}",
                                "Running",
                                60,
                                f"Converting source disk {index} to stream-optimized VMDK",
                            )
                        run(["qemu-img", "convert", "-p", "-O", "vmdk", "-o", "subformat=streamOptimized", str(source_path), str(target_path)])
                        ovf_disks.append({"name": target_path.name, "size": target_path.stat().st_size, "capacity": info["virtual-size"]})
                        source_path.unlink()
                        if reporter:
                            reporter.task(
                                f"{workload.id}-convert-{index}",
                                f"{workload.vm_name}: convert disk {index}",
                                "Succeeded",
                                70,
                                f"Converted source disk {index} to {target_path.name}",
                            )
                    ovf_path = stage_path / f"{target_name}.ovf"
                    if reporter:
                        reporter.task(f"{workload.id}-package", f"{workload.vm_name}: package OVA", "Running", 75, f"Building OVF and OVA package for {target_name}")
                    ovf_path.write_text(
                        ovf_descriptor(target_name, metadata["cpu"], metadata["memory_bytes"], ovf_disks, metadata["interfaces"]),
                        encoding="utf-8",
                    )
                    ova_path = stage_path / f"{target_name}.ova"
                    with tarfile.open(ova_path, "w") as archive:
                        archive.add(ovf_path, arcname=ovf_path.name)
                        for disk in ovf_disks:
                            archive.add(stage_path / disk["name"], arcname=disk["name"])
                    if reporter:
                        reporter.task(f"{workload.id}-package", f"{workload.vm_name}: package OVA", "Succeeded", 82, f"Prepared OVA package {ova_path.name}")
                    command = ["govc", "import.ova", "-name", target_name]
                    if request.options.get("power_on"):
                        command.append("-powerOn")
                    command.append(str(ova_path))
                    if reporter:
                        reporter.task(f"{workload.id}-import", f"{workload.vm_name}: import into vCenter", "Running", 92, f"Importing {ova_path.name} into vCenter as {target_name}")
                    output = run(command, env=env, timeout=int(request.options.get("timeout", 14400)))
                    if reporter:
                        reporter.task(f"{workload.id}-import", f"{workload.vm_name}: import into vCenter", "Succeeded", 98, f"Imported {target_name} into vCenter")
                results.append({"ok": True, "vm_id": workload.id, "vm_name": workload.vm_name, "target_name": target_name, "message": output.strip() or "OVA imported into vCenter"})
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
    with ssh_client(request.target_connector) as client:
        pool_xml, _ = ssh_exec(client, f"virsh pool-dumpxml {shlex.quote(request.options['target_storage_pool'])}")
        pool_path = ET.fromstring(pool_xml).findtext("./target/path")
        if not pool_path:
            raise RuntimeError("Target storage pool has no filesystem path")
        sftp = client.open_sftp()
        try:
            for workload in request.workloads:
                if reporter:
                    reporter.task(f"{workload.id}-inspect", f"{workload.vm_name}: inspect source VM", "Running", 15, f"Inspecting vCenter source VM {workload.vm_name}")
                vm_info = find_vcenter_vm(request.source_connector, workload)
                if vm_info["power_state"].lower() != "poweredoff":
                    raise RuntimeError(f"{workload.vm_name} must be powered off before virt-v2v conversion")
                target_name = safe_name(request.options.get("target_name") or f"{workload.vm_name}-migrated")
                ssh_exec(client, f"! virsh dominfo {shlex.quote(target_name)} >/dev/null 2>&1", timeout=20)
                if reporter:
                    reporter.task(f"{workload.id}-inspect", f"{workload.vm_name}: inspect source VM", "Succeeded", 25, f"Verified powered-off source and free target name {target_name}")
                with tempfile.TemporaryDirectory(prefix="ds-shift-vc-kvm-") as stage:
                    stage_path = Path(stage)
                    password_path = stage_path / "vcenter-password"
                    password_path.write_text(password, encoding="utf-8")
                    password_path.chmod(0o600)
                    if reporter:
                        reporter.task(f"{workload.id}-convert", f"{workload.vm_name}: convert with virt-v2v", "Running", 55, f"Converting {workload.vm_name} with virt-v2v")
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
                    if request.options.get("target_network"):
                        command.extend(["--network", request.options["target_network"]])
                    output = run(command, timeout=int(request.options.get("timeout", 14400)))
                    if reporter:
                        reporter.task(f"{workload.id}-convert", f"{workload.vm_name}: convert with virt-v2v", "Succeeded", 70, f"virt-v2v created local conversion artifacts for {target_name}")
                    xml_path = stage_path / f"{target_name}.xml"
                    if not xml_path.exists():
                        raise RuntimeError("virt-v2v did not generate target libvirt XML")
                    root = ET.parse(xml_path)
                    local_disks = []
                    if reporter:
                        reporter.task(f"{workload.id}-transfer", f"{workload.vm_name}: transfer converted disks", "Running", 82, f"Uploading converted disks into storage pool {request.options['target_storage_pool']}")
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
                            reporter.task(f"{workload.id}-define", f"{workload.vm_name}: define target VM", "Succeeded", 98, f"Defined libvirt domain {target_name}")
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
                results.append({"ok": True, "vm_id": workload.id, "vm_name": workload.vm_name, "target_name": target_name, "target_pool": request.options["target_storage_pool"], "message": output.strip() or "virt-v2v conversion and libvirt definition completed"})
        finally:
            sftp.close()
    return results
