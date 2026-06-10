from __future__ import annotations

import re
import ssl
from urllib.parse import urlparse

import httpx
import paramiko
from fastapi import FastAPI
from pyVim.connect import Disconnect, SmartConnect
from pyVmomi import vim

from common import ConnectorRequest, EngineResponse, EngineResult, credential_from_env

app = FastAPI(title="DS Shift Host Connector Engine", version="1.0")

PLATFORMS = [
    {"type": "KVM", "tool": "Paramiko SSH and virsh", "discovery": True},
    {"type": "VMware ESXi / vCenter", "tool": "VMware pyVmomi", "discovery": True},
    {"type": "Nutanix AHV", "tool": "Nutanix Prism Central v3 REST API", "discovery": True},
]


@app.get("/health")
def health():
    return {"status": "ok", "engine": "Host Connector Engine", "platforms": len(PLATFORMS)}


@app.get("/platforms")
def platforms():
    return PLATFORMS


@app.post("/validate", response_model=EngineResponse)
def validate(request: ConnectorRequest):
    return _dispatch(request, discovery=False)


@app.post("/discover", response_model=EngineResponse)
def discover(request: ConnectorRequest):
    return _dispatch(request, discovery=True)


def _dispatch(request: ConnectorRequest, discovery: bool) -> EngineResult:
    if request.connector_type == "KVM":
        return discover_kvm(request) if discovery else validate_kvm(request)
    if request.connector_type in {"VMware ESXi / vCenter", "VMware ESXi", "vCenter"}:
        return discover_vcenter(request) if discovery else validate_vcenter(request)
    if request.connector_type == "Nutanix AHV":
        return discover_nutanix(request) if discovery else validate_nutanix(request)
    return EngineResult(False, f"Unsupported host connector type: {request.connector_type}", [], [])


def _ssh_parts(request: ConnectorRequest) -> tuple[str, int, str]:
    if not request.endpoint:
        raise ValueError("Connector endpoint is required")
    parsed = urlparse(request.endpoint)
    if parsed.scheme.startswith("qemu+ssh"):
        return parsed.hostname or "", parsed.port or request.port or 22, parsed.username or request.username or "root"
    host_port = request.endpoint.replace("https://", "").replace("http://", "").split("/")[0]
    host = host_port.split(":", 1)[0]
    port = request.port or (int(host_port.rsplit(":", 1)[1]) if ":" in host_port and host_port.rsplit(":", 1)[1].isdigit() else 22)
    return host, port, request.username or "root"


def _ssh_client(request: ConnectorRequest) -> paramiko.SSHClient:
    host, port, user = _ssh_parts(request)
    if not host:
        raise ValueError("Connector host could not be parsed")
    password = credential_from_env(request.credential_reference)
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
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    code = stdout.channel.recv_exit_status()
    return code, stdout.read().decode(errors="replace"), stderr.read().decode(errors="replace")


def validate_kvm(request: ConnectorRequest) -> EngineResult:
    commands = ["Paramiko SSH connect", "virsh list --all --name"]
    try:
        with _ssh_client(request) as client:
            code, output, error = _ssh_exec(client, "virsh list --all --name", timeout=15)
            if code:
                return EngineResult(False, "KVM validation failed. SSH works, but virsh access failed.", [], commands)
            return EngineResult(True, "KVM connector validated. SSH and virsh are reachable.", [], commands)
    except Exception as exc:
        return EngineResult(False, f"KVM validation failed: {exc}", [], commands)


def discover_kvm(request: ConnectorRequest) -> EngineResult:
    commands = ["virsh list --all --name"]
    try:
        client = _ssh_client(request)
    except Exception as exc:
        return EngineResult(False, f"KVM discovery failed: {exc}", [], commands)
    records = []
    try:
        code, listed, error = _ssh_exec(client, "virsh list --all --name", timeout=20)
        if code:
            return EngineResult(False, f"KVM discovery failed: {error.strip()}", [], commands)
        for name in [line.strip() for line in listed.splitlines() if line.strip()]:
            command = f"virsh dominfo {name!r}; echo __BLK__; virsh domblklist {name!r}; echo __ADDR__; virsh domifaddr {name!r} || true"
            commands.append(command)
            code, text, _ = _ssh_exec(client, command)
            if code:
                continue
            records.append(
                {
                    "vm_name": name,
                    "source_platform": "KVM",
                    "cpu": _match_int(text, r"CPU\(s\):\s+(\d+)") or 0,
                    "memory_gb": round((_match_int(text, r"Max memory:\s+(\d+) KiB") or 0) / 1024 / 1024),
                    "disk_gb": 0,
                    "os_type": "Unknown",
                    "ip_address": _match_text(_section(text, "__ADDR__", None), r"ipv4\s+([0-9.]+)/"),
                    "current_status": "Discovered",
                    "power_state": _match_text(text, r"State:\s+(.+)") or "unknown",
                }
            )
        return EngineResult(True, f"Discovered {len(records)} KVM VMs", records, commands)
    finally:
        client.close()


def _vc_connect(request: ConnectorRequest):
    parsed = urlparse(request.endpoint if "://" in (request.endpoint or "") else f"https://{request.endpoint}")
    password = credential_from_env(request.credential_reference)
    if not parsed.hostname or not request.username or not password:
        raise ValueError("vCenter requires endpoint, username, and an available env: password reference")
    return SmartConnect(
        host=parsed.hostname,
        port=request.port or 443,
        user=request.username,
        pwd=password,
        sslContext=ssl._create_unverified_context(),
        connectionPoolTimeout=20,
    )


