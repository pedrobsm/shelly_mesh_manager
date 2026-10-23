"""Phase 1 HTTP API (SPEC §3.6)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Body, HTTPException, Request

from . import __version__
from .adapters.base import channel_label, slot_label
from .config import Settings
from .db import Database
from .discovery import ScanManager, rescan_device
from .graph import build_graph
from .models import (
    ActionSlot,
    ActionUrl,
    Channel,
    Credentials,
    Device,
    DeviceDetail,
    Graph,
    Health,
    LayoutEntry,
    Ok,
    ScanStarted,
    ScanStatus,
)

router = APIRouter(prefix="/api")


def _db(request: Request) -> Database:
    return request.app.state.db


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _scans(request: Request) -> ScanManager:
    return request.app.state.scans


def _fail(status_code: int, error: str, detail: str | None = None) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": error, "detail": detail})


async def _devices(db: Database, device_id: str | None = None) -> list[Device]:
    where, params = ("WHERE id = ?", (device_id,)) if device_id else ("", ())
    device_rows = await db.fetchall(
        f"SELECT * FROM devices {where} ORDER BY name, id", params
    )
    if not device_rows:
        return []
    ids = [row["id"] for row in device_rows]
    placeholders = ",".join("?" for _ in ids)
    channel_rows = await db.fetchall(
        f"SELECT * FROM channels WHERE device_id IN ({placeholders}) ORDER BY kind, idx", ids
    )
    slot_rows = await db.fetchall(
        f"""SELECT * FROM action_slots WHERE device_id IN ({placeholders})
            ORDER BY source_kind, source_idx, event""",
        ids,
    )
    url_rows = await db.fetchall(
        f"""SELECT u.* FROM action_urls u JOIN action_slots s ON s.id = u.slot_id
            WHERE s.device_id IN ({placeholders}) ORDER BY u.slot_id, u.position""",
        ids,
    )

    urls_by_slot: dict[str, list[ActionUrl]] = {}
    for row in url_rows:
        urls_by_slot.setdefault(row["slot_id"], []).append(
            ActionUrl(id=row["id"], position=row["position"], raw_url=row["raw_url"])
        )

    slots_by_device: dict[str, list[ActionSlot]] = {}
    for row in slot_rows:
        slots_by_device.setdefault(row["device_id"], []).append(
            ActionSlot(
                id=row["id"],
                source_kind=row["source_kind"],
                source_idx=row["source_idx"],
                event=row["event"],
                native_key=row["native_key"],
                enabled=bool(row["enabled"]),
                name=row["name"],
                label=slot_label(row["source_kind"], row["source_idx"], row["event"]),
                urls=urls_by_slot.get(row["id"], []),
            )
        )

    channels_by_device: dict[str, list[Channel]] = {}
    for row in channel_rows:
        channels_by_device.setdefault(row["device_id"], []).append(
            Channel(
                id=row["id"],
                kind=row["kind"],
                idx=row["idx"],
                name=row["name"],
                label=row["name"] or channel_label(row["kind"], row["idx"]),
            )
        )

    return [
        Device(
            id=row["id"],
            ip=row["ip"],
            gen=row["gen"],
            model=row["model"],
            name=row["name"],
            fw_version=row["fw_version"],
            profile=row["profile"],
            auth_required=bool(row["auth_required"]),
            online=bool(row["online"]),
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            channels=channels_by_device.get(row["id"], []),
            slots=slots_by_device.get(row["id"], []),
        )
        for row in device_rows
    ]


@router.get("/health", response_model=Health)
async def health(request: Request) -> Health:
    return Health(status="ok", version=__version__, demo=_settings(request).demo_mode)


@router.post("/scan", response_model=ScanStarted, status_code=202)
async def start_scan(request: Request) -> ScanStarted:
    scans = _scans(request)
    if scans.running:
        raise _fail(409, "scan_running", "a scan is already running")
    try:
        scan_id = await scans.start()
    except RuntimeError as exc:
        raise _fail(409, "scan_running", str(exc)) from exc
    return ScanStarted(scan_id=scan_id)


@router.get("/scan/{scan_id}", response_model=ScanStatus)
async def scan_status(request: Request, scan_id: str) -> ScanStatus:
    row = await _db(request).fetchone("SELECT * FROM scan_runs WHERE id = ?", (scan_id,))
    if row is None:
        raise _fail(404, "not_found", f"no scan {scan_id}")
    errors = json.loads(row["errors"] or "[]")
    if row["ended_at"] is None:
        status = "running"
    else:
        status = "error" if errors and row["found"] == 0 else "done"
    return ScanStatus(
        status=status,
        found=row["found"] or 0,
        errors=errors,
        method=row["method"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
    )


@router.get("/devices", response_model=list[Device])
async def list_devices(request: Request) -> list[Device]:
    return await _devices(_db(request))


@router.get("/devices/{device_id}", response_model=DeviceDetail)
async def get_device(request: Request, device_id: str) -> DeviceDetail:
    db = _db(request)
    devices = await _devices(db, device_id)
    if not devices:
        raise _fail(404, "not_found", f"no device {device_id}")
    device = devices[0]
    row = await db.fetchone("SELECT raw_info FROM devices WHERE id = ?", (device_id,))
    creds = await db.fetchone("SELECT 1 AS x FROM credentials WHERE device_id = ?", (device_id,))
    snapshots = await db.fetchall(
        "SELECT taken_at FROM config_snapshots WHERE device_id = ? ORDER BY taken_at DESC",
        (device_id,),
    )
    raw_info = None
    if row is not None and row["raw_info"]:
        try:
            raw_info = json.loads(row["raw_info"])
        except json.JSONDecodeError:
            raw_info = None
    return DeviceDetail(
        **device.model_dump(),
        raw_info=raw_info,
        has_credentials=creds is not None,
        snapshot_count=len(snapshots),
        last_snapshot_at=snapshots[0]["taken_at"] if snapshots else None,
    )


@router.post("/devices/{device_id}/credentials", response_model=Ok)
async def set_credentials(request: Request, device_id: str, body: Credentials) -> Ok:
    db = _db(request)
    row = await db.fetchone("SELECT id FROM devices WHERE id = ?", (device_id,))
    if row is None:
        raise _fail(404, "not_found", f"no device {device_id}")
    await db.execute(
        """
        INSERT INTO credentials (device_id, username, password) VALUES (?, ?, ?)
        ON CONFLICT(device_id) DO UPDATE SET username = excluded.username,
                                            password = excluded.password
        """,
        (device_id, body.username, body.password),
    )
    error = await rescan_device(db, _settings(request), device_id)
    if error:
        raise _fail(502, "reprobe_failed", error)
    return Ok()


@router.get("/graph", response_model=Graph)
async def get_graph(request: Request) -> Graph:
    return await build_graph(_db(request))


@router.put("/layout", response_model=Ok)
async def put_layout(request: Request, entries: list[LayoutEntry] = Body(...)) -> Ok:
    await _db(request).executemany(
        """
        INSERT INTO node_layout (node_id, x, y) VALUES (?, ?, ?)
        ON CONFLICT(node_id) DO UPDATE SET x = excluded.x, y = excluded.y
        """,
        [(entry.node_id, entry.x, entry.y) for entry in entries],
    )
    return Ok()
