from __future__ import annotations

import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse

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
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or f"{command[0]} exited with status {completed.returncode}")
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


def compute_option(env: dict[str, str], compute_name: str) -> list[str]:
    host_path = run(["govc", "find", "-type", "h", "-name", compute_name], env=env, timeout=30)
    if host_path:
        return ["-host", host_path.splitlines()[0].strip()]
    cluster_path = run(["govc", "find", "-type", "c", "-name", compute_name], env=env, timeout=30)
    if cluster_path:
        return ["-cluster", cluster_path.splitlines()[0].strip()]
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
        remote_dir = f"DS-Shift/{request.vm_name}"
        command_log: list[str] = []
        imported_paths: list[str] = []
        for disk in request.disks:
            local_path = Path(disk.local_path)
            if not local_path.exists():
                raise RuntimeError(f"Converted disk is missing: {local_path}")
            command_log.append(f"govc import.vmdk {local_path} {remote_dir}")
            run(["govc", "import.vmdk", str(local_path), remote_dir], env=env, timeout=7200)
            imported_paths.append(f"{remote_dir}/{local_path.name}")
        create_command = [
            "govc",
            "vm.create",
            "-on=false",
            "-m",
            str(max(1, request.memory_bytes // 1024 // 1024)),
            "-c",
            str(max(1, request.cpu)),
            "-g",
            request.guest_os_id,
            "-net",
            request.target_connector.target_network,
            "-ds",
            request.target_connector.target_datastore,
            *compute_option(env, request.target_connector.target_compute_name),
            "-disk",
            imported_paths[0],
            request.vm_name,
        ]
        command_log.append(" ".join(create_command))
        try:
            run(create_command, env=env, timeout=300)
            for disk_path in imported_paths[1:]:
                attach_command = ["govc", "vm.disk.attach", "-vm", request.vm_name, "-link=false", "-disk", disk_path]
                command_log.append(" ".join(attach_command))
                run(attach_command, env=env, timeout=300)
            if request.power_on:
                power_command = ["govc", "vm.power", "-on", request.vm_name]
                command_log.append(" ".join(power_command))
                run(power_command, env=env, timeout=300)
        except Exception:
            rollback_vm(env, request.vm_name, remote_dir)
            raise
        return {"ok": True, "vm_name": request.vm_name, "disk_paths": imported_paths, "commands": command_log}
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
