"""Phase 1 API surface (SPEC §3.6), exercised in demo mode."""

from __future__ import annotations

import asyncio
import importlib
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


def test_second_scan_while_one_runs_is_409(client, app_module, monkeypatch):
    async def slow_scan(db, settings, method=None, scan_id=None):
        await asyncio.sleep(0.5)
        return {"scan_id": scan_id, "status": "done", "found": 0, "errors": []}

    monkeypatch.setattr("app.discovery.run_scan", slow_scan)
    assert client.post("/api/scan").status_code == 202
    conflict = client.post("/api/scan")
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "scan_running"
