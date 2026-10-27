"""API models — the frontend contract of SPEC §2."""

from __future__ import annotations

from typing import Annotated, Any, Literal

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
    # Commands the guided builder can emit for this channel, each with the
    # parameters it accepts on this device's generation (SPEC §4).
    commands: dict[str, list[str]] = Field(default_factory=dict)


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


# --- Phase 2: the guided action builder (SPEC §4, §5.1) ----------------------


class UrlParse(BaseModel):
    """What the resolver makes of one URL, for the form and the URL list."""

    raw_url: str
    target_type: str
    status: str
    command: str
    label: str
    params: dict[str, Any] = Field(default_factory=dict)
    target_device_id: str | None = None
    target_device_name: str | None = None
    channel_id: str | None = None
    channel_label: str | None = None
    external_host: str | None = None
    external_path: str | None = None
    webhook_id: str | None = None
    summary: str


class SlotUrl(ActionUrl):
    parsed: UrlParse


class SlotDetail(BaseModel):
    id: str
    # Changes whenever the stored slot does; the editor sends it back on save.
    revision: str = ""
    device_id: str
    device_name: str | None = None
    device_gen: int
    source_kind: str
    source_idx: int
    event: str
    native_key: str
    enabled: bool
    name: str | None = None
    label: str
    urls: list[SlotUrl] = Field(default_factory=list)


class BuildRequest(BaseModel):
    target_device_id: str
    channel_id: str
    command: str
    params: dict[str, Any] = Field(default_factory=dict)


class BuiltUrlResponse(BaseModel):
    url: str
    command: str
    channel_id: str
    channel_kind: str
    channel_idx: int
    params: dict[str, Any] = Field(default_factory=dict)
    summary: str


class ParseRequest(BaseModel):
    raw_url: str


class BuiltUrlSpec(BaseModel):
    """The server generates the URL; the client never composes one (SPEC §4)."""

    kind: Literal["built"]
    target_device_id: str
    channel_id: str
    command: str
    params: dict[str, Any] = Field(default_factory=dict)


class HaWebhookSpec(BaseModel):
    kind: Literal["ha_webhook"]
    webhook_id: str


class RawExternalSpec(BaseModel):
    """Only allowed for hosts that are not inventoried devices (SPEC §5.1)."""

    kind: Literal["raw_external"]
    url: str


UrlSpec = Annotated[
    BuiltUrlSpec | HaWebhookSpec | RawExternalSpec, Field(discriminator="kind")
]


class SlotUpdate(BaseModel):
    enabled: bool
    urls: list[UrlSpec] = Field(default_factory=list)
    # Removing the last URL of a Gen2/3 slot deletes the webhook (SPEC §5.3).
    confirm_delete: bool = False
    # The slot state the editor loaded, so a re-scan in the meantime is caught
    # instead of silently overwritten (SPEC §5.4).
    expect_revision: str | None = None


class UrlTestRequest(BaseModel):
    url: str


class UrlTestResult(BaseModel):
    status_code: int | None = None
    elapsed_ms: int
    body_snippet: str = ""
    error: str | None = None
    simulated: bool = False


class AppSettings(BaseModel):
    ha_base_url: str | None = None


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


class RescanResult(BaseModel):
    ok: bool
    online: bool
    error: str | None = None


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
