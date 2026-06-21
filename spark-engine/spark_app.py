from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
import json
import logging
import os
import shlex
import socket
import threading
import time
import uuid
from urllib.parse import urlparse

import boto3
import httpx
from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.compute.models import (
    CreationData,
    DataDisk,
    Disk,
    HardwareProfile,
    ManagedDiskParameters,
    NetworkInterfaceReference,
    NetworkProfile,
    OSDisk,
    Snapshot,
    StorageProfile,
    VirtualMachine,
)
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.network.models import NetworkInterface, NetworkInterfaceIPConfiguration
from fastapi import FastAPI, HTTPException
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account
import paramiko
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text, create_engine, select, text, update

from host_migrations import (
    execute_kvm_to_vcenter,
    execute_vcenter_to_kvm,
    preflight_kvm_to_vcenter,
    preflight_vcenter_to_kvm,
)


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://dsshift:dsshift@database:5432/dsshift")
LIVE_EXECUTION_ENABLED = os.getenv("SPARK_LIVE_EXECUTION_ENABLED", "true").lower() == "true"
WORKER_ID = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
POLL_SECONDS = float(os.getenv("SPARK_POLL_SECONDS", "2"))
LAUNCHGRID_URL = os.getenv("LAUNCHGRID_URL", "http://launchgrid:8300")
logger = logging.getLogger("spark-engine")

metadata = MetaData()
jobs = Table(
    "spark_execution_jobs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("plan_id", Integer, nullable=False, index=True),
    Column("status", String(40), nullable=False, default="Queued", index=True),
    Column("adapter", String(80), nullable=False),
    Column("requested_by", String(80), nullable=False),
    Column("worker_id", String(160)),
    Column("request_json", Text, nullable=False),
    Column("result_json", Text, nullable=False, default="{}"),
    Column("message", Text),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
    Column("started_at", DateTime),
    Column("completed_at", DateTime),
)
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
stop_event = threading.Event()


CAPABILITIES = [
    {
        "source": "Amazon Web Services",
        "target": "Amazon Web Services",
        "adapter": "aws-ec2-copy",
        "live": True,
        "required_options": [],
        "notes": "Creates an AMI, optionally copies it to another region in the same AWS account, then launches a target EC2 instance.",
    },
    {
        "source": "Google Cloud Platform",
        "target": "Google Cloud Platform",
        "adapter": "gcp-machine-image",
        "live": True,
        "required_options": ["target_zone"],
        "notes": "Creates a Compute Engine machine image and launches a target instance from it.",
    },
    {
        "source": "Microsoft Azure",
        "target": "Microsoft Azure",
        "adapter": "azure-managed-disk-clone",
        "live": True,
        "required_options": ["target_resource_group", "target_subnet_id"],
        "notes": "Clones managed disks through snapshots, creates a NIC, and creates a target VM in the same subscription.",
    },
    {
        "source": "KVM",
        "target": "KVM",
        "adapter": "kvm-libvirt-migrate",
        "live": True,
        "required_options": [],
        "notes": "Runs a libvirt peer-to-peer migration from the source KVM host to the target KVM URI.",
    },
    {
        "source": "KVM",
        "target": "VMware ESXi / vCenter",
        "adapter": "kvm-vcenter-ova",
        "live": True,
        "required_options": ["target_datastore", "target_network", "target_vdc_name", "target_compute_name"],
        "notes": "Copies powered-off file-backed KVM disks into host staging, converts them to stream-optimized VMDK, and hands them to LaunchGrid for VMware provisioning.",
    },
    {
        "source": "VMware ESXi / vCenter",
        "target": "KVM",
        "adapter": "vcenter-kvm-virt-v2v",
        "live": True,
        "required_options": ["target_storage_pool"],
        "notes": "Converts a powered-off vCenter VM with virt-v2v, transfers the generated qcow2 disks to the target KVM storage pool, and defines the libvirt domain.",
    },
]


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


class Workload(BaseModel):
    id: int
    vm_name: str
    external_id: str | None = None
    host_name: str | None = None
    details: dict = Field(default_factory=dict)


class JobRequest(BaseModel):
    plan_id: int
    plan_name: str | None = None
    source_connector: Connector
    target_connector: Connector
    workloads: list[Workload]
    options: dict = Field(default_factory=dict)
    keep_source_vm: bool = True
    requested_by: str
    live: bool = True
    approval: str


