from __future__ import annotations

import os

import httpx

from . import models

HOST_ENGINE_URL = os.getenv("HOST_CONNECTOR_ENGINE_URL", "http://host-connector-engine:8101")
CLOUD_ENGINE_URL = os.getenv("CLOUD_CONNECTOR_ENGINE_URL", "http://cloud-connector-engine:8102")

CONNECTOR_PLATFORMS = {
    "host": [
        {
            "type": "KVM",
            "tool": "Paramiko SSH and virsh",
            "endpoint_hint": "qemu+ssh://root@hostname/system",
            "credential_hint": "ssh-key:container or env:KVM_PASSWORD",
        },
        {
            "type": "VMware ESXi / vCenter",
            "tool": "VMware pyVmomi",
            "endpoint_hint": "https://vcenter.example.com/sdk",
            "credential_hint": "env:VCENTER_PASSWORD",
        },
        {
            "type": "Nutanix AHV",
            "tool": "Nutanix Prism Central v3 REST API",
            "endpoint_hint": "https://prism-central.example.com:9440",
            "credential_hint": "env:NUTANIX_PASSWORD",
        },
    ],
    "cloud": [
        {
            "type": "Amazon Web Services",
            "tool": "AWS SDK for Python (Boto3)",
            "endpoint_hint": "AWS region, for example us-east-1",
            "credential_hint": "env:AWS_CONNECTOR_CREDENTIALS",
        },
        {
            "type": "Google Cloud Platform",
            "tool": "Google Cloud Compute Python SDK",
            "endpoint_hint": "Google Cloud project ID",
            "credential_hint": "env:GCP_CONNECTOR_CREDENTIALS",
        },
        {
            "type": "Microsoft Azure",
            "tool": "Azure Identity and Compute Management SDKs",
            "endpoint_hint": "Azure subscription ID",
            "credential_hint": "env:AZURE_CONNECTOR_CREDENTIALS",
        },
    ],
}

TYPE_ALIASES = {
    "AWS": "Amazon Web Services",
    "Azure": "Microsoft Azure",
}


def normalize_connector_type(connector_type: str) -> str:
    return TYPE_ALIASES.get(connector_type, connector_type)


def validate_connector_platform(category: str, connector_type: str) -> str:
    normalized = normalize_connector_type(connector_type)
    allowed = {platform["type"] for platform in CONNECTOR_PLATFORMS.get(category, [])}
    if normalized not in allowed:
        raise ValueError(f"Unsupported {category} connector type: {connector_type}")
    return normalized


def connector_payload(connector: models.ConnectorProfile, credential_payload: dict | None = None) -> dict:
    payload = {
        "connector_type": normalize_connector_type(connector.connector_type),
        "endpoint": connector.endpoint,
        "port": connector.port,
        "username": connector.username,
        "credential_reference": connector.credential_reference,
        "environment": connector.environment,
    }
    if credential_payload:
        payload["credential_payload"] = credential_payload
    return payload


def call_connector_engine(connector: models.ConnectorProfile, operation: str, *, credential_payload: dict | None = None) -> dict:
    if connector.connector_category == "host":
        base_url = HOST_ENGINE_URL
    elif connector.connector_category == "cloud":
        base_url = CLOUD_ENGINE_URL
    else:
        raise ValueError(f"Unsupported connector category: {connector.connector_category}")
    with httpx.Client(timeout=120) as client:
        response = client.post(f"{base_url}/{operation}", json=connector_payload(connector, credential_payload))
        response.raise_for_status()
        return response.json()


def connector_engine_status() -> list[dict]:
    engines = [
        ("host", "Host Connector Engine", HOST_ENGINE_URL),
        ("cloud", "Cloud Connector Engine", CLOUD_ENGINE_URL),
    ]
    statuses = []
    with httpx.Client(timeout=5) as client:
        for category, name, url in engines:
            try:
                response = client.get(f"{url}/health")
                response.raise_for_status()
                payload = response.json()
                statuses.append({"category": category, "name": name, "status": payload.get("status", "unknown"), "platforms": payload.get("platforms", 0)})
            except Exception as exc:
                statuses.append({"category": category, "name": name, "status": "unavailable", "platforms": 0, "message": str(exc)})
    return statuses
