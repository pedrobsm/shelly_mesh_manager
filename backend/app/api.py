"""Phase 1 HTTP API (SPEC §3.6)."""

from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Body, HTTPException, Request

from . import __version__
from .adapters.base import channel_label, slot_label
from .generator import UnsupportedCommand, build_url, supported_commands
from .config import Settings
from .db import Database
from .discovery import ScanManager, rescan_device
from .graph import build_graph, load_device_refs
from .models import (
    ActionSlot,
    ActionUrl,
    AppSettings,
    BuildRequest,
    BuiltUrlResponse,
    Channel,
    Credentials,
    Device,
    DeviceDetail,
    Graph,
    Health,
    LayoutEntry,
    Ok,
    ParseRequest,
    RescanResult,
    ScanStarted,
    ScanStatus,
    SlotDetail,
    SlotUpdate,
    SlotUrl,
    UrlParse,
    UrlTestRequest,
    UrlTestResult,
)
from .slots import SlotWriteError, apply_slot_update, revision_of, test_url
from .resolver import external_label, format_command_label, resolve

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

    gens = {row["id"]: row["gen"] for row in device_rows}
    channels_by_device: dict[str, list[Channel]] = {}
    for row in channel_rows:
        commands = supported_commands(gens.get(row["device_id"], 1), row["kind"])
        channels_by_device.setdefault(row["device_id"], []).append(
            Channel(
                id=row["id"],
                kind=row["kind"],
                idx=row["idx"],
                name=row["name"],
                label=row["name"] or channel_label(row["kind"], row["idx"]),
                commands={command: list(params) for command, params in commands.items()},
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
async def start_scan(request: Request, scope: Literal["full", "known"] = "full") -> ScanStarted:
    """`full` discovers; `known` re-reads only the devices already inventoried."""
    scans = _scans(request)
    if scans.running:
        raise _fail(409, "scan_running", "a scan is already running")
    try:
        scan_id = await scans.start(scope)
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


@router.post("/devices/{device_id}/rescan", response_model=RescanResult)
async def rescan(request: Request, device_id: str) -> RescanResult:
    """Re-read one device without a full scan. Read-only towards the device."""
    db = _db(request)
    row = await db.fetchone("SELECT id FROM devices WHERE id = ?", (device_id,))
    if row is None:
        raise _fail(404, "not_found", f"no device {device_id}")
    if _scans(request).running:
        raise _fail(409, "scan_running", "a full scan is running; try again when it finishes")

    error = await rescan_device(db, _settings(request), device_id)
    state = await db.fetchone("SELECT online FROM devices WHERE id = ?", (device_id,))
    # An unreachable device is a result, not a server error: the node goes offline.
    return RescanResult(
        ok=error is None, online=bool(state["online"]) if state is not None else False, error=error
    )


# --- Phase 2: the guided action builder (SPEC §5.1) ---------------------------


async def _parse_url(db: Database, raw_url: str, *, slot_enabled: bool = True) -> UrlParse:
    """Resolve one URL and dress it up for the form: names, labels, a summary."""
    refs = await load_device_refs(db)
    result = resolve(raw_url, refs, slot_enabled=slot_enabled)

    device_name: str | None = None
    channel_label_text: str | None = None
    if result.target_device_id:
        row = await db.fetchone(
            "SELECT name, id FROM devices WHERE id = ?", (result.target_device_id,)
        )
        device_name = (row["name"] or row["id"]) if row is not None else None
    if result.target_channel_id:
        row = await db.fetchone(
            "SELECT kind, idx, name FROM channels WHERE id = ?", (result.target_channel_id,)
        )
        if row is not None:
            channel_label_text = row["name"] or channel_label(row["kind"], row["idx"])

    if result.target_type == "external" and result.status != "unparsed":
        label = external_label(raw_url, result.webhook_id)
    else:
        label = format_command_label(result.command, result.params, result.status)

    if result.target_type == "device":
        target = " · ".join(filter(None, [device_name, channel_label_text])) or "device"
    elif result.target_type == "unknown_shelly":
        target = f"{result.external_host} (not in inventory)"
    else:
        target = "Home Assistant" if result.webhook_id else (result.external_host or "external")

    return UrlParse(
        raw_url=raw_url,
        target_type=result.target_type,
        status=result.status,
        command=result.command,
        label=label,
        params=result.params,
        target_device_id=result.target_device_id,
        target_device_name=device_name,
        channel_id=result.target_channel_id,
        channel_label=channel_label_text,
        external_host=result.external_host,
        external_path=result.external_path,
        webhook_id=result.webhook_id,
        summary=f"→ {target} · {label}",
    )


@router.get("/slots/{slot_id}", response_model=SlotDetail)
async def get_slot(request: Request, slot_id: str) -> SlotDetail:
    db = _db(request)
    row = await db.fetchone(
        """
        SELECT s.*, d.name AS device_name, d.gen AS device_gen
        FROM action_slots s JOIN devices d ON d.id = s.device_id
        WHERE s.id = ?
        """,
        (slot_id,),
    )
    if row is None:
        raise _fail(404, "not_found", f"no slot {slot_id}")

    url_rows = await db.fetchall(
        "SELECT * FROM action_urls WHERE slot_id = ? ORDER BY position", (slot_id,)
    )
    urls = [
        SlotUrl(
            id=url["id"],
            position=url["position"],
            raw_url=url["raw_url"],
            parsed=await _parse_url(db, url["raw_url"], slot_enabled=bool(row["enabled"])),
        )
        for url in url_rows
    ]
    return SlotDetail(
        id=row["id"],
        revision=revision_of(bool(row["enabled"]), [url.raw_url for url in urls]),
        device_id=row["device_id"],
        device_name=row["device_name"],
        device_gen=row["device_gen"],
        source_kind=row["source_kind"],
        source_idx=row["source_idx"],
        event=row["event"],
        native_key=row["native_key"],
        enabled=bool(row["enabled"]),
        name=row["name"],
        label=slot_label(row["source_kind"], row["source_idx"], row["event"]),
        urls=urls,
    )


@router.put("/slots/{slot_id}", response_model=SlotDetail)
async def put_slot(request: Request, slot_id: str, body: SlotUpdate) -> SlotDetail:
    """Write a slot to its device, verify it, and return the state that stuck."""
    try:
        # Creating a Gen2/3 webhook gives the slot the id the device chose.
        slot_id = await apply_slot_update(_db(request), _settings(request), slot_id, body)
    except SlotWriteError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"error": exc.code, "detail": exc.detail, **exc.extra},
        ) from exc
    return await get_slot(request, slot_id)


