"""Authenticated devices (DoD #4): a 401 must always leave the UI a way back in."""

from __future__ import annotations

import httpx
import pytest

from app.adapters.base import ProbeResult
from app.db import Database
from app.discovery import inventory_device

PROBE = ProbeResult(
    ip="192.168.1.50", gen=1, info={"type": "SHSW-25", "mac": "AABBCCDDEEFF"}, auth_required=True
)
DEVICE_ID = "shsw-25-AABBCCDDEEFF"


async def probe_against_401(db: Database, credentials: dict[str, tuple[str, str]]):
    transport = httpx.MockTransport(lambda request: httpx.Response(401))
    async with httpx.AsyncClient(transport=transport) as client:
        return await inventory_device(client, db, PROBE, credentials)


async def auth_flag(db: Database) -> int | None:
    row = await db.fetchone("SELECT auth_required FROM devices WHERE id = ?", (DEVICE_ID,))
    return None if row is None else row["auth_required"]


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "mesh.db")
    await database.connect()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_401_without_credentials_records_the_device(db):
    device_id, error = await probe_against_401(db, {})
    assert device_id == DEVICE_ID
    assert "authentication required" in error
    assert await auth_flag(db) == 1


@pytest.mark.asyncio
async def test_rejected_credentials_still_flag_the_device(db):
    """A password changed on the device must not hide the credentials form."""
    device_id, error = await probe_against_401(db, {DEVICE_ID: ("admin", "stale")})
    assert device_id == DEVICE_ID
    assert "rejected" in error
    # Without this flag the drawer shows no credentials field and the device is
    # stuck with a password nobody can replace.
    assert await auth_flag(db) == 1