def validate_vcenter(request: ConnectorRequest) -> EngineResult:
    commands = ["pyVmomi SmartConnect", "Retrieve ServiceContent.About"]
    try:
        service_instance = _vc_connect(request)
        try:
            about = service_instance.RetrieveContent().about
            return EngineResult(True, f"vCenter connector validated. Connected to {about.fullName}.", [], commands)
        finally:
            Disconnect(service_instance)
    except Exception as exc:
        return EngineResult(False, f"vCenter validation failed: {exc}", [], commands)


def discover_vcenter(request: ConnectorRequest) -> EngineResult:
    commands = ["pyVmomi SmartConnect", "ContainerView VirtualMachine"]
    try:
        service_instance = _vc_connect(request)
    except Exception as exc:
        return EngineResult(False, f"vCenter discovery failed: {exc}", [], commands)
    records = []
    try:
        content = service_instance.RetrieveContent()
        view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
        try:
            for vm_obj in view.view[:500]:
                summary = vm_obj.summary
                config = summary.config
                guest = summary.guest
                disks = [
                    round((device.capacityInKB or 0) / 1024 / 1024)
                    for device in (vm_obj.config.hardware.device if vm_obj.config and vm_obj.config.hardware else [])
                    if isinstance(device, vim.vm.device.VirtualDisk)
                ]
                records.append(
                    {
                        "vm_name": config.name,
                        "source_platform": "VMware ESXi / vCenter",
                        "cpu": config.numCpu or 0,
                        "memory_gb": round((config.memorySizeMB or 0) / 1024),
                        "disk_gb": sum(disks),
                        "os_type": config.guestFullName or "Unknown",
                        "ip_address": guest.ipAddress,
                        "current_status": "Discovered",
                        "power_state": str(summary.runtime.powerState),
                    }
                )
        finally:
            view.Destroy()
        return EngineResult(True, f"Discovered {len(records)} vCenter VMs", records, commands)
    finally:
        Disconnect(service_instance)


def _nutanix_client(request: ConnectorRequest) -> tuple[httpx.Client, str]:
    if not request.endpoint or not request.username:
        raise ValueError("Nutanix AHV requires a Prism Central endpoint and username")
    password = credential_from_env(request.credential_reference)
    if not password:
        raise ValueError("Nutanix AHV requires an available env: password reference")
    base = request.endpoint.rstrip("/")
    if not base.startswith(("http://", "https://")):
        base = f"https://{base}:{request.port or 9440}"
    return httpx.Client(verify=False, auth=(request.username, password), timeout=30), base


def validate_nutanix(request: ConnectorRequest) -> EngineResult:
    commands = ["POST /api/nutanix/v3/vms/list"]
    try:
        client, base = _nutanix_client(request)
        with client:
            response = client.post(f"{base}/api/nutanix/v3/vms/list", json={"kind": "vm", "length": 1})
            response.raise_for_status()
        return EngineResult(True, "Nutanix AHV connector validated through Prism Central.", [], commands)
    except Exception as exc:
        return EngineResult(False, f"Nutanix AHV validation failed: {exc}", [], commands)


def discover_nutanix(request: ConnectorRequest) -> EngineResult:
    commands = ["POST /api/nutanix/v3/vms/list with offset pagination"]
    records = []
    try:
        client, base = _nutanix_client(request)
        with client:
            offset = 0
            while len(records) < 1000:
                response = client.post(f"{base}/api/nutanix/v3/vms/list", json={"kind": "vm", "offset": offset, "length": 200})
                response.raise_for_status()
                entities = response.json().get("entities", [])
                for entity in entities:
                    spec = entity.get("spec", {})
                    resources = spec.get("resources", {})
                    status_resources = entity.get("status", {}).get("resources", {})
                    nic_list = status_resources.get("nic_list") or []
                    ip_endpoints = (nic_list[0].get("ip_endpoint_list") or []) if nic_list else []
                    records.append(
                        {
                            "vm_name": spec.get("name") or entity.get("metadata", {}).get("uuid"),
                            "source_platform": "Nutanix AHV",
                            "cpu": resources.get("num_sockets", 0) * resources.get("num_vcpus_per_socket", 0),
                            "memory_gb": round(resources.get("memory_size_mib", 0) / 1024),
                            "disk_gb": 0,
                            "os_type": "Unknown",
                            "ip_address": ip_endpoints[0].get("ip") if ip_endpoints else None,
                            "current_status": "Discovered",
                            "power_state": status_resources.get("power_state", "unknown"),
                        }
                    )
                if len(entities) < 200:
                    break
                offset += len(entities)
        return EngineResult(True, f"Discovered {len(records)} Nutanix AHV VMs", records, commands)
    except Exception as exc:
        return EngineResult(False, f"Nutanix AHV discovery failed: {exc}", [], commands)


def _match_int(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else None


def _match_text(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else None


def _section(text: str, start: str, end: str | None) -> str:
    if start not in text:
        return ""
    value = text.split(start, 1)[1]
    return value.split(end, 1)[0] if end and end in value else value