@router.post("/url/test", response_model=UrlTestResult)
async def post_url_test(request: Request, body: UrlTestRequest) -> UrlTestResult:
    """Fire one URL from the server. On real hardware this really acts."""
    return await test_url(_settings(request), body.url)


@router.post("/url/parse", response_model=UrlParse)
async def parse_url(request: Request, body: ParseRequest) -> UrlParse:
    return await _parse_url(_db(request), body.raw_url)


@router.post("/url/build", response_model=BuiltUrlResponse)
async def build_action_url(request: Request, body: BuildRequest) -> BuiltUrlResponse:
    """The server, not the client, generates every Shelly-target URL (§5.1)."""
    db = _db(request)
    device = await db.fetchone(
        "SELECT id, ip, gen, name FROM devices WHERE id = ?", (body.target_device_id,)
    )
    if device is None:
        raise _fail(404, "not_found", f"no device {body.target_device_id}")
    channel = await db.fetchone(
        "SELECT id, kind, idx, name FROM channels WHERE id = ? AND device_id = ?",
        (body.channel_id, body.target_device_id),
    )
    if channel is None:
        raise _fail(
            404, "not_found", f"device {body.target_device_id} has no channel {body.channel_id}"
        )

    try:
        built = build_url(
            device["ip"], device["gen"], channel["kind"], channel["idx"], body.command, body.params
        )
    except UnsupportedCommand as exc:
        raise _fail(422, "unsupported_command", str(exc)) from exc

    name = device["name"] or device["id"]
    label = format_command_label(built.command, built.params, "ok")
    target_label = channel["name"] or channel_label(channel["kind"], channel["idx"])
    return BuiltUrlResponse(
        url=built.url,
        command=built.command,
        channel_id=channel["id"],
        channel_kind=built.channel_kind,
        channel_idx=built.channel_idx,
        params=built.params,
        summary=f"→ {name} · {target_label} · {label}",
    )


@router.get("/settings", response_model=AppSettings)
async def get_settings(request: Request) -> AppSettings:
    row = await _db(request).fetchone(
        "SELECT value FROM app_settings WHERE key = 'ha_base_url'"
    )
    return AppSettings(ha_base_url=row["value"] if row is not None else None)


@router.put("/settings", response_model=AppSettings)
async def put_settings(request: Request, body: AppSettings) -> AppSettings:
    value = (body.ha_base_url or "").strip().rstrip("/")
    db = _db(request)
    if value:
        await db.execute(
            """
            INSERT INTO app_settings (key, value) VALUES ('ha_base_url', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (value,),
        )
    else:
        await db.execute("DELETE FROM app_settings WHERE key = 'ha_base_url'")
    return AppSettings(ha_base_url=value or None)


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
