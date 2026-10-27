"""Shared adapter types, probing and the human-readable label vocabulary."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx


# Keys whose value changes between two reads of an unchanged device. They are
# stored in the snapshot (it is a verbatim config dump, and Phase 4 restores from
# it) but ignored when deciding whether the config changed, so an untouched Gen1
# device does not record a fresh snapshot on every scan (issue #12).
# Extend this list if a field is found churning on real hardware.
VOLATILE_KEYS = frozenset(
    {
        "time",
        "unixtime",
        "unixtime_ms",
        "uptime",
        "uptime_s",
        "ram_free",
        "ram_total",
        "ram_size",
        "fs_free",
        "fs_size",
        "rssi",
        "connected",
        "has_update",
        "new_version",
        "old_version",
        "serial",
        "cfg_changed_cnt",
        "last_error",
    }
)

# Live readings carried inside a device's per-channel arrays. Their names are
# generic enough to name a real setting elsewhere ("power" next to "max_power"),
# so they only count as volatile under these containers — a top-level or Gen2
# field of the same name is still compared, and a genuine config change to it
# still records a snapshot.
VOLATILE_IN_CONTAINER: dict[str, frozenset[str]] = {
    "meters": frozenset({"power", "counters", "total", "timestamp", "is_valid", "overpower"}),
    "emeters": frozenset(
        {
            "power",
            "counters",
            "total",
            "total_returned",
            "timestamp",
            "is_valid",
            "voltage",
            "current",
            "pf",
        }
    ),
    "rollers": frozenset(
        {
            "power",
            "state",
            "is_valid",
            "current_pos",
            "last_direction",
            "stop_reason",
            "calibrating",
            "positioning",
            "overtemperature",
            "safety_switch",
        }
    ),
    "relays": frozenset(
        {
            "ison",
            "has_timer",
            "timer_started",
            "timer_duration",
            "timer_remaining",
            "overpower",
            "overtemperature",
            "source",
        }
    ),
    "lights": frozenset(
        {
            "ison",
            "has_timer",
            "power",
            "timer_started",
            "timer_duration",
            "timer_remaining",
            "overpower",
            "source",
        }
    ),
}


def strip_volatile(value: Any, container: str = "") -> Any:
    """Drop volatile fields so two reads of one device can be compared.

    `container` is the name of the nearest enclosing array — 'meters', 'relays'
    and so on — which is what scopes the generic names of VOLATILE_IN_CONTAINER.
    """
    if isinstance(value, dict):
        scoped = VOLATILE_IN_CONTAINER.get(container, frozenset())
        stripped: dict[str, Any] = {}
        for key, item in value.items():
            low = key.lower()
            if low in VOLATILE_KEYS or low in scoped:
                continue
            stripped[key] = strip_volatile(item, low if isinstance(item, list) else container)
        return stripped
    if isinstance(value, list):
        return [strip_volatile(item, container) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def changed_paths(before: Any, after: Any, prefix: str = "", limit: int = 8) -> list[str]:
    """Dotted paths whose values differ — what gets logged when a snapshot is kept."""
    if isinstance(before, dict) and isinstance(after, dict):
        paths: list[str] = []
        for key in sorted(set(before) | set(after)):
            if len(paths) >= limit:
                break
            where = f"{prefix}.{key}" if prefix else str(key)
            if key not in before or key not in after:
                paths.append(where)
            else:
                paths.extend(changed_paths(before[key], after[key], where, limit - len(paths)))
        return paths[:limit]
    return [] if before == after else [prefix or "(root)"]


def gen1_device_id(model: str | None, mac: str | None, fallback: str = "") -> str:
    """The stable id of a Gen1 device: MAC-derived, never the hostname (SPEC §2).

    Both the full-inventory path and the partial path used for a device that
    refuses inventory must name a device identically, or the same device is
    recorded twice (issue #27). A hostname is not stable either — changing it in
    the device's web UI would orphan everything keyed on it (issue #28).
    """
    mac = str(mac or "").strip().upper()
    name = str(model or "shelly").strip().lower() or "shelly"
    if mac:
        return f"{name}-{mac}"
    return fallback or name


class DeviceError(Exception):
    """A device could not be inventoried; the scan carries on regardless."""


class AuthRequired(DeviceError):
    """Device answered 401 and no working credentials are known."""


@dataclass
class InventoryChannel:
    """An input port of the graph (SPEC §1.2)."""

    kind: str  # relay|roller|cover|light|white|rgbw
    idx: int
    name: str | None = None

    @property
    def key(self) -> tuple[str, int]:
        return (self.kind, self.idx)


@dataclass
class InventorySlot:
    """An output port of the graph — one action slot (SPEC §1.2)."""

    id: str
    source_kind: str  # input|relay|roller|cover|light|sensor
    source_idx: int
    event: str
    native_key: str
    enabled: bool
    urls: list[str] = field(default_factory=list)
    name: str | None = None


@dataclass
class DeviceInventory:
    id: str
    ip: str
    gen: int
    model: str
    name: str | None = None
    fw_version: str | None = None
    profile: str | None = None
    auth_required: bool = False
    raw_info: dict[str, Any] = field(default_factory=dict)
    channels: list[InventoryChannel] = field(default_factory=list)
    slots: list[InventorySlot] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)

    def config_json(self) -> str:
        return canonical_json(self.config)


@dataclass
class ProbeResult:
    ip: str
    gen: int
    info: dict[str, Any]
    auth_required: bool = False


async def probe(client: httpx.AsyncClient, ip: str) -> ProbeResult | None:
    """`GET http://{ip}/shelly` — unauthenticated on all generations (SPEC §3.4.2)."""
    try:
        response = await client.get(f"http://{ip}/shelly")
    except (httpx.HTTPError, OSError):
        return None
    if response.status_code == 401:
        # /shelly is unauthenticated everywhere; a 401 here still means "a Shelly".
        return ProbeResult(ip=ip, gen=1, info={}, auth_required=True)
    if response.status_code >= 400:
        return None
    try:
        info = response.json()
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(info, dict):
        return None
    gen = info.get("gen")
    if isinstance(gen, int) and gen >= 2:
        return ProbeResult(ip=ip, gen=gen, info=info, auth_required=bool(info.get("auth_en")))
    if "type" in info or "mac" in info:
        return ProbeResult(ip=ip, gen=1, info=info, auth_required=bool(info.get("auth")))
    return None


# --- Label vocabulary (used by the API and the graph builder) -----------------

_KIND_LABELS = {
    "relay": "Relay",
    "roller": "Roller",
    "cover": "Cover",
    "light": "Light",
    "white": "White",
    "rgbw": "RGBW",
    "input": "SW",
    "sensor": "Sensor",
}

EVENT_LABELS = {
    "btn_on": "button on",
    "btn_off": "button off",
    "shortpush": "short push",
    "longpush": "long push",
    "double_shortpush": "double push",
    "triple_shortpush": "triple push",
    "shortpush_longpush": "short+long push",
    "longpush_shortpush": "long+short push",
    "out_on": "output on",
    "out_off": "output off",
    "roller_open": "opened",
    "roller_close": "closed",
    "roller_stop": "stopped",
    "roller_opening": "opening",
    "roller_closing": "closing",
    "over_power": "over power",
    "over_temp": "over temperature",
    "report": "report",
}


def channel_label(kind: str, idx: int) -> str:
    """Input-port label, e.g. 'Relay 0'."""
    return f"{_KIND_LABELS.get(kind, kind.title())} {idx}"


def source_label(source_kind: str, source_idx: int) -> str:
    """Event-source label, e.g. 'SW1', 'Relay 0', 'Roller'."""
    if source_kind == "input":
        return f"SW{source_idx + 1}"
    if source_kind == "roller":
        return "Roller" if source_idx == 0 else f"Roller {source_idx}"
    if source_kind == "sensor":
        return "Sensor" if source_idx == 0 else f"Sensor {source_idx}"
    return channel_label(source_kind, source_idx)


def event_label(event: str) -> str:
    return EVENT_LABELS.get(event, event.replace("_", " "))


def slot_label(source_kind: str, source_idx: int, event: str) -> str:
    """Output-port label, e.g. 'SW1 · button on', 'Roller · stopped'."""
    return f"{source_label(source_kind, source_idx)} · {event_label(event)}"