class JobProgressReporter:
    def __init__(self, job_id: int):
        self.job_id = job_id

    def task(
        self,
        key: str,
        title: str,
        status: str,
        progress: int,
        message: str,
        *,
        details: dict | None = None,
    ) -> list[dict]:
        with engine.begin() as connection:
            entries = _job_entries(connection, self.job_id)
            now = datetime.utcnow().isoformat()
            existing = next((entry for entry in entries if entry.get("kind") == "task" and entry.get("key") == key), None)
            if existing:
                existing.update({"title": title, "status": status, "progress": progress, "message": message})
                if details is not None:
                    existing["details"] = details
            else:
                existing = {
                    "kind": "task",
                    "key": key,
                    "title": title,
                    "status": status,
                    "progress": progress,
                    "message": message,
                    "started_at": now,
                }
                if details is not None:
                    existing["details"] = details
                entries.append(existing)
            if status in {"Succeeded", "Failed"}:
                existing["completed_at"] = now
            connection.execute(
                update(jobs)
                .where(jobs.c.id == self.job_id)
                .values(result_json=json.dumps(entries), message=message)
            )
            return entries

    def finalize(self, vm_results: list[dict]) -> list[dict]:
        with engine.begin() as connection:
            entries = _job_entries(connection, self.job_id)
            non_vm_entries = [entry for entry in entries if entry.get("kind") != "vm_result"]
            merged = non_vm_entries + [{**row, "kind": row.get("kind", "vm_result")} for row in vm_results]
            connection.execute(
                update(jobs)
                .where(jobs.c.id == self.job_id)
                .values(result_json=json.dumps(merged))
            )
            return merged


def adapter_for(source: str, target: str) -> dict | None:
    return next((row for row in CAPABILITIES if row["source"] == source and row["target"] == target), None)


def _job_entries(connection, job_id: int) -> list[dict]:
    payload = connection.execute(select(jobs.c.result_json).where(jobs.c.id == job_id)).scalar_one()
    if not payload:
        return []
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def current_task_progress(job_id: int) -> int:
    with engine.connect() as connection:
        entries = _job_entries(connection, job_id)
    return max(
        (
            int(task.get("progress", 0))
            for task in entries
            if isinstance(task, dict) and task.get("kind") == "task" and task.get("key") not in {"failed", "complete"} and task.get("status") != "Failed"
        ),
        default=0,
    )


def row_dict(row) -> dict:
    result = dict(row._mapping)
    for key in ("created_at", "started_at", "completed_at"):
        if result.get(key):
            result[key] = result[key].isoformat()
    if result.get("result_json"):
        result["result"] = json.loads(result["result_json"])
    else:
        result["result"] = []
    tasks = [entry for entry in result["result"] if isinstance(entry, dict) and entry.get("kind") == "task"]
    result["tasks"] = tasks
    result["vm_results"] = [entry for entry in result["result"] if isinstance(entry, dict) and entry.get("kind") != "task"]
    if result.get("status") == "Succeeded":
        progress = 100
    elif result.get("status") == "Failed":
        progress = max(
            (
                int(task.get("progress", 0))
                for task in tasks
                if task.get("key") not in {"failed", "complete"} and task.get("status") != "Failed"
            ),
            default=max((int(task.get("progress", 0)) for task in tasks if task.get("key") != "failed"), default=0),
        )
    else:
        progress = max((int(task.get("progress", 0)) for task in tasks), default=0)
    result["progress_percent"] = min(100, max(0, progress))
    result.pop("request_json", None)
    result.pop("result_json", None)
    return result


@asynccontextmanager
async def lifespan(_app: FastAPI):
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_lock(84752001)"))
        try:
            metadata.create_all(connection)
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(84752001)"))
    worker = threading.Thread(target=worker_loop, name=f"spark-worker-{WORKER_ID}", daemon=True)
    worker.start()
    yield
    stop_event.set()
    worker.join(timeout=5)


app = FastAPI(title="DS Shift Spark Engine", version="0.1", lifespan=lifespan)


@app.get("/health")
def health():
    with engine.connect() as connection:
        connection.execute(select(jobs.c.id).limit(1))
    return {
        "status": "ok",
        "engine": "Spark Engine",
        "worker_id": WORKER_ID,
        "live_execution_enabled": LIVE_EXECUTION_ENABLED,
        "capabilities": len(CAPABILITIES),
    }


@app.get("/capabilities")
def capabilities():
    return {"live_execution_enabled": LIVE_EXECUTION_ENABLED, "capabilities": CAPABILITIES}


