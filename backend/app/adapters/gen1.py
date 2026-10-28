"""Gen1 adapter — /settings + /settings/actions (SPEC §3.4.3)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from .base import (
    DeviceError,
    DeviceInventory,
    InventoryChannel,
    InventorySlot,
    ProbeResult,
    gen1_device_id,
)

# Static Gen1 action-key -> (source_kind, normalized event) table.
GEN1_ACTION_EVENTS: dict[str, tuple[str, str]] = {
    "btn_on_url": ("input", "btn_on"),
    "btn_off_url": ("input", "btn_off"),
    "shortpush_url": ("input", "shortpush"),
    "longpush_url": ("input", "longpush"),
    "double_shortpush_url": ("input", "double_shortpush"),
    "triple_shortpush_url": ("input", "triple_shortpush"),
    "shortpush_longpush_url": ("input", "shortpush_longpush"),
    "longpush_shortpush_url": ("input", "longpush_shortpush"),
    "out_on_url": ("relay", "out_on"),
    "out_off_url": ("relay", "out_off"),
    "roller_open_url": ("roller", "roller_open"),
    "roller_close_url": ("roller", "roller_close"),
    "roller_stop_url": ("roller", "roller_stop"),
    "over_power_url": ("relay", "over_power"),
    "over_temp_url": ("sensor", "over_temp"),
    "report_url": ("sensor", "report"),
    "lp_on_url": ("input", "longpush"),
    "lp_off_url": ("input", "longpush"),
}


def event_for_key(key: str) -> tuple[str, str]:
    """Unknown `*_url` keys still become slots — sensor-sourced, event = key stem."""
    if key in GEN1_ACTION_EVENTS:
        return GEN1_ACTION_EVENTS[key]
    return ("sensor", key[:-4] if key.endswith("_url") else key)


def device_id_for(info: dict[str, Any], settings: dict[str, Any]) -> str:
    """MAC-derived, with the hostname only as a last resort (SPEC §2, issue #28)."""
    device = settings.get("device") or {}
    return gen1_device_id(
        info.get("type") or device.get("type"),
        info.get("mac") or device.get("mac"),
        fallback=str(device.get("hostname") or ""),
    )


def channels_from_settings(settings: dict[str, Any]) -> list[InventoryChannel]:
    """Relay / roller / light channels — the device's controllable channels."""
    mode = (settings.get("mode") or "").lower()
    channels: list[InventoryChannel] = []

    rollers = settings.get("rollers") or []
    relays = settings.get("relays") or []
    lights = settings.get("lights") or []

    if mode == "roller" and rollers:
        for idx, roller in enumerate(rollers):
            channels.append(InventoryChannel("roller", idx, (roller or {}).get("name")))
        return channels

    if lights:
        kind = {"white": "white", "color": "rgbw"}.get(mode, "light")
        for idx, light in enumerate(lights):
            channels.append(InventoryChannel(kind, idx, (light or {}).get("name")))
        return channels

    for idx, relay in enumerate(relays):
        channels.append(InventoryChannel("relay", idx, (relay or {}).get("name")))
    return channels


def slots_from_actions(device_id: str, actions: dict[str, Any]) -> list[InventorySlot]:
    """Every `*_url` key x index becomes one action slot."""
    slots: list[InventorySlot] = []
    for key, entries in sorted((actions or {}).items()):
        if not isinstance(entries, list):
            continue
        source_kind, event = event_for_key(key)
        for position, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            idx = entry.get("index", position)
            idx = idx if isinstance(idx, int) else position
            urls = [str(u) for u in (entry.get("urls") or []) if str(u).strip()]
            slots.append(
                InventorySlot(
                    id=f"{device_id}:act:{key}:{idx}",
                    source_kind=source_kind,
                    source_idx=idx,
                    event=event,
                    native_key=key,
                    enabled=bool(entry.get("enabled", False)),
                    urls=urls,
                )
            )
    return slots


async def inventory(
    client: httpx.AsyncClient, probe_result: ProbeResult, auth: httpx.Auth | None = None
) -> DeviceInventory:
    """Read a Gen1 device: identity, channels, action slots, config snapshot."""
    settings_response = await client.get(f"http://{probe_result.ip}/settings", auth=auth)
    settings_response.raise_for_status()
    settings = settings_response.json()

    actions_response = await client.get(f"http://{probe_result.ip}/settings/actions", auth=auth)
    actions_response.raise_for_status()
    actions = (actions_response.json() or {}).get("actions") or {}

    info = probe_result.info or {}
    device_id = device_id_for(info, settings)
    device_block = settings.get("device") or {}

    return DeviceInventory(
        id=device_id,
        ip=probe_result.ip,
        gen=1,
        model=str(info.get("type") or device_block.get("type") or "unknown"),
        name=settings.get("name") or device_block.get("hostname"),
        fw_version=str(info.get("fw") or settings.get("fw") or "") or None,
        profile=(settings.get("mode") or None),
        auth_required=bool((settings.get("login") or {}).get("enabled", info.get("auth", False))),
        raw_info=info,
        channels=channels_from_settings(settings),
        slots=slots_from_actions(device_id, actions),
        config={"settings": settings, "actions": actions},
    )


def action_query(native_key: str, idx: int, enabled: bool, urls: list[str]) -> str:
    """The query string of a Gen1 action write (SPEC §5.3).

    The brackets of `urls[]` must reach the device **literally**. Percent-encoding
    the parameter *name* — which is what a normal query builder does, producing
    `urls%5B%5D` — leaves the firmware with a parameter it does not recognise: it
    applies `enabled` and silently ignores the URLs, so a slot could be switched
    on and off but never edited or cleared. Values are still encoded, because a
    URL carries `?` and `&` of its own.
    """
    parts = [
        f"index={idx}",
        f"name={quote(native_key, safe='')}",
        f"enabled={'true' if enabled else 'false'}",
    ]
    # A single empty `urls[]` clears the slot.
    parts.extend(f"urls[]={quote(url, safe='')}" for url in urls) if urls else parts.append("urls[]=")
    return "&".join(parts)


async def write_slot(
    client: httpx.AsyncClient,
    ip: str,
    native_key: str,
    idx: int,
    enabled: bool,
    urls: list[str],
    auth: httpx.Auth | None = None,
) -> None:
    """Write one action slot: `/settings/actions?index=&name=&enabled=&urls[]=…`."""
    url = httpx.URL(f"http://{ip}/settings/actions").copy_with(
        query=action_query(native_key, idx, enabled, urls).encode()
    )
    response = await client.get(url, auth=auth)
    response.raise_for_status()


async def read_slot(
    client: httpx.AsyncClient,
    ip: str,
    native_key: str,
    idx: int,
    auth: httpx.Auth | None = None,
) -> tuple[bool, list[str]]:
    """Re-read one slot for the mandatory verify-after-write."""
    response = await client.get(f"http://{ip}/settings/actions", auth=auth)
    response.raise_for_status()
    actions = (response.json() or {}).get("actions") or {}
    for position, entry in enumerate(actions.get(native_key) or []):
        if not isinstance(entry, dict):
            continue
        if entry.get("index", position) == idx:
            urls = [str(url) for url in (entry.get("urls") or []) if str(url).strip()]
            return bool(entry.get("enabled", False)), urls
    raise DeviceError(f"the device no longer reports an action slot {native_key}[{idx}]")
