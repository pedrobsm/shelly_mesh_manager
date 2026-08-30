"""Action-URL resolver — the pattern table of SPEC §3.5 (normative).

Rules implemented here, in order:
  1. Parse with a real URL parser (never a regex over the whole URL).
  2. Decide the target: inventoried device IP -> 'device'; else a Shelly command
     pattern -> 'unknown_shelly' (dangling); else 'external' (HA webhook paths get
     their own classification).
  3. Match the path/query against the command pattern table.
  4. Derive the edge status from target + pattern + channel existence + slot state.
Matching is case-insensitive and tolerates trailing slashes and extra query params.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlsplit

HA_WEBHOOK_PREFIX = "/api/webhook/"

# Normalized commands (SPEC §2 `edges.command`).
CMD_ON = "on"
CMD_OFF = "off"
CMD_TOGGLE = "toggle"
CMD_OPEN = "open"
CMD_CLOSE = "close"
CMD_STOP = "stop"
CMD_SET = "set"
CMD_OTHER = "other"

_TRUE = {"true", "1", "on", "yes"}
_FALSE = {"false", "0", "off", "no"}


@dataclass(frozen=True)
class DeviceRef:
    """The bits of an inventoried device the resolver needs."""

    id: str
    ip: str
    gen: int
    # (kind, idx) -> channel id
    channels: Mapping[tuple[str, int], str] = field(default_factory=dict)

    def find_channel(self, kinds: tuple[str, ...], idx: int) -> tuple[str, str] | None:
        """Return (kind, channel_id) for the first candidate kind that exists."""
        for kind in kinds:
            channel_id = self.channels.get((kind, idx))
            if channel_id is not None:
                return kind, channel_id
        return None


@dataclass(frozen=True)
class CommandMatch:
    """A path that matched a row of the §3.5 pattern table."""

    channel_kinds: tuple[str, ...]  # candidates, most specific first
    channel_idx: int
    command: str
    params: dict[str, Any]


@dataclass
class Resolution:
    """One resolved action URL — mirrors a row of the `edges` table."""

    raw_url: str
    target_type: str  # 'device' | 'external' | 'unknown_shelly'
    status: str  # 'ok' | 'disabled' | 'dangling' | 'unparsed'
    command: str = CMD_OTHER
    params: dict[str, Any] = field(default_factory=dict)
    target_device_id: str | None = None
    target_channel_id: str | None = None
    target_channel_kind: str | None = None
    target_channel_idx: int | None = None
    external_host: str | None = None
    external_path: str | None = None
    webhook_id: str | None = None
    host: str | None = None
    path: str | None = None


def _num(value: str | None) -> Any:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _collect(query: Mapping[str, str], mapping: Mapping[str, str]) -> dict[str, Any]:
    """Pull the optional parameters of a pattern row out of the query string."""
    params: dict[str, Any] = {}
    for src_key, out_key in mapping.items():
        value = _num(query.get(src_key))
        if value is not None:
            params[out_key] = value
    return params


def split_url(raw_url: str) -> tuple[str, str, dict[str, str]]:
    """Return (host, path, query) — host without port/credentials, lower-cased."""
    text = (raw_url or "").strip()
    if "://" not in text:
        # Shelly action URLs are frequently stored scheme-less ('192.168.1.5/relay/0?...').
        text = "http://" + text.lstrip("/")
    parts = urlsplit(text)
    host = (parts.hostname or "").lower()
    path = parts.path or "/"
    query = {k.lower(): v for k, v in parse_qsl(parts.query, keep_blank_values=True)}
    return host, path, query


def _norm_path(path: str) -> str:
    path = path.lower()
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    if not path.startswith("/"):
        path = "/" + path
    return path


def _segments(path: str) -> list[str]:
    return [seg for seg in _norm_path(path).split("/") if seg]


def _turn_command(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().lower()
    return value if value in {CMD_ON, CMD_OFF, CMD_TOGGLE} else None


def _on_flag(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().lower()
    if value in _TRUE:
        return CMD_ON
    if value in _FALSE:
        return CMD_OFF
    return None


def match_command(path: str, query: Mapping[str, str]) -> CommandMatch | None:
    """The §3.5 pattern table. Matches by path shape only, on every generation."""
    segs = _segments(path)

    # --- Gen1 style: /relay/{i}, /roller/{i}, /light/{i}, /white/{i}, /color/{i} ---
    if len(segs) == 2 and segs[1].isdigit():
        idx = int(segs[1])
        head = segs[0]

        if head == "relay":
            command = _turn_command(query.get("turn"))
            if command:
                return CommandMatch(("relay",), idx, command, _collect(query, {"timer": "timer"}))

        elif head == "roller":
            go = (query.get("go") or "").strip().lower()
            if go in {CMD_OPEN, CMD_CLOSE, CMD_STOP}:
                return CommandMatch(
                    ("roller", "cover"), idx, go, _collect(query, {"duration": "duration"})
                )

        elif head in {"light", "white", "color"}:
            command = _turn_command(query.get("turn"))
            if command:
                kinds = {
                    "light": ("light", "rgbw", "white"),
                    "white": ("white", "light"),
                    "color": ("rgbw", "light"),
                }[head]
                params = _collect(query, {"brightness": "brightness", "timer": "timer"})
                return CommandMatch(kinds, idx, command, params)

        return None

    # --- Gen2/3 style: /rpc/{Method}?id={i}&... ---
    if len(segs) == 2 and segs[0] == "rpc":
        method = segs[1]
        idx = _num(query.get("id"))
        idx = idx if isinstance(idx, int) else 0

        if method == "switch.set":
            command = _on_flag(query.get("on"))
            if command:
                return CommandMatch(
                    ("relay",), idx, command, _collect(query, {"toggle_after": "toggle_after"})
                )

        elif method == "switch.toggle":
            return CommandMatch(("relay",), idx, CMD_TOGGLE, {})

        elif method in {"cover.open", "cover.close", "cover.stop"}:
            command = method.split(".", 1)[1]
            return CommandMatch(
                ("cover", "roller"), idx, command, _collect(query, {"duration": "duration"})
            )

        elif method == "cover.gotoposition":
            return CommandMatch(
                ("cover", "roller"), idx, CMD_SET, _collect(query, {"pos": "pos"})
            )

        elif method == "light.set":
            params = _collect(query, {"brightness": "brightness", "toggle_after": "toggle_after"})
            command = _on_flag(query.get("on"))
            if command is None:
                command = CMD_SET if "brightness" in params else None
            if command:
                return CommandMatch(("light", "rgbw", "white"), idx, command, params)

        return None

    return None


def looks_like_shelly(path: str, query: Mapping[str, str]) -> bool:
    """§1.1 / §3.5.2 — a path that matches a Shelly command pattern."""
    return match_command(path, query) is not None


def resolve(
    raw_url: str,
    devices_by_ip: Mapping[str, DeviceRef],
    *,
    slot_enabled: bool = True,
) -> Resolution:
    """Resolve one action URL into an edge (target, command, params, status)."""
    host, path, query = split_url(raw_url)
    norm_path = _norm_path(path)
    match = match_command(path, query)
    device = devices_by_ip.get(host)

    result = Resolution(
        raw_url=raw_url,
        target_type="external",
        status="unparsed",
        host=host,
        path=norm_path,
    )
    if match is not None:
        result.command = match.command
        result.params = dict(match.params)
        result.target_channel_kind = match.channel_kinds[0]
        result.target_channel_idx = match.channel_idx

    # --- 2. Target classification ---
    if device is not None:
        result.target_type = "device"
        result.target_device_id = device.id
        if match is None:
            # Device target, pattern not matched -> keep the edge, label '?'.
            result.command = CMD_OTHER
            result.params = {}
            result.status = "unparsed"
            return result

        found = device.find_channel(match.channel_kinds, match.channel_idx)
        if found is None:
            # Parsed channel does not exist on the target.
            result.status = "dangling"
            return result

        kind, channel_id = found
        result.target_channel_kind = kind
        result.target_channel_id = channel_id
        result.status = "ok" if slot_enabled else "disabled"
        return result

    if match is not None:
        # Looks like a Shelly, but the host is not inventoried.
        result.target_type = "unknown_shelly"
        result.external_host = host
        result.external_path = norm_path
        result.status = "dangling"
        return result

    result.target_type = "external"
    result.external_host = host
    result.external_path = norm_path
    result.command = CMD_OTHER
    result.params = {}
    if norm_path.startswith(HA_WEBHOOK_PREFIX):
        webhook_id = norm_path[len(HA_WEBHOOK_PREFIX) :].split("/", 1)[0]
        result.webhook_id = webhook_id or None
    # An external call is a legitimate, fully understood edge.
    result.status = "ok" if slot_enabled else "disabled"
    return result


_SUFFIX_UNITS = {
    "timer": "s",
    "toggle_after": "s",
    "duration": "s",
    "pos": "%",
}


def format_command_label(command: str, params: Mapping[str, Any] | None, status: str) -> str:
    """§1.3 edge labels: 'on', 'on · 30s', 'open · 50%', 'set · b=80', '?'."""
    if status == "unparsed":
        return "?"
    parts: list[str] = []
    for key, value in (params or {}).items():
        if key == "brightness":
            parts.append(f"b={value}")
        elif key in _SUFFIX_UNITS:
            parts.append(f"{value}{_SUFFIX_UNITS[key]}")
        else:
            parts.append(f"{key}={value}")
    return " · ".join([command, *parts])
