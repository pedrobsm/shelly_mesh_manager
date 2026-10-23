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
