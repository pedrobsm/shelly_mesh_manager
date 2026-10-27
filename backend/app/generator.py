"""URL generator — the exact inverse of the resolver's §3.5 table (SPEC §5.2).

The app builds every Shelly-target URL; the user never types one (§4). Each URL
is emitted in the *target's* native syntax — Gen1 path style for Gen1 targets,
`/rpc/...` for Gen2/3 — and anything generated here must parse back through
`resolver.resolve()` to the identical channel, command and params. That
roundtrip is the correctness backbone of Phase 2 and is tested exhaustively in
`tests/test_roundtrip.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlencode

from .resolver import (
    CMD_CLOSE,
    CMD_DIM_DOWN,
    CMD_DIM_STOP,
    CMD_DIM_UP,
    CMD_OFF,
    CMD_ON,
    CMD_OPEN,
    CMD_SET,
    CMD_STOP,
    CMD_TOGGLE,
)


class UnsupportedCommand(ValueError):
    """This command is not expressible for that channel kind on that generation."""


# Channel kinds, grouped by the component they map to.
RELAY_KINDS = ("relay",)
COVER_KINDS = ("roller", "cover")
LIGHT_KINDS = ("light", "white", "rgbw")

# Gen1 path head per light-ish channel kind.
GEN1_LIGHT_PATHS = {"light": "light", "white": "white", "rgbw": "color"}

# command -> the parameters it accepts, per generation. Native names: Gen1 and
# Gen2 spell the same idea differently (`timer` vs `toggle_after`, a dimmer
# `step` in percent vs a Gen2 `fade_rate`), and the §4 form shows whichever
# applies to the chosen target.
_GEN1_PARAMS: dict[tuple[str, str], tuple[str, ...]] = {
    ("relay", CMD_ON): ("timer",),
    ("relay", CMD_OFF): ("timer",),
    ("relay", CMD_TOGGLE): ("timer",),
    ("cover", CMD_OPEN): ("duration",),
    ("cover", CMD_CLOSE): ("duration",),
    ("cover", CMD_STOP): (),
    ("cover", CMD_SET): ("pos",),
    ("light", CMD_ON): ("brightness", "gain", "timer"),
    ("light", CMD_OFF): ("timer",),
    ("light", CMD_TOGGLE): ("brightness", "gain", "timer"),
    ("light", CMD_SET): ("brightness", "gain", "timer"),
    ("light", CMD_DIM_UP): ("step",),
    ("light", CMD_DIM_DOWN): ("step",),
    ("light", CMD_DIM_STOP): (),
}

_GEN2_PARAMS: dict[tuple[str, str], tuple[str, ...]] = {
    ("relay", CMD_ON): ("toggle_after",),
    ("relay", CMD_OFF): ("toggle_after",),
    ("relay", CMD_TOGGLE): (),
    ("cover", CMD_OPEN): ("duration",),
    ("cover", CMD_CLOSE): ("duration",),
    ("cover", CMD_STOP): (),
    ("cover", CMD_SET): ("pos",),
    ("light", CMD_ON): ("brightness", "transition_duration", "toggle_after"),
    ("light", CMD_OFF): ("transition_duration", "toggle_after"),
    ("light", CMD_TOGGLE): (),
    ("light", CMD_SET): ("brightness", "transition_duration"),
    ("light", CMD_DIM_UP): ("fade_rate",),
    ("light", CMD_DIM_DOWN): ("fade_rate",),
    ("light", CMD_DIM_STOP): (),
}

# Canonical names the UI may use, mapped to what the target actually calls them.
_ALIASES = {2: {"timer": "toggle_after", "step": "fade_rate"}}

# Commands that carry no meaning without one of these: a 'set' with no level is
# not a command the resolver can read back.
_REQUIRED: dict[tuple[str, str], tuple[str, ...]] = {
    ("cover", CMD_SET): ("pos",),
    ("light", CMD_SET): ("brightness", "gain"),
}

# A Gen1 colour channel takes `gain` where a white or dimmer channel takes
# `brightness`; offering both would let the UI build a URL the device ignores.
_LEVEL_BY_KIND = {"rgbw": "gain"}


@dataclass(frozen=True)
class BuiltUrl:
    """A generated URL plus the parse it is guaranteed to produce."""

    url: str
    command: str
    channel_kind: str
    channel_idx: int
    params: dict[str, Any] = field(default_factory=dict)


def _group(kind: str) -> str:
    if kind in RELAY_KINDS:
        return "relay"
    if kind in COVER_KINDS:
        return "cover"
    if kind in LIGHT_KINDS:
        return "light"
    raise UnsupportedCommand(f"unknown channel kind {kind!r}")


def supported_commands(gen: int, kind: str) -> dict[str, tuple[str, ...]]:
    """Commands available for this channel on this generation, with their params.

    This is what §4's command select and parameter fields are built from, so the
    UI can never offer something the generator cannot emit.
    """
    group = _group(kind)
    table = _GEN1_PARAMS if gen < 2 else _GEN2_PARAMS
    if gen >= 2 and group == "light" and kind != "light":
        # Gen2/3 RGBW and white components have no §3.5 row yet.
        return {}

    level = _LEVEL_BY_KIND.get(kind, "brightness")
    dropped = {"brightness", "gain"} - {level}
    return {
        command: tuple(name for name in params if name not in dropped)
        for (member, command), params in table.items()
        if member == group
    }


def _clean_params(
    gen: int, kind: str, command: str, params: Mapping[str, Any] | None
) -> dict[str, Any]:
    allowed = supported_commands(gen, kind).get(command)
    if allowed is None:
        raise UnsupportedCommand(f"{command!r} is not available on a {kind} channel (gen {gen})")

    aliases = _ALIASES.get(gen if gen < 2 else 2, {})
    cleaned: dict[str, Any] = {}
    for key, value in (params or {}).items():
        if value is None or value == "":
            continue
        name = aliases.get(key, key)
        if name not in allowed:
            raise UnsupportedCommand(f"{key!r} is not a parameter of {command!r} on gen {gen}")
        cleaned[name] = value

    required = _REQUIRED.get((_group(kind), command), ())
    if required and not cleaned.keys() & set(required):
        raise UnsupportedCommand(
            f"{command!r} on a {kind} channel needs one of {', '.join(required)}"
        )
    return cleaned


def _query(path: str, pairs: list[tuple[str, Any]]) -> str:
    return f"{path}?{urlencode(pairs)}" if pairs else path


def build_path(
    gen: int, kind: str, idx: int, command: str, params: Mapping[str, Any] | None = None
) -> BuiltUrl:
    """Build the path+query for one command, in the target's native syntax."""
    values = _clean_params(gen, kind, command, params)
    group = _group(kind)
    ordered = sorted(values.items())

    if gen < 2:
        if group == "relay":
            path = _query(f"/relay/{idx}", [("turn", command), *ordered])
        elif group == "cover":
            if command == CMD_SET:
                pairs = [("go", "to_pos"), ("roller_pos", values["pos"])]
                path = _query(f"/roller/{idx}", pairs)
            else:
                path = _query(f"/roller/{idx}", [("go", command), *ordered])
        else:
            head = GEN1_LIGHT_PATHS[kind]
            if command in {CMD_DIM_UP, CMD_DIM_DOWN}:
                pairs = [("dim", "up" if command == CMD_DIM_UP else "down"), *ordered]
            elif command == CMD_DIM_STOP:
                pairs = [("dim", "stop")]
            elif command == CMD_SET:
                pairs = list(ordered)
            else:
                pairs = [("turn", command), *ordered]
            path = _query(f"/{head}/{idx}", pairs)
    else:
        if group == "relay":
            if command == CMD_TOGGLE:
                path = _query("/rpc/Switch.Toggle", [("id", idx)])
            else:
                pairs = [("id", idx), ("on", "true" if command == CMD_ON else "false"), *ordered]
                path = _query("/rpc/Switch.Set", pairs)
        elif group == "cover":
            if command == CMD_SET:
                path = _query("/rpc/Cover.GoToPosition", [("id", idx), ("pos", values["pos"])])
            else:
                method = {CMD_OPEN: "Open", CMD_CLOSE: "Close", CMD_STOP: "Stop"}[command]
                path = _query(f"/rpc/Cover.{method}", [("id", idx), *ordered])
        else:
            if command in {CMD_DIM_UP, CMD_DIM_DOWN}:
                method = "DimUp" if command == CMD_DIM_UP else "DimDown"
                path = _query(f"/rpc/Light.{method}", [("id", idx), *ordered])
            elif command == CMD_DIM_STOP:
                path = _query("/rpc/Light.DimStop", [("id", idx)])
            elif command == CMD_TOGGLE:
                path = _query("/rpc/Light.Toggle", [("id", idx)])
            elif command == CMD_SET:
                path = _query("/rpc/Light.Set", [("id", idx), *ordered])
            else:
                pairs = [("id", idx), ("on", "true" if command == CMD_ON else "false"), *ordered]
                path = _query("/rpc/Light.Set", pairs)

    return BuiltUrl(
        url=path, command=command, channel_kind=kind, channel_idx=idx, params=dict(values)
    )


def build_url(
    host: str,
    gen: int,
    kind: str,
    idx: int,
    command: str,
    params: Mapping[str, Any] | None = None,
) -> BuiltUrl:
    """The §4 guided builder's output: a full URL at `host`, natively spelled."""
    built = build_path(gen, kind, idx, command, params)
    return BuiltUrl(
        url=f"http://{host}{built.url}",
        command=built.command,
        channel_kind=built.channel_kind,
        channel_idx=built.channel_idx,
        params=built.params,
    )
