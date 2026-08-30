"""Demo fixtures (SPEC §3.8) — the 'runs at first try' guarantee.

DEMO_MODE=true seeds this network and performs no network I/O whatsoever. The
fixture exercises every node type, every edge status, both port-visibility rules,
self-loop rendering and all label formats.
"""

from __future__ import annotations

from .adapters.base import DeviceInventory, InventoryChannel, InventorySlot
from .db import Database

HA_HOST = "192.168.1.5:8123"


def _gen1_slot(device_id: str, key: str, idx: int, source: str, event: str, *, enabled: bool,
               urls: list[str] | None = None) -> InventorySlot:
    return InventorySlot(
        id=f"{device_id}:act:{key}:{idx}",
        source_kind=source,
        source_idx=idx,
        event=event,
        native_key=key,
        enabled=enabled,
        urls=list(urls or []),
    )


def _hook_slot(device_id: str, hook_id: int, event_native: str, source: str, source_idx: int,
               event: str, *, enabled: bool, urls: list[str], name: str | None = None
               ) -> InventorySlot:
    return InventorySlot(
        id=f"{device_id}:hook:{hook_id}",
        source_kind=source,
        source_idx=source_idx,
        event=event,
        native_key=event_native,
        enabled=enabled,
        urls=list(urls),
        name=name,
    )