@app.post("/jobs", status_code=202)
def create_job(request: JobRequest):
    capability = adapter_for(request.source_connector.connector_type, request.target_connector.connector_type)
    if not capability:
        raise HTTPException(400, "No Spark Engine adapter is available for this source and target combination")
    if not capability["live"]:
        raise HTTPException(409, capability["notes"])
    if not request.workloads:
        raise HTTPException(400, "At least one workload is required")
    if not request.live or not LIVE_EXECUTION_ENABLED:
        raise HTTPException(409, "Spark live execution is disabled by SPARK_LIVE_EXECUTION_ENABLED")
    if request.approval != f"EXECUTE:{request.plan_id}":
        raise HTTPException(400, "Invalid live execution approval")
    missing = [key for key in capability["required_options"] if not request.options.get(key)]
    if missing:
        raise HTTPException(400, f"Missing execution options: {', '.join(missing)}")
    with engine.begin() as connection:
        job_id = connection.execute(
            jobs.insert().values(
                plan_id=request.plan_id,
                status="Queued",
                adapter=capability["adapter"],
                requested_by=request.requested_by,
                request_json=request.model_dump_json(),
                result_json="{}",
                created_at=datetime.utcnow(),
            ).returning(jobs.c.id)
        ).scalar_one()
        row = connection.execute(select(jobs).where(jobs.c.id == job_id)).one()
    return row_dict(row)


@app.post("/preflight")
def preflight(request: JobRequest):
    capability = adapter_for(request.source_connector.connector_type, request.target_connector.connector_type)
    if not capability:
        raise HTTPException(400, "No Spark Engine adapter is available for this source and target combination")
    if not request.workloads:
        raise HTTPException(400, "At least one workload is required")
    missing = [key for key in capability["required_options"] if not request.options.get(key)]
    if missing:
        raise HTTPException(400, f"Missing execution options: {', '.join(missing)}")
    if capability["adapter"] == "kvm-vcenter-ova":
        checks = preflight_kvm_to_vcenter(request)
    elif capability["adapter"] == "vcenter-kvm-virt-v2v":
        checks = preflight_vcenter_to_kvm(request)
    else:
        checks = [{"check": "adapter", "ok": True, "message": f"{capability['adapter']} is available"}]
    return {
        "ok": bool(checks) and all(check["ok"] for check in checks),
        "adapter": capability["adapter"],
        "checks": checks,
        "live_execution_enabled": LIVE_EXECUTION_ENABLED,
    }


@app.get("/jobs/{job_id}")
def get_job(job_id: int):
    with engine.connect() as connection:
        row = connection.execute(select(jobs).where(jobs.c.id == job_id)).first()
    if not row:
        raise HTTPException(404, "Spark execution job not found")
    return row_dict(row)


def worker_loop() -> None:
    while not stop_event.wait(POLL_SECONDS):
        try:
            claimed = claim_job()
            if not claimed:
                continue
            execute_job(claimed)
        except Exception:
            logger.exception("Spark worker loop failed")
            time.sleep(POLL_SECONDS)


