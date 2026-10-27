"""Phase 1 API surface (SPEC §3.6), exercised in demo mode."""

from __future__ import annotations

import asyncio
import importlib
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app.config
    import app.main

    importlib.reload(app.config)
    importlib.reload(app.main)
    return app.main


@pytest.fixture
def client(app_module):
    with TestClient(app_module.app) as test_client:
        yield test_client


@pytest.fixture
def live_client(tmp_path, monkeypatch):
    """A non-demo app whose scans are stubbed out — no network, no mDNS wait."""
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SCAN_INTERVAL_MIN", "0")
    import app.config
    import app.main

    importlib.reload(app.config)
    importlib.reload(app.main)

    async def slow_scan(db, settings, method=None, scan_id=None):
        await asyncio.sleep(0.4)
        return {"scan_id": scan_id, "status": "done", "found": 0, "errors": []}

    monkeypatch.setattr("app.discovery.run_scan", slow_scan)
    with TestClient(app.main.app) as test_client:
        yield test_client


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["demo"] is True


def test_graph_is_seeded_on_startup(client):
    graph = client.get("/api/graph").json()
    assert len(graph["nodes"]) == 8
    assert len(graph["edges"]) == 7


def test_devices_include_channels_slots_and_urls(client):
    devices = client.get("/api/devices").json()
    assert len(devices) == 6
    livingroom = next(d for d in devices if d["id"] == "shelly25-livingroom")
    assert [c["kind"] for c in livingroom["channels"]] == ["relay", "relay"]
    slot = next(s for s in livingroom["slots"] if s["native_key"] == "btn_on_url")
    assert slot["urls"][0]["raw_url"] == "http://192.168.1.11/relay/0?turn=on"


def test_device_detail_and_missing_device(client):
    detail = client.get("/api/devices/shellyplus1-hall").json()
    assert detail["raw_info"]["model"] == "SNSW-001X16EU"
    assert detail["snapshot_count"] == 1

    missing = client.get("/api/devices/nope")
    assert missing.status_code == 404
    assert missing.json()["error"] == "not_found"


def test_layout_round_trip(client):
    response = client.put(
        "/api/layout", json=[{"node_id": "shelly25-livingroom", "x": 12.5, "y": -3.0}]
    )
    assert response.status_code == 200
    node = next(
        n for n in client.get("/api/graph").json()["nodes"] if n["id"] == "shelly25-livingroom"
    )
    assert node["position"] == {"x": 12.5, "y": -3.0}


def test_scan_runs_and_reports_status(client):
    scan_id = client.post("/api/scan").json()["scan_id"]
    for _ in range(50):
        state = client.get(f"/api/scan/{scan_id}").json()
        if state["status"] != "running":
            break
        time.sleep(0.05)
    assert state["status"] == "done"
    assert state["found"] == 6
    assert state["method"] == "demo"
    assert client.get("/api/scan/unknown").status_code == 404


def test_rescan_single_device(client):
    response = client.post("/api/devices/shellyplus1-hall/rescan")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "online": True, "error": None}
    # The edge cache is rebuilt, so the graph still resolves after a single-device pass.
    assert len(client.get("/api/graph").json()["edges"]) == 7


def test_rescan_unknown_device_is_404(client):
    response = client.post("/api/devices/nope/rescan")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_rescan_during_a_full_scan_is_409(client, monkeypatch):
    async def slow_scan(db, settings, method=None, scan_id=None):
        await asyncio.sleep(0.5)
        return {"scan_id": scan_id, "status": "done", "found": 0, "errors": []}

    monkeypatch.setattr("app.discovery.run_scan", slow_scan)
    assert client.post("/api/scan").status_code == 202
    conflict = client.post("/api/devices/shellyplus1-hall/rescan")
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "scan_running"


def test_boot_scan_blocks_a_second_scan(live_client):
    """Issue #11: the startup scan must hold the ScanManager, not bypass it."""
    response = live_client.post("/api/scan")
    assert response.status_code == 409
    assert response.json()["error"] == "scan_running"

    runs = live_client.get("/api/scan/does-not-exist")
    assert runs.status_code == 404


def test_boot_scan_records_exactly_one_run(live_client, tmp_path):
    # The stubbed scan takes 0.4 s; stay well inside that while hammering.
    codes = set()
    for _ in range(10):
        codes.add(live_client.post("/api/scan").status_code)
        time.sleep(0.01)
    assert codes == {409}

    connection = sqlite3.connect(tmp_path / "mesh.db")
    try:
        rows = connection.execute("SELECT id FROM scan_runs").fetchall()
    finally:
        connection.close()
    assert len(rows) == 1  # the boot scan, and nothing else


def test_second_scan_while_one_runs_is_409(client, app_module, monkeypatch):
    async def slow_scan(db, settings, method=None, scan_id=None):
        await asyncio.sleep(0.5)
        return {"scan_id": scan_id, "status": "done", "found": 0, "errors": []}

    monkeypatch.setattr("app.discovery.run_scan", slow_scan)
    assert client.post("/api/scan").status_code == 202
    conflict = client.post("/api/scan")
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "scan_running"


# --- Phase 2: the guided builder's read-only surface (SPEC §5.1) --------------


