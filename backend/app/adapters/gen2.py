"""Gen2/Gen3 adapter — JSON-RPC over /rpc (SPEC §3.4.4)."""

from __future__ import annotations

import itertools
from typing import Any

import httpx

from .base import DeviceError, DeviceInventory, InventoryChannel, InventorySlot, ProbeResult

_rpc_ids = itertools.count(1)

# Gen2/3 webhook event -> (source_kind, normalized event) table.
GEN2_EVENTS: dict[str, tuple[str, str]] = {
    "switch.on": ("relay", "out_on"),
    "switch.off": ("relay", "out_off"),
    "input.toggle_on": ("input", "btn_on"),
    "input.toggle_off": ("input", "btn_off"),
    "input.button_push": ("input", "shortpush"),
    "input.button_longpush": ("input", "longpush"),
    "input.button_double_push": ("input", "double_shortpush"),
    "input.button_triple_push": ("input", "triple_shortpush"),
    "cover.open": ("cover", "roller_open"),
    "cover.opened": ("cover", "roller_open"),
    "cover.opening": ("cover", "roller_opening"),
    "cover.closed": ("cover", "roller_close"),
    "cover.closing": ("cover", "roller_closing"),
    "cover.stopped": ("cover", "roller_stop"),
    "light.on": ("light", "out_on"),
    "light.off": ("light", "out_off"),
    "temperature.measurement": ("sensor", "report"),
    "humidity.measurement": ("sensor", "report"),
}

# Config component key -> channel kind.
COMPONENT_KINDS = {
    "switch": "relay",
    "cover": "cover",
    "light": "light",
    "rgbw": "rgbw",
    "rgb": "rgbw",
    "white": "white",
}


def event_for(event: str, cid: int) -> tuple[str, int, str]:
    """Return (source_kind, source_idx, normalized event) for a webhook event."""
    key = (event or "").strip().lower()
    if key in GEN2_EVENTS:
        source_kind, normalized = GEN2_EVENTS[key]
        return source_kind, cid, normalized
    component = key.split(".", 1)[0] if "." in key else key
    source_kind = COMPONENT_KINDS.get(component, "input" if component == "input" else "sensor")
    return source_kind, cid, key.replace(".", "_") or "event"


async def rpc(
    client: httpx.AsyncClient,
    ip: str,
    method: str,
    params: dict[str, Any] | None = None,
    auth: httpx.Auth | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": next(_rpc_ids), "method": method}
    if params:
        payload["params"] = params
    response = await client.post(f"http://{ip}/rpc", json=payload, auth=auth)
    response.raise_for_status()
    body = response.json()
    if isinstance(body, dict) and body.get("error"):
        raise DeviceError(f"{method}: {body['error']}")
    result = body.get("result") if isinstance(body, dict) else None
    return result if isinstance(result, dict) else {}


def channels_from_config(config: dict[str, Any]) -> list[InventoryChannel]:
    """Channels come from the `switch:N` / `cover:N` / `light:N` config keys."""
    channels: list[InventoryChannel] = []
    for key, value in sorted((config or {}).items()):
        if ":" not in key:
            continue
        component, _, raw_idx = key.partition(":")
        kind = COMPONENT_KINDS.get(component.lower())
        if kind is None or not raw_idx.isdigit():
            continue
        name = (value or {}).get("name") if isinstance(value, dict) else None
        channels.append(InventoryChannel(kind, int(raw_idx), name))
    return channels


def slots_from_hooks(device_id: str, hooks: list[dict[str, Any]]) -> list[InventorySlot]:
    """Each webhook is one action slot."""
    slots: list[InventorySlot] = []
    for hook in hooks or []:
        if not isinstance(hook, dict):
            continue
        cid = hook.get("cid", 0)
        cid = cid if isinstance(cid, int) else 0
        source_kind, source_idx, event = event_for(str(hook.get("event") or ""), cid)
        hook_id = hook.get("id")
        urls = [str(u) for u in (hook.get("urls") or []) if str(u).strip()]
        slots.append(
            InventorySlot(
                id=f"{device_id}:hook:{hook_id}",
                source_kind=source_kind,
                source_idx=source_idx,
                event=event,
                native_key=str(hook.get("event") or ""),
                enabled=bool(hook.get("enable", False)),
                urls=urls,
                name=hook.get("name"),
            )
        )
    return slots


async def inventory(
    client: httpx.AsyncClient, probe_result: ProbeResult, auth: httpx.Auth | None = None
) -> DeviceInventory:
    """Read a Gen2/3 device: device info, config, supported events, webhooks."""
    ip = probe_result.ip
    info = await rpc(client, ip, "Shelly.GetDeviceInfo", auth=auth)
    config = await rpc(client, ip, "Shelly.GetConfig", auth=auth)
    supported = await rpc(client, ip, "Webhook.ListSupported", auth=auth)
    hook_list = await rpc(client, ip, "Webhook.List", auth=auth)

    merged_info = {**(probe_result.info or {}), **info}
    device_id = str(merged_info.get("id") or merged_info.get("mac") or ip)
    profile = merged_info.get("profile") or (
        ((config.get("sys") or {}).get("device") or {}).get("profile")
    )

    return DeviceInventory(
        id=device_id,
        ip=ip,
        gen=int(merged_info.get("gen") or probe_result.gen or 2),
        model=str(merged_info.get("model") or "unknown"),
        name=merged_info.get("name") or ((config.get("sys") or {}).get("device") or {}).get("name"),
        fw_version=str(merged_info.get("ver") or merged_info.get("fw_id") or "") or None,
        profile=profile,
        auth_required=bool(merged_info.get("auth_en", False)),
        raw_info=merged_info,
        channels=channels_from_config(config),
        slots=slots_from_hooks(device_id, hook_list.get("hooks") or []),
        config={"config": config, "webhooks": hook_list, "supported": supported},
    )
