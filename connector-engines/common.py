from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from pydantic import BaseModel, Field


class ConnectorRequest(BaseModel):
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
    environment: str | None = None


class EngineResponse(BaseModel):
    ok: bool
    message: str
    records: list[dict] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    hosts: list[dict] = Field(default_factory=list)


@dataclass
class EngineResult:
    ok: bool
    message: str
    records: list[dict]
    commands: list[str]
    hosts: list[dict] = field(default_factory=list)


def credential_from_env(reference: str | None) -> str | None:
    if not reference or not reference.startswith("env:"):
        return None
    return os.getenv(reference.split(":", 1)[1])


def password_value(request: ConnectorRequest) -> str | None:
    if request.credential_payload.get("password"):
        return str(request.credential_payload["password"])
    return credential_from_env(request.credential_reference)


def credential_json(request: ConnectorRequest) -> dict:
    if request.credential_payload:
        return request.credential_payload
    value = credential_from_env(request.credential_reference)
    if not value:
        raise ValueError("An available connector credential is required")
    return json.loads(value)