def claim_job() -> dict | None:
    with engine.begin() as connection:
        row = connection.execute(
            select(jobs)
            .where(jobs.c.status == "Queued")
            .order_by(jobs.c.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        ).first()
        if not row:
            return None
        connection.execute(
            update(jobs)
            .where(jobs.c.id == row.id)
            .values(status="Running", worker_id=WORKER_ID, started_at=datetime.utcnow(), message="Spark Engine worker claimed the job")
        )
        return dict(row._mapping)


def execute_job(job: dict) -> None:
    request = JobRequest.model_validate_json(job["request_json"])
    reporter = JobProgressReporter(job["id"])
    try:
        reporter.task("prepare", "Prepare execution", "Running", 5, f"Starting {job['adapter']} for {len(request.workloads)} workload(s)")
        if job["adapter"] == "aws-ec2-copy":
            result = execute_aws(request)
        elif job["adapter"] == "gcp-machine-image":
            result = execute_gcp(request)
        elif job["adapter"] == "azure-managed-disk-clone":
            result = execute_azure(request)
        elif job["adapter"] == "kvm-libvirt-migrate":
            result = execute_kvm(request)
        elif job["adapter"] == "kvm-vcenter-ova":
            result = execute_kvm_to_vcenter(request, reporter=reporter)
        elif job["adapter"] == "vcenter-kvm-virt-v2v":
            result = execute_vcenter_to_kvm(request, reporter=reporter)
        else:
            raise RuntimeError(f"Unknown Spark adapter: {job['adapter']}")
        failed_results = [row for row in result if row.get("ok") is False]
        if failed_results:
            message = failed_results[0].get("message") or f"Spark Engine failed {len(failed_results)} workload migration(s)"
            reporter.task("failed", "Execution failed", "Failed", current_task_progress(job["id"]), message)
            merged_result = reporter.finalize(result)
            status = "Failed"
        else:
            message = f"Spark Engine completed {len(result)} workload migration(s)"
            reporter.task("complete", "Finalize execution", "Succeeded", 100, message)
            merged_result = reporter.finalize(result)
            status = "Succeeded"
    except Exception as exc:
        reporter.task("failed", "Execution failed", "Failed", current_task_progress(job["id"]), str(exc))
        merged_result = reporter.finalize([{"kind": "vm_result", "ok": False, "message": str(exc)}])
        status = "Failed"
        message = str(exc)
    with engine.begin() as connection:
        connection.execute(
            update(jobs)
            .where(jobs.c.id == job["id"])
            .values(status=status, result_json=json.dumps(merged_result), message=message, completed_at=datetime.utcnow())
        )


def secret_json(connector: Connector) -> dict:
    if connector.credential_payload:
        return connector.credential_payload
    reference = connector.credential_reference or ""
    if not reference.startswith("env:"):
        raise RuntimeError(f"{connector.name} must use an env: credential reference")
    value = os.getenv(reference.split(":", 1)[1])
    if not value:
        raise RuntimeError(f"Credential environment variable for {connector.name} is unavailable")
    return json.loads(value)


def aws_client(connector: Connector, service: str, region: str | None = None):
    secret = secret_json(connector)
    return boto3.client(
        service,
        region_name=region or secret.get("region") or connector.endpoint or "us-east-1",
        aws_access_key_id=secret.get("access_key_id"),
        aws_secret_access_key=secret.get("secret_access_key"),
        aws_session_token=secret.get("session_token"),
    )


def execute_aws(request: JobRequest) -> list[dict]:
    source_region = request.options.get("source_region") or secret_json(request.source_connector).get("region") or request.source_connector.endpoint
    target_region = request.options.get("target_region") or secret_json(request.target_connector).get("region") or request.target_connector.endpoint
    source_ec2 = aws_client(request.source_connector, "ec2", source_region)
    target_ec2 = aws_client(request.target_connector, "ec2", target_region)
    source_account = aws_client(request.source_connector, "sts", source_region).get_caller_identity()["Account"]
    target_account = aws_client(request.target_connector, "sts", target_region).get_caller_identity()["Account"]
    if source_account != target_account:
        raise RuntimeError("AWS cross-account execution requires AMI and snapshot sharing and is not enabled in this adapter")
    results = []
    for workload in request.workloads:
        instance_id = workload.external_id
        if not instance_id:
            raise RuntimeError(f"{workload.vm_name} has no AWS instance ID")
        source_instance = source_ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]
        image_name = f"ds-shift-{workload.vm_name}-{int(time.time())}".replace("_", "-")[:128]
        image_id = source_ec2.create_image(
            InstanceId=instance_id,
            Name=image_name,
            NoReboot=bool(request.options.get("no_reboot", False)),
            TagSpecifications=[{"ResourceType": "image", "Tags": [{"Key": "CreatedBy", "Value": "DS Shift Spark Engine"}]}],
        )["ImageId"]
        source_ec2.get_waiter("image_available").wait(ImageIds=[image_id], WaiterConfig={"Delay": 15, "MaxAttempts": 120})
        target_image_id = image_id
        if source_region != target_region:
            target_image_id = target_ec2.copy_image(SourceRegion=source_region, SourceImageId=image_id, Name=image_name)["ImageId"]
            target_ec2.get_waiter("image_available").wait(ImageIds=[target_image_id], WaiterConfig={"Delay": 15, "MaxAttempts": 120})
        launch = {
            "ImageId": target_image_id,
            "InstanceType": request.options.get("target_instance_type") or source_instance["InstanceType"],
            "MinCount": 1,
            "MaxCount": 1,
            "TagSpecifications": [{"ResourceType": "instance", "Tags": [{"Key": "Name", "Value": request.options.get("target_name") or f"{workload.vm_name}-migrated"}, {"Key": "CreatedBy", "Value": "DS Shift Spark Engine"}]}],
        }
        if request.options.get("target_subnet_id"):
            launch["SubnetId"] = request.options["target_subnet_id"]
        if request.options.get("security_group_ids"):
            launch["SecurityGroupIds"] = request.options["security_group_ids"]
        if request.options.get("key_name"):
            launch["KeyName"] = request.options["key_name"]
        target_instance = target_ec2.run_instances(**launch)["Instances"][0]["InstanceId"]
        results.append({"ok": True, "vm_id": workload.id, "vm_name": workload.vm_name, "source_id": instance_id, "image_id": target_image_id, "target_id": target_instance})
    return results


