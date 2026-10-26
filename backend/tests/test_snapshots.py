"""Config snapshots must record real changes only (issue #12 / DoD #7)."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from app.adapters.base import canonical_json, changed_paths, strip_volatile
from app.db import Database
from app.discovery import store_snapshot

# A Gen1 /settings dump, trimmed, with the fields that move between two reads.
GEN1_SETTINGS = {
    "device": {"type": "SHSW-25", "mac": "A4CF12F45B10", "hostname": "shelly25-livingroom"},
    "name": "Living room",
    "mode": "relay",
    "time": "15:28",
    "unixtime": 1756567680,
    "cloud": {"enabled": True, "connected": True},
    "relays": [{"name": "Ceiling", "default_state": "off", "auto_off": 0}],
    "wifi_sta": {"enabled": True, "ssid": "home", "rssi": -61},
}


def later_read(**overrides) -> dict:
    """The same device, read a minute later: clock moved, config did not."""
    config = json.loads(json.dumps(GEN1_SETTINGS))
    config["time"] = "15:29"
    config["unixtime"] = 1756567740
    config["wifi_sta"]["rssi"] = -58
    config["cloud"]["connected"] = False
    config.update(overrides)
    return {"settings": config, "actions": {}}


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "mesh.db")
    await database.connect()
    await database.execute(
        """
        INSERT INTO devices (id, ip, gen, model, online, first_seen, last_seen)
        VALUES ('dev', '192.168.1.10', 1, 'SHSW-25', 1, 'now', 'now')
        """
    )
    yield database
    await database.close()


async def count(database: Database) -> int:
    return len(await database.fetchall("SELECT id FROM config_snapshots"))


def test_strip_volatile_is_recursive():
    stripped = strip_volatile(GEN1_SETTINGS)
    assert "time" not in stripped and "unixtime" not in stripped
    assert "rssi" not in stripped["wifi_sta"]
    assert "connected" not in stripped["cloud"]
    # Real configuration survives untouched.
    assert stripped["wifi_sta"]["ssid"] == "home"
    assert stripped["relays"] == [{"name": "Ceiling", "default_state": "off", "auto_off": 0}]
    assert stripped["cloud"] == {"enabled": True}


def test_changed_paths_names_the_field():
    assert changed_paths({"a": {"b": 1}}, {"a": {"b": 2}}) == ["a.b"]
    assert changed_paths({"a": 1}, {"a": 1}) == []


@pytest.mark.asyncio
async def test_first_read_stores_one_snapshot(db):
    assert await store_snapshot(db, "dev", {"settings": GEN1_SETTINGS, "actions": {}}) is True
    assert await count(db) == 1


@pytest.mark.asyncio
async def test_volatile_only_change_stores_nothing(db):
    # The two reads really do differ verbatim — comparing the raw dumps, as the
    # code did before #12, is what made every scan record a snapshot.
    assert canonical_json(GEN1_SETTINGS) != canonical_json(later_read()["settings"])

    await store_snapshot(db, "dev", {"settings": GEN1_SETTINGS, "actions": {}})
    for _ in range(5):  # five more scans of an untouched device
        assert await store_snapshot(db, "dev", later_read()) is False
    assert await count(db) == 1


@pytest.mark.asyncio
async def test_real_change_stores_a_second_snapshot(db):
    await store_snapshot(db, "dev", {"settings": GEN1_SETTINGS, "actions": {}})
    renamed = later_read(name="Lounge")
    assert await store_snapshot(db, "dev", renamed) is True
    assert await count(db) == 2


@pytest.mark.asyncio
async def test_action_change_stores_a_second_snapshot(db):
    await store_snapshot(db, "dev", {"settings": GEN1_SETTINGS, "actions": {}})
    changed = later_read()
    changed["actions"] = {"btn_on_url": [{"index": 0, "enabled": True, "urls": ["http://x/"]}]}
    assert await store_snapshot(db, "dev", changed) is True
    assert await count(db) == 2


@pytest.mark.asyncio
async def test_the_stored_config_stays_verbatim(db):
    await store_snapshot(db, "dev", {"settings": GEN1_SETTINGS, "actions": {}})
    row = await db.fetchone("SELECT config FROM config_snapshots WHERE device_id = 'dev'")
    stored = json.loads(row["config"])
    # Volatile fields are ignored for comparison, never dropped from the dump.
    assert stored["settings"]["unixtime"] == 1756567680
    assert stored["settings"]["wifi_sta"]["rssi"] == -61


@pytest.mark.asyncio
async def test_ring_keeps_the_last_ten(db):
    for index in range(14):
        await store_snapshot(db, "dev", later_read(name=f"name-{index}"))
    assert await count(db) == 10


def live_gen1_read() -> dict:
    """Shapes taken from real SHSW-44 (meters, relays) and SHSW-25 (rollers) dumps."""
    settings = {
        "name": "Hall",
        "time": "20:03",
        "unixtime": 1792782236,
        "meters": [{"power": 0.0, "is_valid": True, "timestamp": 1792785837,
                    "counters": [0.0, 0.0, 0.0], "total": 77960}],
        "rollers": [{"power": 4.83, "state": "stop", "maxtime": 20.0, "is_valid": True}],
        "relays": [{"name": None, "ison": False, "has_timer": False, "auto_off": 0.0}],
    }
    return {"settings": settings, "actions": {}}


@pytest.mark.asyncio
async def test_live_meter_roller_and_relay_state_is_not_config(db):
    first = live_gen1_read()
    assert await store_snapshot(db, "dev", first) is True

    later = live_gen1_read()
    later["settings"]["time"] = "20:04"
    later["settings"]["meters"][0].update(
        power=7.6, timestamp=1792786056, counters=[7.58, 0.396, 0.0], total=77967
    )
    later["settings"]["rollers"][0]["power"] = 4.82
    later["settings"]["relays"][0].update(ison=True, has_timer=True)
    assert await store_snapshot(db, "dev", later) is False
    assert await count(db) == 1


@pytest.mark.asyncio
async def test_relay_config_change_still_stores_a_snapshot(db):
    await store_snapshot(db, "dev", live_gen1_read())
    changed = live_gen1_read()
    changed["settings"]["relays"][0]["auto_off"] = 30.0
    assert await store_snapshot(db, "dev", changed) is True
    assert await count(db) == 2


# --- the generic names are scoped to their container ---------------------------


@pytest.mark.asyncio
async def test_a_setting_that_merely_shares_a_live_name_is_compared(db):
    """'power' is a live reading inside meters[], but a setting anywhere else."""
    await store_snapshot(db, "dev", {"settings": {"name": "Lamp", "power": 10}, "actions": {}})
    assert (
        await store_snapshot(db, "dev", {"settings": {"name": "Lamp", "power": 25}, "actions": {}})
        is True
    )
    assert await count(db) == 2


@pytest.mark.asyncio
async def test_gen2_config_fields_are_compared(db):
    """Nothing in a Gen2 dump is scoped, so its settings compare verbatim."""
    before = {"config": {"switch:0": {"name": "Hall", "power_limit": 2800, "initial_state": "off"}}}
    await store_snapshot(db, "dev", before)
    after = {"config": {"switch:0": {"name": "Hall", "power_limit": 1400, "initial_state": "off"}}}
    assert await store_snapshot(db, "dev", after) is True
    assert await count(db) == 2


@pytest.mark.asyncio
async def test_roller_state_and_meter_validity_are_live_state(db):
    """The two candidates left open by the hardware run (#12)."""
    first = live_gen1_read()
    first["settings"]["emeters"] = [{"power": 12.4, "total": 900, "is_valid": True, "ct_type": 50}]
    await store_snapshot(db, "dev", first)

    moved = live_gen1_read()
    moved["settings"]["rollers"][0].update(state="open", is_valid=False, current_pos=42)
    moved["settings"]["meters"][0]["is_valid"] = False
    moved["settings"]["emeters"] = [{"power": 31.9, "total": 905, "is_valid": False, "ct_type": 50}]
    assert await store_snapshot(db, "dev", moved) is False
    assert await count(db) == 1


@pytest.mark.asyncio
async def test_a_roller_setting_still_stores_a_snapshot(db):
    await store_snapshot(db, "dev", live_gen1_read())
    changed = live_gen1_read()
    changed["settings"]["rollers"][0]["maxtime"] = 35.0
    assert await store_snapshot(db, "dev", changed) is True
    assert await count(db) == 2
