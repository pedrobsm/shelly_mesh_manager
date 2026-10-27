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
from ..adapters.base import (
    DeviceInventory,
    ProbeResult,
    gen1_device_id,
    canonical_json,
    changed_paths,
    strip_volatile,
)
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


# --- device identity upkeep (issues #27, #28) ---------------------------------


def canonical_device_id(row: Any) -> str | None:
    """The id a stored device *should* have, computed from its own raw_info.

    Gen2/3 ids come straight from the device and are already MAC-derived; only
    Gen1 ever disagreed, because the id used to be taken from the hostname.
    """
    try:
        info = json.loads(row["raw_info"] or "{}")
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(info, dict):
        return None
    if (row["gen"] or 1) >= 2:
        return str(info.get("id") or "") or None
    if not info.get("mac"):
        return None
    return gen1_device_id(info.get("type") or row["model"], info.get("mac"))


def _merged_values(keeper: Any, stale: Any) -> tuple:
    """Field values for the surviving row: the newer reading wins, nulls fall back."""
    newer, older = (keeper, stale) if keeper["last_seen"] >= stale["last_seen"] else (stale, keeper)

    def pick(field: str) -> Any:
        return newer[field] if newer[field] is not None else older[field]

    ip = newer["ip"]
    if ip.startswith(("migrating:", "unassigned:")):
        ip = older["ip"]
    return (
        ip,
        pick("gen"),
        pick("model"),
        pick("name"),
        pick("fw_version"),
        pick("profile"),
        newer["auth_required"],
        newer["online"],
        min(keeper["first_seen"], stale["first_seen"]),
        max(keeper["last_seen"], stale["last_seen"]),
        pick("raw_info"),
    )


