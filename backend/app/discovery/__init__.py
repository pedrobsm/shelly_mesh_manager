"""Scan orchestration: discover -> probe -> inventory -> store -> rebuild edges."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

from ..adapters import base as adapters_base
from ..adapters import gen1, gen2
from ..adapters.base import DeviceInventory, ProbeResult
from ..config import Settings
from ..db import Database
from ..graph import rebuild_edges
from . import mdns, range_scan

log = logging.getLogger(__name__)

CONCURRENCY = 16
SNAPSHOTS_PER_DEVICE = 10


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def auth_for(gen: int, username: str, password: str) -> httpx.Auth:
    """Basic on Gen1, digest on Gen2/3 (SPEC §3.4.5)."""
    if gen >= 2:
        return httpx.DigestAuth(username, password)
    return httpx.BasicAuth(username, password)


async def load_credentials(db: Database) -> dict[str, tuple[str, str]]:
    rows = await db.fetchall("SELECT device_id, username, password FROM credentials")
    return {row["device_id"]: (row["username"], row["password"]) for row in rows}


async def store_snapshot(db: Database, device_id: str, config_json: str) -> bool:
    """Store a config snapshot when it differs from the newest one (keep last 10)."""
    latest = await db.fetchone(
        "SELECT config FROM config_snapshots WHERE device_id = ? ORDER BY taken_at DESC LIMIT 1",
        (device_id,),
    )
    if latest is not None and latest["config"] == config_json:
        return False
    await db.execute(
        "INSERT INTO config_snapshots (id, device_id, taken_at, config) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), device_id, now_iso(), config_json),
    )
    await db.execute(
        """
        DELETE FROM config_snapshots
        WHERE device_id = ? AND id NOT IN (
          SELECT id FROM config_snapshots WHERE device_id = ? ORDER BY taken_at DESC LIMIT ?
        )
        """,
        (device_id, device_id, SNAPSHOTS_PER_DEVICE),
    )
    return True


async def upsert_device(
    db: Database,
    *,
    device_id: str,
    ip: str,
    gen: int,
    model: str,
    name: str | None,
    fw_version: str | None,
    profile: str | None,
    auth_required: bool,
    raw_info: dict[str, Any] | None,
) -> None:
    """Upsert by device id — the IP may change, the id may not (SPEC §3.4.6)."""
    timestamp = now_iso()
    # A recycled IP must not collide with the UNIQUE(ip) constraint: the previous holder
    # of this address keeps its row (never auto-delete) but loses the address.
    await db.execute(
        "UPDATE devices SET ip = 'unassigned:' || id, online = 0 WHERE ip = ? AND id <> ?",
        (ip, device_id),
    )
    await db.execute(
        """
        INSERT INTO devices (id, ip, gen, model, name, fw_version, profile, auth_required,
                             online, first_seen, last_seen, raw_info)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          ip = excluded.ip, gen = excluded.gen, model = excluded.model,
          name = COALESCE(excluded.name, devices.name),
          fw_version = COALESCE(excluded.fw_version, devices.fw_version),
          profile = COALESCE(excluded.profile, devices.profile),
          auth_required = excluded.auth_required, online = 1,
          last_seen = excluded.last_seen,
          raw_info = COALESCE(excluded.raw_info, devices.raw_info)
        """,
        (
            device_id,
            ip,
            gen,
            model,
            name,
            fw_version,
            profile,
            int(auth_required),
            timestamp,
            timestamp,
            json.dumps(raw_info or {}),
        ),
    )


async def store_inventory(db: Database, inv: DeviceInventory) -> None:
    """Replace the stored channels / slots / urls of one device."""
    await upsert_device(
        db,
        device_id=inv.id,
        ip=inv.ip,
        gen=inv.gen,
        model=inv.model,
        name=inv.name,
        fw_version=inv.fw_version,
        profile=inv.profile,
        auth_required=inv.auth_required,
        raw_info=inv.raw_info,
    )
    await db.execute("DELETE FROM channels WHERE device_id = ?", (inv.id,))
    await db.executemany(
        "INSERT INTO channels (id, device_id, kind, idx, name) VALUES (?, ?, ?, ?, ?)",
        [
            (f"{inv.id}:{ch.kind}:{ch.idx}", inv.id, ch.kind, ch.idx, ch.name)
            for ch in inv.channels
        ],
    )
    await db.execute("DELETE FROM action_slots WHERE device_id = ?", (inv.id,))
    await db.executemany(
        """
        INSERT INTO action_slots (id, device_id, source_kind, source_idx, event, native_key,
                                  enabled, name)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                slot.id,
                inv.id,
                slot.source_kind,
                slot.source_idx,
                slot.event,
                slot.native_key,
                int(slot.enabled),
                slot.name,
            )
            for slot in inv.slots
        ],
    )
    await db.executemany(
        "INSERT INTO action_urls (id, slot_id, position, raw_url) VALUES (?, ?, ?, ?)",
        [
            (f"{slot.id}#{position}", slot.id, position, url)
            for slot in inv.slots
            for position, url in enumerate(slot.urls)
        ],
    )
    if inv.config:
        await store_snapshot(db, inv.id, inv.config_json())


def partial_identity(probe_result: ProbeResult) -> tuple[str, str]:
    """(device_id, model) for a device that answered /shelly but refused inventory."""
    info = probe_result.info or {}
    if probe_result.gen >= 2:
        device_id = str(info.get("id") or info.get("mac") or probe_result.ip)
        return device_id, str(info.get("model") or "unknown")
    mac = str(info.get("mac") or "").upper()
    model = str(info.get("type") or "unknown")
    device_id = f"{model.lower()}-{mac}" if mac else f"shelly-{probe_result.ip}"
    return device_id, model


