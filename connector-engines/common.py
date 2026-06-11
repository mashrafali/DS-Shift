from __future__ import annotations

import os
from dataclasses import dataclass, field

from pydantic import BaseModel, Field


class ConnectorRequest(BaseModel):
    connector_type: str
    endpoint: str | None = None
    port: int | None = None
    username: str | None = None
    credential_reference: str | None = None
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