async def reassign_device_id(db: Database, stale_id: str, keeper_id: str) -> bool:
    """Move a device record onto its canonical id, keeping what cannot be re-read.

    Channels, slots and URLs are rebuilt by the next scan, so they ride along by
    being dropped. Credentials, config snapshots and the node's position cannot
    be re-derived, so they move explicitly — losing them is what made the old
    'park the row at unassigned:' behaviour a data-loss bug (#27).
    """
    if stale_id == keeper_id:
        return False
    stale = await db.fetchone("SELECT * FROM devices WHERE id = ?", (stale_id,))
    if stale is None:
        return False

    # Free the unique IP before the keeper can claim it.
    await db.execute("UPDATE devices SET ip = 'migrating:' || id WHERE id = ?", (stale_id,))
    keeper = await db.fetchone("SELECT * FROM devices WHERE id = ?", (keeper_id,))

    if keeper is None:
        await db.execute(
            """
            INSERT INTO devices (id, ip, gen, model, name, fw_version, profile, auth_required,
                                 online, first_seen, last_seen, raw_info)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                keeper_id,
                stale["ip"],
                stale["gen"],
                stale["model"],
                stale["name"],
                stale["fw_version"],
                stale["profile"],
                stale["auth_required"],
                stale["online"],
                stale["first_seen"],
                stale["last_seen"],
                stale["raw_info"],
            ),
        )
    else:
        await db.execute(
            """
            UPDATE devices SET ip = ?, gen = ?, model = ?, name = ?, fw_version = ?, profile = ?,
                   auth_required = ?, online = ?, first_seen = ?, last_seen = ?, raw_info = ?
            WHERE id = ?
            """,
            (*_merged_values(keeper, stale), keeper_id),
        )

    await db.execute(
        "UPDATE config_snapshots SET device_id = ? WHERE device_id = ?", (keeper_id, stale_id)
    )
    kept_credentials = await db.fetchone(
        "SELECT 1 AS present FROM credentials WHERE device_id = ?", (keeper_id,)
    )
    if kept_credentials is None:
        await db.execute(
            "UPDATE credentials SET device_id = ? WHERE device_id = ?", (keeper_id, stale_id)
        )
    kept_layout = await db.fetchone(
        "SELECT 1 AS present FROM node_layout WHERE node_id = ?", (keeper_id,)
    )
    if kept_layout is None:
        await db.execute(
            "UPDATE node_layout SET node_id = ? WHERE node_id = ?", (keeper_id, stale_id)
        )
    else:
        await db.execute("DELETE FROM node_layout WHERE node_id = ?", (stale_id,))

    # Cascades the stale record's channels, slots and URLs; the scan rebuilds them.
    await db.execute("DELETE FROM devices WHERE id = ?", (stale_id,))
    log.info("merged device record %s into %s", stale_id, keeper_id)
    return True


async def migrate_device_ids(db: Database) -> int:
    """Re-key any device stored under a non-canonical id (runs at startup).

    Without this, making Gen1 ids MAC-derived (#28) would make the next scan
    insert a second record for every Gen1 device on the network.
    """
    moved = 0
    for row in await db.fetchall("SELECT * FROM devices"):
        canonical = canonical_device_id(row)
        if canonical and canonical != row["id"]:
            moved += int(await reassign_device_id(db, row["id"], canonical))
    if moved:
        log.info("re-keyed %d device record(s) onto their MAC-derived id", moved)
    return moved


async def close_unfinished_scans(db: Database) -> int:
    """Finish scan_runs rows left open by a restart mid-scan (issue #29)."""
    rows = await db.fetchall("SELECT id FROM scan_runs WHERE ended_at IS NULL")
    if not rows:
        return 0
    await db.executemany(
        "UPDATE scan_runs SET ended_at = ?, errors = ? WHERE id = ?",
        [
            (now_iso(), json.dumps(["interrupted: the app stopped while this scan was running"]), row["id"])
            for row in rows
        ],
    )
    log.info("closed %d scan run(s) interrupted by a restart", len(rows))
    return len(rows)


async def store_snapshot(db: Database, device_id: str, config: dict[str, Any]) -> bool:
    """Store a config snapshot when the config really changed (keep the last 10).

    Comparison ignores the volatile fields of `VOLATILE_KEYS` — Gen1 `/settings`
    carries a clock and other per-read values, which otherwise made every scan
    look like a config change and filled the ring with noise (issue #12). What is
    stored stays the verbatim config, since Phase 4 restores a device from it.
    """
    latest = await db.fetchone(
        """
        SELECT config FROM config_snapshots
        WHERE device_id = ? ORDER BY taken_at DESC, rowid DESC LIMIT 1
        """,
        (device_id,),
    )
    stable = strip_volatile(config)

    if latest is not None:
        try:
            previous = strip_volatile(json.loads(latest["config"]))
        except json.JSONDecodeError:
            previous = None
        if previous is not None:
            if canonical_json(previous) == canonical_json(stable):
                return False
            # Name what moved: on real hardware this is how an unknown volatile
            # field gets found and added to VOLATILE_KEYS.
            log.info(
                "config changed on %s: %s",
                device_id,
                ", ".join(changed_paths(previous, stable)) or "(unknown)",
            )

    await db.execute(
        "INSERT INTO config_snapshots (id, device_id, taken_at, config) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), device_id, now_iso(), canonical_json(config)),
    )
    await db.execute(
        """
        DELETE FROM config_snapshots
        WHERE device_id = ? AND id NOT IN (
          SELECT id FROM config_snapshots
          WHERE device_id = ? ORDER BY taken_at DESC, rowid DESC LIMIT ?
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
    # Another record holding this IP is either the same device under a stale id —
    # merge into this one (#27) — or a different device that took the address,
    # which keeps its row (never auto-delete) but loses the address.
    holder = await db.fetchone(
        "SELECT * FROM devices WHERE ip = ? AND id <> ?", (ip, device_id)
    )
    if holder is not None:
        if canonical_device_id(holder) == device_id:
            await reassign_device_id(db, holder["id"], device_id)
        else:
            await db.execute(
                "UPDATE devices SET ip = 'unassigned:' || id, online = 0 WHERE id = ?",
                (holder["id"],),
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
        await store_snapshot(db, inv.id, inv.config)


def partial_identity(probe_result: ProbeResult) -> tuple[str, str]:
    """(device_id, model) for a device that answered /shelly but refused inventory."""
    info = probe_result.info or {}
    if probe_result.gen >= 2:
        device_id = str(info.get("id") or info.get("mac") or probe_result.ip)
        return device_id, str(info.get("model") or "unknown")
    model = str(info.get("type") or "unknown")
    # The same function the full inventory uses, so a protected device is not
    # recorded under a second id (issue #27).
    device_id = gen1_device_id(model, info.get("mac"), fallback=f"shelly-{probe_result.ip}")
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
            # Mark the device as needing credentials whether or not some are
            # stored: a password changed on the device rejects what we have, and
            # the UI only offers the credentials form while auth_required is set.
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
            if creds is None:
                return device_id, f"{probe_result.ip}: authentication required"
            return device_id, f"{probe_result.ip}: stored credentials rejected"
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
        try:
            await run_scan(self.db, self.settings, scan_id=scan_id)
        except asyncio.CancelledError:
            # Best effort: the startup reconciliation closes the row if this
            # write does not land either (#29).
            await self.db.execute(
                "UPDATE scan_runs SET ended_at = ?, errors = ? WHERE id = ? AND ended_at IS NULL",
                (now_iso(), json.dumps(["interrupted: the scan was cancelled"]), scan_id),
            )
            raise

    async def cancel(self) -> None:
        """Stop the in-flight scan, if any (shutdown path)."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):  # noqa: B014 - shutdown is best effort
            pass