async def inventory_device(
    client: httpx.AsyncClient,
    db: Database,
    probe_result: ProbeResult,
    credentials: dict[str, tuple[str, str]],
) -> tuple[str, str | None]:
    """Inventory one device. Returns (device_id, error message or None)."""
    device_id, model = partial_identity(probe_result)
    adapter = gen2 if probe_result.gen >= 2 else gen1
    creds = credentials.get(device_id)
    auth = auth_for(probe_result.gen, *creds) if creds else None

    try:
        inv = await adapter.inventory(client, probe_result, auth)
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code == 401:
            if creds is None:
                await upsert_device(
                    db,
                    device_id=device_id,
                    ip=probe_result.ip,
                    gen=probe_result.gen,
                    model=model,
                    name=None,
                    fw_version=str((probe_result.info or {}).get("fw") or "") or None,
                    profile=None,
                    auth_required=True,
                    raw_info=probe_result.info,
                )
                return device_id, f"{probe_result.ip}: authentication required"
            return device_id, f"{probe_result.ip}: credentials rejected"
        return device_id, f"{probe_result.ip}: HTTP {exc.response.status_code if exc.response else '?'}"
    except Exception as exc:  # a misbehaving device never aborts the scan
        return device_id, f"{probe_result.ip}: {exc}"

    await store_inventory(db, inv)
    return inv.id, None


async def scan_targets(
    db: Database, settings: Settings, ips: Iterable[str]
) -> tuple[list[str], list[str]]:
    """Probe + inventory a list of candidate IPs. Returns (device_ids, errors)."""
    credentials = await load_credentials(db)
    semaphore = asyncio.Semaphore(CONCURRENCY)
    device_ids: list[str] = []
    errors: list[str] = []
    timeout = httpx.Timeout(settings.http_timeout_s)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:

        async def worker(ip: str) -> None:
            async with semaphore:
                probe_result = await adapters_base.probe(client, ip)
                if probe_result is None:
                    return
                device_id, error = await inventory_device(client, db, probe_result, credentials)
                device_ids.append(device_id)
                if error:
                    errors.append(error)

        await asyncio.gather(*(worker(ip) for ip in ips), return_exceptions=False)

    return device_ids, errors


def scan_method(settings: Settings) -> str:
    if settings.demo_mode:
        return "demo"
    return "range" if settings.scan_subnet else "mdns"


async def run_scan(
    db: Database, settings: Settings, method: str | None = None, scan_id: str | None = None
) -> dict[str, Any]:
    """A full scan: discover, inventory, mark absentees offline, rebuild edges."""
    method = method or scan_method(settings)
    if scan_id is None:
        scan_id = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO scan_runs (id, started_at, method, found, errors) VALUES (?, ?, ?, 0, ?)",
            (scan_id, now_iso(), method, json.dumps([])),
        )

    errors: list[str] = []
    device_ids: list[str] = []
    try:
        if settings.demo_mode:
            from ..demo_fixtures import seed_demo

            device_ids = await seed_demo(db)
        else:
            candidates = mdns.merge_candidates(
                await mdns.browse(), range_scan.expand(settings.scan_subnet)
            )
            if not candidates:
                errors.append("no candidates found (mDNS returned nothing, SCAN_SUBNET empty)")
            device_ids, errors_found = await scan_targets(db, settings, candidates)
            errors.extend(errors_found)
            if device_ids:
                placeholders = ",".join("?" for _ in device_ids)
                await db.execute(
                    f"UPDATE devices SET online = 0 WHERE id NOT IN ({placeholders})",
                    device_ids,
                )
            else:
                await db.execute("UPDATE devices SET online = 0")

        await rebuild_edges(db)
        status = "done"
    except Exception as exc:  # pragma: no cover - defensive; a scan never crashes the app
        log.exception("scan failed")
        errors.append(str(exc))
        status = "error"

    await db.execute(
        "UPDATE scan_runs SET ended_at = ?, found = ?, errors = ? WHERE id = ?",
        (now_iso(), len(device_ids), json.dumps(errors), scan_id),
    )
    return {"scan_id": scan_id, "status": status, "found": len(device_ids), "errors": errors}


async def rescan_device(db: Database, settings: Settings, device_id: str) -> str | None:
    """Re-probe a single device (used after credentials are stored). Returns an error."""
    row = await db.fetchone("SELECT ip FROM devices WHERE id = ?", (device_id,))
    if row is None:
        return "unknown device"
    if settings.demo_mode:
        await rebuild_edges(db)
        return None
    credentials = await load_credentials(db)
    timeout = httpx.Timeout(settings.http_timeout_s)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        probe_result = await adapters_base.probe(client, row["ip"])
        if probe_result is None:
            await db.execute("UPDATE devices SET online = 0 WHERE id = ?", (device_id,))
            return "device unreachable"
        _, error = await inventory_device(client, db, probe_result, credentials)
    await rebuild_edges(db)
    return error


class ScanManager:
    """One scan at a time (SPEC §3.6: POST /api/scan returns 409 while one runs)."""

    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self._task: asyncio.Task | None = None
        self.current_id: str | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> str:
        if self.running:
            raise RuntimeError("a scan is already running")
        scan_id = str(uuid.uuid4())
        await self.db.execute(
            "INSERT INTO scan_runs (id, started_at, method, found, errors) VALUES (?, ?, ?, 0, ?)",
            (scan_id, now_iso(), scan_method(self.settings), json.dumps([])),
        )
        self.current_id = scan_id
        self._task = asyncio.create_task(self._run(scan_id))
        return scan_id

    async def _run(self, scan_id: str) -> None:
        await run_scan(self.db, self.settings, scan_id=scan_id)