def fixtures() -> list[DeviceInventory]:
    """The exact fixture network of SPEC §3.8."""
    livingroom = DeviceInventory(
        id="shelly25-livingroom",
        ip="192.168.1.10",
        gen=1,
        model="SHSW-25",
        name="Living room",
        fw_version="20230913-112003/v1.14.0",
        profile="relay",
        raw_info={"type": "SHSW-25", "mac": "A4CF12F45B10", "auth": False,
                  "fw": "20230913-112003/v1.14.0", "num_outputs": 2},
        channels=[
            InventoryChannel("relay", 0, "Ceiling light"),
            InventoryChannel("relay", 1, "Reading lamp"),
        ],
        slots=[
            # SW1 button-on -> relay 0 of the hall device.
            _gen1_slot("shelly25-livingroom", "btn_on_url", 0, "input", "btn_on", enabled=True,
                       urls=["http://192.168.1.11/relay/0?turn=on"]),
            # Output-off -> Home Assistant webhook.
            _gen1_slot("shelly25-livingroom", "out_off_url", 0, "relay", "out_off", enabled=True,
                       urls=[f"http://{HA_HOST}/api/webhook/lights_all_off"]),
            # Enabled but empty, and disabled: both hidden until "Show inactive actions".
            _gen1_slot("shelly25-livingroom", "out_on_url", 0, "relay", "out_on", enabled=True),
            _gen1_slot("shelly25-livingroom", "btn_on_url", 1, "input", "btn_on", enabled=False),
        ],
        config={"settings": {"name": "Living room", "mode": "relay"}, "actions": {}},
    )

    blinds = DeviceInventory(
        id="shelly25-blinds",
        ip="192.168.1.20",
        gen=1,
        model="SHSW-25",
        name="Blinds living room",
        fw_version="20230913-112003/v1.14.0",
        profile="roller",
        raw_info={"type": "SHSW-25", "mac": "A4CF12F45B20", "auth": False,
                  "fw": "20230913-112003/v1.14.0", "num_outputs": 2},
        channels=[InventoryChannel("roller", 0, "Blinds")],
        slots=[
            _gen1_slot("shelly25-blinds", "roller_stop_url", 0, "roller", "roller_stop",
                       enabled=True, urls=["http://192.168.1.21/roller/0?go=stop"]),
        ],
        config={"settings": {"name": "Blinds living room", "mode": "roller"}, "actions": {}},
    )

    blinds2 = DeviceInventory(
        id="shelly25-blinds2",
        ip="192.168.1.21",
        gen=1,
        model="SHSW-25",
        name="Blinds kitchen",
        fw_version="20230913-112003/v1.14.0",
        profile="roller",
        raw_info={"type": "SHSW-25", "mac": "A4CF12F45B21", "auth": False,
                  "fw": "20230913-112003/v1.14.0", "num_outputs": 2},
        channels=[InventoryChannel("roller", 0, "Blinds kitchen")],
        slots=[
            _gen1_slot("shelly25-blinds2", "roller_stop_url", 0, "roller", "roller_stop",
                       enabled=False),
        ],
        config={"settings": {"name": "Blinds kitchen", "mode": "roller"}, "actions": {}},
    )

    hall = DeviceInventory(
        id="shellyplus1-hall",
        ip="192.168.1.11",
        gen=2,
        model="SNSW-001X16EU",
        name="Hall",
        fw_version="1.4.4",
        profile="switch",
        raw_info={"id": "shellyplus1-hall", "mac": "A8032ABD7CB0", "model": "SNSW-001X16EU",
                  "gen": 2, "ver": "1.4.4", "app": "Plus1", "auth_en": False},
        channels=[InventoryChannel("relay", 0, "Hall light")],
        slots=[
            _hook_slot("shellyplus1-hall", 0, "switch.off", "relay", 0, "out_off", enabled=True,
                       name="Porch off after 30s",
                       urls=["http://192.168.1.12/rpc/Switch.Set?id=0&on=false&toggle_after=30"]),
        ],
        config={"config": {"switch:0": {"name": "Hall light"}}, "webhooks": {}, "supported": {}},
    )

    porch = DeviceInventory(
        id="shelly1pm-porch",
        ip="192.168.1.12",
        gen=1,
        model="SHSW-PM",
        name="Porch",
        fw_version="20230913-112003/v1.14.0",
        profile="relay",
        raw_info={"type": "SHSW-PM", "mac": "A4CF12F45B12", "auth": False,
                  "fw": "20230913-112003/v1.14.0", "num_outputs": 1},
        channels=[InventoryChannel("relay", 0, "Porch light")],
        slots=[
            # Enabled action pointing at an IP that is not (and never was) in the inventory.
            _gen1_slot("shelly1pm-porch", "btn_on_url", 0, "input", "btn_on", enabled=True,
                       urls=["http://192.168.1.99/relay/0?turn=on"]),
        ],
        config={"settings": {"name": "Porch", "mode": "relay"}, "actions": {}},
    )

    garage = DeviceInventory(
        id="shellyplus2pm-garage",
        ip="192.168.1.13",
        gen=3,
        model="S3SW-002P16EU",
        name="Garage",
        fw_version="1.4.4",
        profile="cover",
        raw_info={"id": "shellyplus2pm-garage", "mac": "A8032ABD7CD3", "model": "S3SW-002P16EU",
                  "gen": 3, "ver": "1.4.4", "app": "Plus2PM", "auth_en": False,
                  "profile": "cover"},
        channels=[InventoryChannel("cover", 0, "Garage door")],
        slots=[
            # Disabled self-loop: hidden by default, dashed when "Show inactive" is on.
            _hook_slot("shellyplus2pm-garage", 0, "input.toggle_on", "input", 0, "btn_on",
                       enabled=False, name="Open garage",
                       urls=["http://192.168.1.13/rpc/Cover.Open?id=0"]),
            # Enabled, but the URL is not a command pattern -> amber dotted 'unparsed' edge.
            _hook_slot("shellyplus2pm-garage", 1, "cover.stopped", "cover", 0, "roller_stop",
                       enabled=True, name="Poll hall",
                       urls=["http://192.168.1.11/status"]),
        ],
        config={"config": {"cover:0": {"name": "Garage door"},
                           "sys": {"device": {"profile": "cover"}}},
                "webhooks": {}, "supported": {}},
    )

    return [livingroom, blinds, blinds2, hall, porch, garage]


async def seed_demo(db: Database) -> list[str]:
    """Seed the fixture network into the DB. Returns the seeded device ids."""
    from .discovery import store_inventory

    devices = fixtures()
    ids = [device.id for device in devices]
    placeholders = ",".join("?" for _ in ids)
    await db.execute(f"DELETE FROM devices WHERE id NOT IN ({placeholders})", ids)
    for device in devices:
        await store_inventory(db, device)
    return ids
