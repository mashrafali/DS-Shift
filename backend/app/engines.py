from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass
class EngineResult:
    ok: bool
    message: str
    records: list[dict]
    commands: list[str]
    raw: str = ""


def _run(command: list[str], timeout: int = 45, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, timeout=timeout, env=env, check=False)


def _ssh_target(endpoint: str | None, username: str | None) -> str:
    if not endpoint:
        raise ValueError("Connector endpoint is required")
    parsed = urlparse(endpoint)
    if parsed.scheme.startswith("qemu+ssh"):
        host = parsed.hostname or ""
        user = parsed.username or username or "root"
        return f"{user}@{host}" if host else endpoint.replace("qemu+ssh://", "").split("/")[0]
    if "@" in endpoint:
        return endpoint.split("/")[0]
    host = endpoint.replace("https://", "").replace("http://", "").split("/")[0].split(":")[0]
    user = username or "root"
    return f"{user}@{host}"


def discover_kvm(endpoint: str | None, username: str | None) -> EngineResult:
    if not shutil.which("ssh"):
        return EngineResult(False, "openssh-client is not installed in the backend container", [], ["ssh"])
    target = _ssh_target(endpoint, username)
    list_cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", target, "virsh list --all --name"]
    listed = _run(list_cmd)
    commands = [" ".join(list_cmd)]
    if listed.returncode != 0:
        return EngineResult(False, "KVM discovery failed. SSH key or virsh access is not available.", [], commands, listed.stderr + listed.stdout)
    records = []
    for name in [line.strip() for line in listed.stdout.splitlines() if line.strip()]:
        info_cmd = ["ssh", "-o", "BatchMode=yes", target, f"virsh dominfo {name!r}; echo __BLK__; virsh domblklist {name!r}; echo __IF__; virsh domiflist {name!r}"]
        result = _run(info_cmd)
        commands.append(" ".join(info_cmd))
        text = result.stdout
        cpu = _match_int(text, r"CPU\(s\):\s+(\d+)") or 0
        mem_kib = _match_int(text, r"Max memory:\s+(\d+) KiB") or _match_int(text, r"Used memory:\s+(\d+) KiB") or 0
        state = _match_text(text, r"State:\s+(.+)") or "unknown"
        disks = [line.split()[-1] for line in _section(text, "__BLK__", "__IF__").splitlines() if line.strip().startswith(("vd", "sd", "hd"))]
        nics = [line.split()[0:5] for line in _section(text, "__IF__", None).splitlines() if line.strip().startswith("vnet")]
        records.append(
            {
                "vm_name": name,
                "source_platform": "KVM",
                "cpu": cpu,
                "memory_gb": round(mem_kib / 1024 / 1024),
                "disk_gb": 0,
                "os_type": "Unknown",
                "ip_address": None,
                "current_status": "Discovered",
                "power_state": state,
                "disks": disks,
                "nics": nics,
            }
        )
    return EngineResult(True, f"Discovered {len(records)} KVM VMs", records, commands, listed.stdout)


def discover_vcenter(endpoint: str | None, username: str | None, credential_reference: str | None) -> EngineResult:
    if not shutil.which("govc"):
        return EngineResult(False, "govc is not installed in the backend container. Install govc or provide an engine image with govc.", [], ["govc find / -type m"])
    if not endpoint or not username:
        return EngineResult(False, "vCenter endpoint and username are required", [], [])
    password = _credential_from_env(credential_reference)
    if not password:
        return EngineResult(False, "vCenter password is not available. Use credential_reference env:VARIABLE_NAME for MVP execution.", [], [])
    env = {**os.environ, "GOVC_URL": endpoint, "GOVC_USERNAME": username, "GOVC_PASSWORD": password, "GOVC_INSECURE": "1"}
    find_cmd = ["govc", "find", "/", "-type", "m"]
    found = _run(find_cmd, timeout=60, env=env)
    commands = ["GOVC_URL=<redacted> GOVC_USERNAME=<redacted> govc find / -type m"]
    if found.returncode != 0:
        return EngineResult(False, "vCenter discovery failed through govc", [], commands, found.stderr + found.stdout)
    records = []
    for path in [line.strip() for line in found.stdout.splitlines() if line.strip()][:100]:
        info_cmd = ["govc", "vm.info", "-json", path]
        info = _run(info_cmd, timeout=30, env=env)
        commands.append(f"govc vm.info -json {path!r}")
        if info.returncode != 0:
            continue
        try:
            payload = json.loads(info.stdout)
            vm = payload["VirtualMachines"][0]
            config = vm.get("Config", {})
            runtime = vm.get("Runtime", {})
            records.append(
                {
                    "vm_name": config.get("Name") or path.rsplit("/", 1)[-1],
                    "source_platform": "VMware ESXi / vCenter",
                    "cpu": config.get("Hardware", {}).get("NumCPU") or 0,
                    "memory_gb": round((config.get("Hardware", {}).get("MemoryMB") or 0) / 1024),
                    "disk_gb": 0,
                    "os_type": config.get("GuestFullName") or "Unknown",
                    "ip_address": vm.get("Guest", {}).get("IpAddress"),
                    "current_status": "Discovered",
                    "power_state": runtime.get("PowerState"),
                    "path": path,
                }
            )
        except (KeyError, json.JSONDecodeError):
            continue
    return EngineResult(True, f"Discovered {len(records)} vCenter VMs", records, commands, found.stdout)


def build_kvm_to_esxi_preflight(source_endpoint: str | None, target_endpoint: str | None, vm_name: str, target_datastore: str | None) -> EngineResult:
    required = ["ssh", "virt-v2v", "qemu-img", "govc"]
    missing = [tool for tool in required if not shutil.which(tool)]
    source_uri = source_endpoint or "qemu+ssh://root@kvm/system"
    datastore = target_datastore or "<target-vmware-datastore>"
    commands = [
        "ssh <kvm-host> virsh domstate <vm-name>",
        "ssh <kvm-host> virsh domblklist <vm-name>",
        "virt-v2v -ic qemu+ssh://<kvm-host>/system <vm-name> -o vpx -os <datastore> -op <password-file>",
    ]
    runbook = [
        {
            "step": "Validate source VM state and disks",
            "command": f"virsh -c {source_uri} domblklist {vm_name}",
        },
        {
            "step": "Run conversion to VMware vCenter/ESXi",
            "command": f"virt-v2v -ic {source_uri} {vm_name} -o vpx -os {datastore} -op /run/secrets/vcenter-password",
        },
        {
            "step": "Validate target inventory",
            "command": f"govc vm.info {vm_name}",
        },
    ]
    if missing:
        return EngineResult(False, f"Migration preflight blocked. Missing tools: {', '.join(missing)}", runbook, commands)
    return EngineResult(True, "Migration preflight passed locally. Live execution still requires explicit approval and target credentials.", runbook, commands)


def _credential_from_env(reference: str | None) -> str | None:
    if reference and reference.startswith("env:"):
        return os.getenv(reference.split(":", 1)[1])
    return None


def _match_int(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else None


def _match_text(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else None


def _section(text: str, start: str, end: str | None) -> str:
    if start not in text:
        return ""
    section = text.split(start, 1)[1]
    if end and end in section:
        section = section.split(end, 1)[0]
    return section