def gcp_session(connector: Connector) -> tuple[AuthorizedSession, str]:
    secret = secret_json(connector)
    project = connector.endpoint or secret.get("project_id")
    credentials = service_account.Credentials.from_service_account_info(secret, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return AuthorizedSession(credentials), project


def wait_gcp(session: AuthorizedSession, operation_url: str, timeout: int = 3600) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = session.get(operation_url)
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") == "DONE":
            if payload.get("error"):
                raise RuntimeError(json.dumps(payload["error"]))
            return payload
        time.sleep(5)
    raise RuntimeError("Timed out waiting for Google Compute Engine operation")


def execute_gcp(request: JobRequest) -> list[dict]:
    source_session, source_project = gcp_session(request.source_connector)
    target_session, target_project = gcp_session(request.target_connector)
    target_zone = request.options["target_zone"]
    results = []
    for workload in request.workloads:
        source_zone = request.options.get("source_zone") or workload.details.get("zone", "").split("/")[-1]
        if not source_zone:
            raise RuntimeError(f"{workload.vm_name} requires source_zone")
        image_name = f"ds-shift-{workload.vm_name}-{int(time.time())}".lower().replace("_", "-")[:63]
        create_image = source_session.post(
            f"https://compute.googleapis.com/compute/v1/projects/{source_project}/global/machineImages",
            json={"name": image_name, "sourceInstance": f"projects/{source_project}/zones/{source_zone}/instances/{workload.vm_name}"},
        )
        create_image.raise_for_status()
        operation = create_image.json()
        wait_gcp(source_session, f"https://compute.googleapis.com/compute/v1/projects/{source_project}/global/operations/{operation['name']}")
        target_name = request.options.get("target_name") or f"{workload.vm_name}-migrated"
        create_instance = target_session.post(
            f"https://compute.googleapis.com/compute/v1/projects/{target_project}/zones/{target_zone}/instances",
            json={
                "name": target_name,
                "sourceMachineImage": f"projects/{source_project}/global/machineImages/{image_name}",
            },
        )
        create_instance.raise_for_status()
        target_operation = create_instance.json()
        wait_gcp(target_session, f"https://compute.googleapis.com/compute/v1/projects/{target_project}/zones/{target_zone}/operations/{target_operation['name']}")
        results.append({"ok": True, "vm_id": workload.id, "vm_name": workload.vm_name, "machine_image": image_name, "target_id": f"projects/{target_project}/zones/{target_zone}/instances/{target_name}"})
    return results


def azure_clients(connector: Connector):
    secret = secret_json(connector)
    subscription_id = connector.endpoint or secret.get("subscription_id")
    credential = ClientSecretCredential(tenant_id=secret["tenant_id"], client_id=secret["client_id"], client_secret=secret["client_secret"])
    return ComputeManagementClient(credential, subscription_id), NetworkManagementClient(credential, subscription_id), subscription_id


def resource_group(resource_id: str) -> str:
    parts = resource_id.split("/")
    return parts[parts.index("resourceGroups") + 1]


def execute_azure(request: JobRequest) -> list[dict]:
    source_compute, _, source_subscription = azure_clients(request.source_connector)
    target_compute, target_network, target_subscription = azure_clients(request.target_connector)
    if source_subscription != target_subscription:
        raise RuntimeError("Azure cross-subscription execution is not enabled; disk copy through an approved staging path is required")
    target_rg = request.options["target_resource_group"]
    subnet_id = request.options["target_subnet_id"]
    results = []
    for workload in request.workloads:
        if not workload.external_id:
            raise RuntimeError(f"{workload.vm_name} has no Azure resource ID")
        source_rg = resource_group(workload.external_id)
        source_vm = source_compute.virtual_machines.get(source_rg, workload.vm_name)
        location = request.options.get("target_location") or source_vm.location
        target_name = request.options.get("target_name") or f"{workload.vm_name}-migrated"
        disk_specs = [("os", source_vm.storage_profile.os_disk.managed_disk.id, None)]
        disk_specs.extend(("data", disk.managed_disk.id, disk.lun) for disk in source_vm.storage_profile.data_disks)
        cloned = []
        for kind, disk_id, lun in disk_specs:
            source_disk_rg = resource_group(disk_id)
            source_disk_name = disk_id.rsplit("/", 1)[-1]
            source_disk = source_compute.disks.get(source_disk_rg, source_disk_name)
            suffix = "os" if kind == "os" else f"data-{lun}"
            snapshot_name = f"{target_name}-{suffix}-snapshot"
            snapshot = target_compute.snapshots.begin_create_or_update(
                target_rg,
                snapshot_name,
                Snapshot(location=location, creation_data=CreationData(create_option="Copy", source_resource_id=source_disk.id)),
            ).result()
            disk_name = f"{target_name}-{suffix}"
            disk = target_compute.disks.begin_create_or_update(
                target_rg,
                disk_name,
                Disk(location=location, creation_data=CreationData(create_option="Copy", source_resource_id=snapshot.id), sku=source_disk.sku),
            ).result()
            cloned.append((kind, disk, lun))
        nic_name = f"{target_name}-nic"
        nic = target_network.network_interfaces.begin_create_or_update(
            target_rg,
            nic_name,
            NetworkInterface(
                location=location,
                ip_configurations=[NetworkInterfaceIPConfiguration(name="ipconfig1", subnet={"id": subnet_id})],
            ),
        ).result()
        os_disk = next(disk for kind, disk, _ in cloned if kind == "os")
        data_disks = [
            DataDisk(lun=lun, create_option="Attach", managed_disk=ManagedDiskParameters(id=disk.id))
            for kind, disk, lun in cloned
            if kind == "data"
        ]
        target_vm = target_compute.virtual_machines.begin_create_or_update(
            target_rg,
            target_name,
            VirtualMachine(
                location=location,
                hardware_profile=HardwareProfile(vm_size=request.options.get("target_instance_type") or source_vm.hardware_profile.vm_size),
                storage_profile=StorageProfile(
                    os_disk=OSDisk(
                        name=os_disk.name,
                        create_option="Attach",
                        os_type=source_vm.storage_profile.os_disk.os_type,
                        managed_disk=ManagedDiskParameters(id=os_disk.id),
                    ),
                    data_disks=data_disks,
                ),
                network_profile=NetworkProfile(network_interfaces=[NetworkInterfaceReference(id=nic.id, primary=True)]),
            ),
        ).result()
        results.append({"ok": True, "vm_id": workload.id, "vm_name": workload.vm_name, "target_id": target_vm.id, "target_nic_id": nic.id})
    return results


def ssh_parts(connector: Connector) -> tuple[str, int, str]:
    parsed = urlparse(connector.endpoint or "")
    if parsed.scheme.startswith("qemu+ssh"):
        return parsed.hostname or "", parsed.port or connector.port or 22, parsed.username or connector.username or "root"
    host = (connector.endpoint or "").replace("https://", "").replace("http://", "").split("/")[0].split(":")[0]
    return host, connector.port or 22, connector.username or "root"


def execute_kvm(request: JobRequest) -> list[dict]:
    host, port, username = ssh_parts(request.source_connector)
    password = request.source_connector.credential_payload.get("password")
    if password is None:
        reference = request.source_connector.credential_reference or ""
        if reference.startswith("env:"):
            password = os.getenv(reference.split(":", 1)[1])
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=host, port=port, username=username, password=password, look_for_keys=not bool(password), allow_agent=not bool(password))
    results = []
    try:
        target_uri = request.target_connector.endpoint
        if not target_uri:
            raise RuntimeError("Target KVM connector URI is required")
        for workload in request.workloads:
            flags = ["--persistent", "--copy-storage-all"]
            if request.options.get("live", True):
                flags.insert(0, "--live")
            if request.options.get("undefine_source", False):
                flags.append("--undefinesource")
            command = "virsh migrate " + " ".join(flags) + f" {shlex.quote(workload.vm_name)} {shlex.quote(target_uri)}"
            _, stdout, stderr = client.exec_command(command, timeout=int(request.options.get("timeout", 3600)))
            code = stdout.channel.recv_exit_status()
            output = stdout.read().decode(errors="replace")
            error = stderr.read().decode(errors="replace")
            if code:
                raise RuntimeError(f"KVM migration failed for {workload.vm_name}: {error or output}")
            results.append({"ok": True, "vm_id": workload.id, "vm_name": workload.vm_name, "target_uri": target_uri, "message": output.strip() or "libvirt migration completed"})
    finally:
        client.close()
    return results
