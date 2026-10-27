"""One device, one record (issues #27, #28) and no scan left running (#29)."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from app.adapters.base import ProbeResult, gen1_device_id
from app.adapters.gen1 import device_id_for
from app.db import Database
from app.discovery import (
    canonical_device_id,
    close_unfinished_scans,
    migrate_device_ids,
    partial_identity,
    upsert_device,
)

# The Office entrance switch from the 2026-10-27 pass, which ended up stored twice.
MAC = "A4CF12BA765C"
HOSTNAME_ID = "shellyswitch25-BA765C"
MAC_ID = "shsw-25-A4CF12BA765C"
IP = "192.168.33.11"
INFO = {"type": "SHSW-25", "mac": MAC, "auth": False, "fw": "20230913/v1.14.0"}
SETTINGS = {"device": {"hostname": HOSTNAME_ID, "type": "SHSW-25", "mac": MAC}, "name": "Office"}


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "mesh.db")
    await database.connect()
    yield database
    await database.close()


async def insert_device(db: Database, device_id: str, ip: str, **overrides) -> None:
    values = {
        "name": "Office entrance switch",
        "online": 1,
        "auth_required": 0,
        "first_seen": "2026-08-01T00:00:00+00:00",
        "last_seen": "2026-10-27T10:00:00+00:00",
        "raw_info": json.dumps(INFO),
        **overrides,
    }
    await db.execute(
        """
        INSERT INTO devices (id, ip, gen, model, name, online, auth_required,
                             first_seen, last_seen, raw_info)
        VALUES (?, ?, 1, 'SHSW-25', ?, ?, ?, ?, ?, ?)
        """,
        (
            device_id,
            ip,
            values["name"],
            values["online"],
            values["auth_required"],
            values["first_seen"],
            values["last_seen"],
            values["raw_info"],
        ),
    )


async def device_ids(db: Database) -> list[str]:
    return [row["id"] for row in await db.fetchall("SELECT id FROM devices ORDER BY id")]


# --- #28: the id is MAC-derived ------------------------------------------------


def test_the_hostname_no_longer_names_the_device():
    assert device_id_for(INFO, SETTINGS) == MAC_ID


def test_both_identity_paths_agree():
    """A protected device names itself the same way a readable one does (#27)."""
    partial, model = partial_identity(ProbeResult(ip=IP, gen=1, info=INFO))
    assert partial == device_id_for(INFO, SETTINGS) == MAC_ID
    assert model == "SHSW-25"


def test_a_renamed_host_keeps_its_id():
    renamed = {"device": {**SETTINGS["device"], "hostname": "my-hallway-switch"}}
    assert device_id_for(INFO, renamed) == MAC_ID


def test_the_mac_is_taken_from_wherever_it_is_reported():
    """/shelly and /settings both carry it; either will do."""
    assert device_id_for({"type": "SHSW-25"}, SETTINGS) == MAC_ID
    assert device_id_for(INFO, {"device": {"hostname": HOSTNAME_ID}}) == MAC_ID


def test_the_hostname_is_still_a_fallback_without_a_mac():
    macless = {"device": {"hostname": HOSTNAME_ID, "type": "SHSW-25"}}
    assert device_id_for({"type": "SHSW-25"}, macless) == HOSTNAME_ID
    assert gen1_device_id("SHSW-25", None, fallback="x") == "x"


# --- #27: migrating what is already stored ------------------------------------


@pytest.mark.asyncio
async def test_canonical_id_is_read_from_the_stored_raw_info(db):
    await insert_device(db, HOSTNAME_ID, IP)
    row = await db.fetchone("SELECT * FROM devices WHERE id = ?", (HOSTNAME_ID,))
    assert canonical_device_id(row) == MAC_ID


@pytest.mark.asyncio
async def test_migration_rekeys_and_keeps_what_cannot_be_re_read(db):
    await insert_device(db, HOSTNAME_ID, IP)
    await db.execute(
        "INSERT INTO channels (id, device_id, kind, idx) VALUES (?, ?, 'relay', 0)",
        (f"{HOSTNAME_ID}:relay:0", HOSTNAME_ID),
    )
    await db.execute(
        "INSERT INTO credentials (device_id, username, password) VALUES (?, 'admin', 'secret')",
        (HOSTNAME_ID,),
    )
    await db.execute(
        "INSERT INTO config_snapshots (id, device_id, taken_at, config) VALUES ('s1', ?, 'now', '{}')",
        (HOSTNAME_ID,),
    )
    await db.execute(
        "INSERT INTO node_layout (node_id, x, y) VALUES (?, 12.0, 34.0)", (HOSTNAME_ID,)
    )

    assert await migrate_device_ids(db) == 1
    assert await device_ids(db) == [MAC_ID]

    kept = await db.fetchone("SELECT * FROM devices WHERE id = ?", (MAC_ID,))
    assert kept["ip"] == IP
    assert kept["name"] == "Office entrance switch"
    assert kept["first_seen"] == "2026-08-01T00:00:00+00:00"

    creds = await db.fetchone("SELECT * FROM credentials WHERE device_id = ?", (MAC_ID,))
    assert creds["password"] == "secret"
    snapshot = await db.fetchone("SELECT * FROM config_snapshots WHERE device_id = ?", (MAC_ID,))
    assert snapshot["id"] == "s1"
    layout = await db.fetchone("SELECT * FROM node_layout WHERE node_id = ?", (MAC_ID,))
    assert (layout["x"], layout["y"]) == (12.0, 34.0)
    # Channels ride along by being dropped; the next scan rebuilds them.
    assert await db.fetchall("SELECT id FROM channels") == []


@pytest.mark.asyncio
async def test_migration_merges_the_stub_left_by_a_protected_device(db):
    """The exact state found on the LAN: a full record plus an empty stub."""
    await insert_device(db, HOSTNAME_ID, IP, last_seen="2026-10-27T10:00:00+00:00")
    await insert_device(
        db,
        MAC_ID,
        f"unassigned:{MAC_ID}",
        name=None,
        online=0,
        auth_required=1,
        last_seen="2026-10-26T09:00:00+00:00",
    )
    await db.execute(
        "INSERT INTO node_layout (node_id, x, y) VALUES (?, 7.0, 8.0)", (HOSTNAME_ID,)
    )

    assert await migrate_device_ids(db) == 1
    assert await device_ids(db) == [MAC_ID]

    kept = await db.fetchone("SELECT * FROM devices WHERE id = ?", (MAC_ID,))
    assert kept["ip"] == IP  # the real address, not the parked one
    assert kept["name"] == "Office entrance switch"  # the newer, fuller reading wins
    assert kept["auth_required"] == 0
    layout = await db.fetchone("SELECT * FROM node_layout WHERE node_id = ?", (MAC_ID,))
    assert (layout["x"], layout["y"]) == (7.0, 8.0)


@pytest.mark.asyncio
async def test_migration_is_idempotent_and_leaves_canonical_rows_alone(db):
    await insert_device(db, MAC_ID, IP)
    assert await migrate_device_ids(db) == 0
    assert await device_ids(db) == [MAC_ID]


@pytest.mark.asyncio
async def test_a_scan_absorbs_a_record_stored_under_an_old_id(db):
    """A device seen by a scan before the migration ran must not double up."""
    await insert_device(db, HOSTNAME_ID, IP)
    await upsert_device(
        db,
        device_id=MAC_ID,
        ip=IP,
        gen=1,
        model="SHSW-25",
        name="Office entrance switch",
        fw_version=None,
        profile="relay",
        auth_required=False,
        raw_info=INFO,
    )
    assert await device_ids(db) == [MAC_ID]


@pytest.mark.asyncio
async def test_a_different_device_taking_the_address_still_keeps_its_row(db):
    await insert_device(db, "shsw-1-AABBCCDDEEFF", IP, raw_info=json.dumps({"type": "SHSW-1", "mac": "AABBCCDDEEFF"}))
    await upsert_device(
        db,
        device_id=MAC_ID,
        ip=IP,
        gen=1,
        model="SHSW-25",
        name=None,
        fw_version=None,
        profile=None,
        auth_required=False,
        raw_info=INFO,
    )
    rows = {row["id"]: row["ip"] for row in await db.fetchall("SELECT id, ip FROM devices")}
    assert rows[MAC_ID] == IP
    assert rows["shsw-1-AABBCCDDEEFF"] == "unassigned:shsw-1-AABBCCDDEEFF"


# --- #29: no scan left running forever ----------------------------------------


@pytest.mark.asyncio
async def test_unfinished_scans_are_closed_at_startup(db):
    await db.execute(
        "INSERT INTO scan_runs (id, started_at, method, found, errors) VALUES ('a', 'then', 'mdns', 0, '[]')"
    )
    await db.execute(
        "INSERT INTO scan_runs (id, started_at, ended_at, method, found, errors)"
        " VALUES ('b', 'then', 'later', 'mdns', 15, '[]')"
    )

    assert await close_unfinished_scans(db) == 1
    interrupted = await db.fetchone("SELECT * FROM scan_runs WHERE id = 'a'")
    assert interrupted["ended_at"] is not None
    assert "interrupted" in json.loads(interrupted["errors"])[0]
    finished = await db.fetchone("SELECT * FROM scan_runs WHERE id = 'b'")
    assert finished["ended_at"] == "later"  # untouched

    assert await close_unfinished_scans(db) == 0


# --- the #27 flow end to end ---------------------------------------------------


def settings_transport() -> "httpx.MockTransport":
    """A Gen1 device that answers /settings and /settings/actions."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/settings":
            return httpx.Response(200, json={**SETTINGS, "mode": "relay", "relays": [{}]})
        if request.url.path == "/settings/actions":
            return httpx.Response(200, json={"actions": {}})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_a_protected_device_and_its_unprotected_self_are_one_record(db):
    """Set a password, scan, remove it, scan: one row, not two (#27)."""
    import httpx

    from app.discovery import inventory_device

    probe = ProbeResult(ip=IP, gen=1, info=INFO, auth_required=True)

    # Protected, no credentials stored: only a stub can be recorded.
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(401))
    ) as client:
        stub_id, error = await inventory_device(client, db, probe, {})
    assert "authentication required" in error
    assert await device_ids(db) == [MAC_ID]

    # Password removed: the full inventory must land on the same record.
    async with httpx.AsyncClient(transport=settings_transport()) as client:
        full_id, error = await inventory_device(client, db, probe, {})
    assert error is None
    assert full_id == stub_id == MAC_ID
    assert await device_ids(db) == [MAC_ID]

    device = await db.fetchone("SELECT * FROM devices WHERE id = ?", (MAC_ID,))
    assert device["name"] == "Office"
    assert device["auth_required"] == 0
    assert len(await db.fetchall("SELECT id FROM channels WHERE device_id = ?", (MAC_ID,))) == 1
