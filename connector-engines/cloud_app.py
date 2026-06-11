from __future__ import annotations

import json

import boto3
from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient
from fastapi import FastAPI
from google.cloud import compute_v1
from google.oauth2 import service_account

from common import ConnectorRequest, EngineResponse, EngineResult, credential_from_env

app = FastAPI(title="DS Shift Cloud Connector Engine", version="1.0")

PLATFORMS = [
    {"type": "Amazon Web Services", "tool": "AWS SDK for Python (Boto3)", "discovery": True},
    {"type": "Google Cloud Platform", "tool": "Google Cloud Compute Python SDK", "discovery": True},
    {"type": "Microsoft Azure", "tool": "Azure Identity and Compute Management SDKs", "discovery": True},
]

ALIASES = {
    "AWS": "Amazon Web Services",
    "Azure": "Microsoft Azure",
}


@app.get("/health")
def health():
    return {"status": "ok", "engine": "Cloud Connector Engine", "platforms": len(PLATFORMS)}


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
    connector_type = ALIASES.get(request.connector_type, request.connector_type)
    if connector_type == "Amazon Web Services":
        return discover_aws(request) if discovery else validate_aws(request)
    if connector_type == "Google Cloud Platform":
        return discover_gcp(request) if discovery else validate_gcp(request)
    if connector_type == "Microsoft Azure":
        return discover_azure(request) if discovery else validate_azure(request)
    return EngineResult(False, f"Unsupported cloud connector type: {request.connector_type}", [], [])


def _secret_json(request: ConnectorRequest) -> dict:
    value = credential_from_env(request.credential_reference)
    if not value:
        raise ValueError("An available env: credential reference containing JSON is required")
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("Credential environment value must contain valid JSON") from exc


def _aws_client(request: ConnectorRequest):
    secret = _secret_json(request)
    return boto3.client(
        "ec2",
        region_name=secret.get("region") or request.endpoint or "us-east-1",
        aws_access_key_id=secret.get("access_key_id"),
        aws_secret_access_key=secret.get("secret_access_key"),
        aws_session_token=secret.get("session_token"),
    )


def validate_aws(request: ConnectorRequest) -> EngineResult:
    commands = ["Boto3 EC2 describe_regions"]
    try:
        _aws_client(request).describe_regions(AllRegions=False, DryRun=False)
        return EngineResult(True, "AWS connector validated through the EC2 API.", [], commands)
    except Exception as exc:
        return EngineResult(False, f"AWS validation failed: {exc}", [], commands)


def discover_aws(request: ConnectorRequest) -> EngineResult:
    commands = ["Boto3 EC2 describe_instances paginator"]
    records = []
    try:
        client = _aws_client(request)
        for page in client.get_paginator("describe_instances").paginate():
            for reservation in page.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    name = next((tag["Value"] for tag in instance.get("Tags", []) if tag.get("Key") == "Name"), instance["InstanceId"])
                    records.append(
                        {
                            "vm_name": name,
                            "external_id": instance["InstanceId"],
                            "source_platform": "Amazon Web Services",
                            "cpu": instance.get("CpuOptions", {}).get("CoreCount", 0) * instance.get("CpuOptions", {}).get("ThreadsPerCore", 1),
                            "memory_gb": 0,
                            "disk_gb": 0,
                            "os_type": instance.get("PlatformDetails", "Unknown"),
                            "ip_address": instance.get("PrivateIpAddress"),
                            "current_status": "Discovered",
                            "power_state": instance.get("State", {}).get("Name", "unknown"),
                            "instance_id": instance["InstanceId"],
                        }
                    )
        return EngineResult(True, f"Discovered {len(records)} AWS EC2 instances", records, commands)
    except Exception as exc:
        return EngineResult(False, f"AWS discovery failed: {exc}", [], commands)


def _gcp_client(request: ConnectorRequest):
    secret = _secret_json(request)
    project = request.endpoint or secret.get("project_id")
    if not project:
        raise ValueError("GCP requires the project ID in Endpoint or service-account JSON")
    credentials = service_account.Credentials.from_service_account_info(secret)
    return compute_v1.InstancesClient(credentials=credentials), project


def validate_gcp(request: ConnectorRequest) -> EngineResult:
    commands = ["Google Compute InstancesClient aggregated_list"]
    try:
        client, project = _gcp_client(request)
        next(iter(client.aggregated_list(project=project, return_partial_success=True)), None)
        return EngineResult(True, "Google Cloud connector validated through the Compute Engine API.", [], commands)
    except Exception as exc:
        return EngineResult(False, f"Google Cloud validation failed: {exc}", [], commands)


def discover_gcp(request: ConnectorRequest) -> EngineResult:
    commands = ["Google Compute InstancesClient aggregated_list(return_partial_success=True)"]
    records = []
    try:
        client, project = _gcp_client(request)
        for zone, scoped_list in client.aggregated_list(project=project, return_partial_success=True):
            for instance in scoped_list.instances or []:
                records.append(
                    {
                        "vm_name": instance.name,
                        "external_id": str(instance.id),
                        "source_platform": "Google Cloud Platform",
                        "cpu": 0,
                        "memory_gb": 0,
                        "disk_gb": 0,
                        "os_type": "Unknown",
                        "ip_address": instance.network_interfaces[0].network_i_p if instance.network_interfaces else None,
                        "current_status": "Discovered",
                        "power_state": instance.status,
                        "zone": zone,
                        "instance_id": str(instance.id),
                    }
                )
        return EngineResult(True, f"Discovered {len(records)} Google Compute Engine instances", records, commands)
    except Exception as exc:
        return EngineResult(False, f"Google Cloud discovery failed: {exc}", [], commands)


def _azure_client(request: ConnectorRequest):
    secret = _secret_json(request)
    subscription_id = request.endpoint or secret.get("subscription_id")
    if not subscription_id:
        raise ValueError("Azure requires the subscription ID in Endpoint or credential JSON")
    credential = ClientSecretCredential(
        tenant_id=secret["tenant_id"],
        client_id=secret["client_id"],
        client_secret=secret["client_secret"],
    )
    return ComputeManagementClient(credential, subscription_id)


def validate_azure(request: ConnectorRequest) -> EngineResult:
    commands = ["Azure ComputeManagementClient virtual_machines.list_all"]
    try:
        next(iter(_azure_client(request).virtual_machines.list_all()), None)
        return EngineResult(True, "Azure connector validated through the Compute Management API.", [], commands)
    except Exception as exc:
        return EngineResult(False, f"Azure validation failed: {exc}", [], commands)


def discover_azure(request: ConnectorRequest) -> EngineResult:
    commands = ["Azure ComputeManagementClient virtual_machines.list_all"]
    records = []
    try:
        for vm in _azure_client(request).virtual_machines.list_all():
            records.append(
                {
                    "vm_name": vm.name,
                    "external_id": vm.id,
                    "source_platform": "Microsoft Azure",
                    "cpu": 0,
                    "memory_gb": 0,
                    "disk_gb": 0,
                    "os_type": getattr(vm.storage_profile.os_disk, "os_type", "Unknown"),
                    "ip_address": None,
                    "current_status": "Discovered",
                    "power_state": "unknown",
                    "location": vm.location,
                    "vm_id": vm.id,
                    "size": vm.hardware_profile.vm_size if vm.hardware_profile else None,
                }
            )
        return EngineResult(True, f"Discovered {len(records)} Azure virtual machines", records, commands)
    except Exception as exc:
        return EngineResult(False, f"Azure discovery failed: {exc}", [], commands)