def test_channels_carry_the_commands_the_builder_can_emit(client):
    hall = client.get("/api/devices/shellyplus1-hall").json()
    relay = hall["channels"][0]
    assert relay["kind"] == "relay"
    assert set(relay["commands"]) == {"on", "off", "toggle"}
    assert relay["commands"]["on"] == ["toggle_after"]  # Gen2 dialect

    garage = client.get("/api/devices/shellyplus2pm-garage").json()
    cover = garage["channels"][0]
    assert set(cover["commands"]) == {"open", "close", "stop", "set"}


def test_build_generates_the_targets_native_syntax(client):
    gen2 = client.post(
        "/api/url/build",
        json={
            "target_device_id": "shellyplus1-hall",
            "channel_id": "shellyplus1-hall:relay:0",
            "command": "off",
            "params": {"timer": 30},
        },
    ).json()
    assert gen2["url"] == "http://192.168.1.11/rpc/Switch.Set?id=0&on=false&toggle_after=30"
    assert gen2["params"] == {"toggle_after": 30}
    assert gen2["summary"] == "→ Hall · Hall light · off · 30s"

    gen1 = client.post(
        "/api/url/build",
        json={
            "target_device_id": "shelly25-blinds2",
            "channel_id": "shelly25-blinds2:roller:0",
            "command": "set",
            "params": {"pos": 50},
        },
    ).json()
    assert gen1["url"] == "http://192.168.1.21/roller/0?go=to_pos&roller_pos=50"


def test_build_refuses_a_command_the_channel_has_no_row_for(client):
    response = client.post(
        "/api/url/build",
        json={
            "target_device_id": "shellyplus1-hall",
            "channel_id": "shellyplus1-hall:relay:0",
            "command": "open",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"] == "unsupported_command"


def test_build_rejects_an_unknown_device_or_channel(client):
    assert (
        client.post(
            "/api/url/build",
            json={"target_device_id": "nope", "channel_id": "x", "command": "on"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/url/build",
            json={
                "target_device_id": "shellyplus1-hall",
                "channel_id": "shelly25-blinds2:roller:0",
                "command": "on",
            },
        ).status_code
        == 404
    )


def test_build_then_parse_agrees(client):
    built = client.post(
        "/api/url/build",
        json={
            "target_device_id": "shellyplus1-hall",
            "channel_id": "shellyplus1-hall:relay:0",
            "command": "on",
            "params": {"timer": 30},
        },
    ).json()
    parsed = client.post("/api/url/parse", json={"raw_url": built["url"]}).json()
    assert parsed["status"] == "ok"
    assert parsed["command"] == built["command"]
    assert parsed["params"] == built["params"]
    assert parsed["channel_id"] == built["channel_id"]


def test_parse_describes_every_target_type(client):
    webhook = client.post(
        "/api/url/parse", json={"raw_url": "http://192.168.1.5:8123/api/webhook/lights_all_off"}
    ).json()
    assert webhook["target_type"] == "external"
    assert webhook["webhook_id"] == "lights_all_off"
    assert webhook["summary"] == "→ Home Assistant · webhook"

    ghost = client.post(
        "/api/url/parse", json={"raw_url": "http://192.168.1.99/relay/0?turn=on"}
    ).json()
    assert ghost["target_type"] == "unknown_shelly"
    assert ghost["status"] == "dangling"

    unparsed = client.post(
        "/api/url/parse", json={"raw_url": "http://192.168.1.11/status"}
    ).json()
    assert unparsed["status"] == "unparsed"
    assert unparsed["label"] == "?"


def test_slot_detail_carries_a_parse_per_url(client):
    slot = client.get("/api/slots/shellyplus1-hall:hook:0").json()
    assert slot["enabled"] is True
    assert slot["label"] == "Relay 0 · output off"
    assert len(slot["urls"]) == 1
    parsed = slot["urls"][0]["parsed"]
    assert parsed["target_device_name"] == "Porch"
    assert parsed["summary"] == "→ Porch · Porch light · off · 30s"

    assert client.get("/api/slots/nope").status_code == 404


def test_slot_detail_marks_urls_of_a_disabled_slot(client):
    slot = client.get("/api/slots/shellyplus2pm-garage:hook:0").json()
    assert slot["enabled"] is False
    assert slot["urls"][0]["parsed"]["status"] == "disabled"


def test_settings_round_trip(client):
    assert client.get("/api/settings").json() == {"ha_base_url": None}
    saved = client.put("/api/settings", json={"ha_base_url": "http://192.168.1.5:8123/"}).json()
    assert saved == {"ha_base_url": "http://192.168.1.5:8123"}  # trailing slash trimmed
    assert client.get("/api/settings").json() == saved
    assert client.put("/api/settings", json={"ha_base_url": ""}).json() == {"ha_base_url": None}


def test_demo_ids_and_layout_survive_a_restart(app_module, tmp_path):
    """The id migration (#27/#28) must not churn the hand-named fixtures."""
    from fastapi.testclient import TestClient

    with TestClient(app_module.app) as client:
        before = sorted(device["id"] for device in client.get("/api/devices").json())
        client.put(
            "/api/layout", json=[{"node_id": "shelly25-livingroom", "x": 11.0, "y": 22.0}]
        )

    with TestClient(app_module.app) as client:  # restart against the same /data
        assert sorted(device["id"] for device in client.get("/api/devices").json()) == before
        node = next(
            n for n in client.get("/api/graph").json()["nodes"] if n["id"] == "shelly25-livingroom"
        )
        assert node["position"] == {"x": 11.0, "y": 22.0}
        snapshots = client.get("/api/devices/shelly25-livingroom").json()["snapshot_count"]
        assert snapshots == 1
