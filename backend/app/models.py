"""API models — the frontend contract of SPEC §2."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

NodeType = Literal["shelly", "external", "unknown_shelly"]
EdgeStatus = Literal["ok", "disabled", "dangling", "unparsed"]


class Port(BaseModel):
    id: str
    label: str
    kind: str
    active: bool | None = None


class Node(BaseModel):
    id: str
    type: NodeType
    label: str
    model: str | None = None
    ip: str | None = None
    gen: int | None = None
    online: bool | None = None
    inputs: list[Port] = Field(default_factory=list)
    outputs: list[Port] = Field(default_factory=list)
    position: dict[str, float] | None = None


class Edge(BaseModel):
    id: str
    source: str
    sourcePort: str
    target: str
    targetPort: str
    command: str
    params: dict[str, Any] | None = None
    status: EdgeStatus
    rawUrl: str


class Graph(BaseModel):
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)


class ActionUrl(BaseModel):
    id: str
    position: int
    raw_url: str


class ActionSlot(BaseModel):
    id: str
    source_kind: str
    source_idx: int
    event: str
    native_key: str
    enabled: bool
    name: str | None = None
    label: str
    urls: list[ActionUrl] = Field(default_factory=list)


class Channel(BaseModel):
    id: str
    kind: str
    idx: int
    name: str | None = None
    label: str


class Device(BaseModel):
    id: str
    ip: str
    gen: int
    model: str
    name: str | None = None
    fw_version: str | None = None
    profile: str | None = None
    auth_required: bool = False
    online: bool = True
    first_seen: str
    last_seen: str
    channels: list[Channel] = Field(default_factory=list)
    slots: list[ActionSlot] = Field(default_factory=list)


class DeviceDetail(Device):
    raw_info: dict[str, Any] | None = None
    has_credentials: bool = False
    snapshot_count: int = 0
    last_snapshot_at: str | None = None


class Health(BaseModel):
    status: str
    version: str
    demo: bool


class ScanStarted(BaseModel):
    scan_id: str


class ScanStatus(BaseModel):
    status: Literal["running", "done", "error"]
    found: int = 0
    errors: list[str] = Field(default_factory=list)
    method: str
    started_at: str
    ended_at: str | None = None


class Credentials(BaseModel):
    username: str
    password: str


class LayoutEntry(BaseModel):
    node_id: str
    x: float
    y: float


class Ok(BaseModel):
    ok: bool = True


class ApiError(BaseModel):
    error: str
    detail: str | None = None
