"""Editing a slot (SPEC §5.1/§5.3) — the demo-mode half, which is every flow §5.5 asks for."""

from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient

EMPTY_SLOT = "shelly25-livingroom:act:out_on_url:0"  # enabled, no URLs
ACTIVE_SLOT = "shelly25-livingroom:act:btn_on_url:0"  # -> hall relay 0
HALL_RELAY = "shellyplus1-hall:relay:0"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app.config
    import app.main

    importlib.reload(app.config)
    importlib.reload(app.main)
    with TestClient(app.main.app) as test_client:
        yield test_client


def built(command: str = "on", **params) -> dict:
    return {
        "kind": "built",
        "target_device_id": "shellyplus1-hall",
        "channel_id": HALL_RELAY,
        "command": command,
        "params": params,
    }


def edges(client) -> dict[str, dict]:
    return {edge["id"]: edge for edge in client.get("/api/graph").json()["edges"]}


def audit(client, tmp_path) -> list[tuple[str, str]]:
    import sqlite3

    connection = sqlite3.connect(tmp_path / "mesh.db")
    try:
        return connection.execute("SELECT action, result FROM audit_log ORDER BY rowid").fetchall()
    finally:
        connection.close()


# --- DoD #20: a built URL activates an empty slot, no re-scan ------------------


def test_a_built_url_activates_an_empty_slot(client, tmp_path):
    before = client.get(f"/api/slots/{EMPTY_SLOT}").json()
    assert before["urls"] == []
    assert before["enabled"] is True

    saved = client.put(
        f"/api/slots/{EMPTY_SLOT}",
        json={"enabled": True, "urls": [built("on", timer=30)]},
    )
    assert saved.status_code == 200
    body = saved.json()
    assert len(body["urls"]) == 1
    # Hall is Gen2, so the canonical timer is emitted as its native toggle_after.
    assert body["urls"][0]["raw_url"] == (
        "http://192.168.1.11/rpc/Switch.Set?id=0&on=true&toggle_after=30"
    )
    assert body["urls"][0]["parsed"]["summary"] == "→ Hall · Hall light · on · 30s"

    # The graph follows from the response alone — nothing was re-scanned.
    edge = edges(client)[f"{EMPTY_SLOT}#0"]
    assert edge["target"] == "shellyplus1-hall"
    assert edge["targetPort"] == HALL_RELAY
    assert edge["command"] == "on · 30s"
    assert edge["status"] == "ok"

    port = next(
        port
        for node in client.get("/api/graph").json()["nodes"]
        if node["id"] == "shelly25-livingroom"
        for port in node["outputs"]
        if port["id"] == EMPTY_SLOT
    )
    assert port["active"] is True
    assert audit(client, tmp_path) == [("slot_update", "simulated")]


def test_disabling_a_slot_dims_its_edge(client):
    client.put(f"/api/slots/{EMPTY_SLOT}", json={"enabled": True, "urls": [built()]})
    client.put(f"/api/slots/{EMPTY_SLOT}", json={"enabled": False, "urls": [built()]})
    assert edges(client)[f"{EMPTY_SLOT}#0"]["status"] == "disabled"


def test_clearing_a_gen1_slot_removes_its_edges(client):
    client.put(f"/api/slots/{ACTIVE_SLOT}", json={"enabled": True, "urls": []})
    assert f"{ACTIVE_SLOT}#0" not in edges(client)
    assert client.get(f"/api/slots/{ACTIVE_SLOT}").json()["urls"] == []


# --- DoD #22: the strict-builder rule lives in the API ------------------------


def test_a_raw_url_aimed_at_an_inventoried_shelly_is_rejected(client):
    response = client.put(
        f"/api/slots/{EMPTY_SLOT}",
        json={
            "enabled": True,
            "urls": [{"kind": "raw_external", "url": "http://192.168.1.11/relay/0?turn=on"}],
        },
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "raw_url_targets_a_device"
    assert body["target_device_id"] == "shellyplus1-hall"
    # Nothing was written.
    assert client.get(f"/api/slots/{EMPTY_SLOT}").json()["urls"] == []


def test_a_raw_url_to_a_host_we_do_not_own_is_allowed(client):
    response = client.put(
        f"/api/slots/{EMPTY_SLOT}",
        json={
            "enabled": True,
            "urls": [{"kind": "raw_external", "url": "http://192.168.9.9:8080/hook?x=1"}],
        },
    )
    assert response.status_code == 200
    assert edges(client)[f"{EMPTY_SLOT}#0"]["command"] == "/hook · 1"


def test_an_unsupported_command_is_refused(client):
    response = client.put(
        f"/api/slots/{EMPTY_SLOT}", json={"enabled": True, "urls": [built("open")]}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "unsupported_command"


def test_a_ha_webhook_needs_the_base_url_first(client):
    spec = {"kind": "ha_webhook", "webhook_id": "lights_all_off"}
    refused = client.put(f"/api/slots/{EMPTY_SLOT}", json={"enabled": True, "urls": [spec]})
    assert refused.status_code == 422
    assert refused.json()["error"] == "ha_base_url_unset"

    client.put("/api/settings", json={"ha_base_url": "http://192.168.1.5:8123"})
    saved = client.put(f"/api/slots/{EMPTY_SLOT}", json={"enabled": True, "urls": [spec]})
    assert saved.status_code == 200
    assert saved.json()["urls"][0]["raw_url"] == (
        "http://192.168.1.5:8123/api/webhook/lights_all_off"
    )
    assert edges(client)[f"{EMPTY_SLOT}#0"]["command"] == "webhook"


# --- §5.4: the optimistic-lock guard ------------------------------------------


def test_a_stale_revision_is_refused(client):
    slot = client.get(f"/api/slots/{EMPTY_SLOT}").json()
    client.put(f"/api/slots/{EMPTY_SLOT}", json={"enabled": True, "urls": [built()]})

    response = client.put(
        f"/api/slots/{EMPTY_SLOT}",
        json={"enabled": True, "urls": [built("off")], "expect_revision": slot["revision"]},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "stale_slot"

    fresh = client.get(f"/api/slots/{EMPTY_SLOT}").json()
    assert fresh["revision"] != slot["revision"]
    assert (
        client.put(
            f"/api/slots/{EMPTY_SLOT}",
            json={"enabled": True, "urls": [built("off")], "expect_revision": fresh["revision"]},
        ).status_code
        == 200
    )


# --- §5.3: deleting a Gen2 webhook needs confirming ---------------------------


def test_emptying_a_gen2_slot_asks_for_confirmation(client):
    hook = "shellyplus1-hall:hook:0"
    response = client.put(f"/api/slots/{hook}", json={"enabled": True, "urls": []})
    assert response.status_code == 409
    assert response.json()["error"] == "confirm_delete"
    assert client.get(f"/api/slots/{hook}").json()["urls"] != []

    confirmed = client.put(
        f"/api/slots/{hook}", json={"enabled": False, "urls": [], "confirm_delete": True}
    )
    assert confirmed.status_code == 404  # the slot is gone from the device and from us
    assert client.get(f"/api/slots/{hook}").status_code == 404


# --- DoD #24: the Test button --------------------------------------------------


def test_url_test_is_simulated_in_demo_mode(client):
    result = client.post("/api/url/test", json={"url": "http://192.168.1.11/relay/0?turn=on"}).json()
    assert result["status_code"] == 200
    assert result["simulated"] is True
    assert result["elapsed_ms"] >= 150


def test_unknown_slots_are_404(client):
    assert client.put("/api/slots/nope", json={"enabled": True, "urls": []}).status_code == 404


def test_a_url_already_on_the_slot_may_be_kept_verbatim(client):
    """An unparsed URL cannot be rebuilt, so keeping it must not be refused."""
    slot = "shellyplus2pm-garage:hook:1"  # its URL is /status on the hall device
    stored = client.get(f"/api/slots/{slot}").json()["urls"][0]["raw_url"]
    assert stored == "http://192.168.1.11/status"

    response = client.put(
        f"/api/slots/{slot}",
        json={"enabled": False, "urls": [{"kind": "raw_external", "url": stored}]},
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is False

    # A *different* URL at the same device is still refused.
    refused = client.put(
        f"/api/slots/{slot}",
        json={
            "enabled": True,
            "urls": [{"kind": "raw_external", "url": "http://192.168.1.11/settings"}],
        },
    )
    assert refused.status_code == 422
    assert refused.json()["error"] == "raw_url_targets_a_device"


# --- #33: the first action on a Gen2/3 device ---------------------------------

NEW_HOOK = "shellyplus1-hall:newhook:input.toggle_on:0"


def test_a_gen2_device_offers_slots_for_events_with_no_webhook(client):
    hall = client.get("/api/devices/shellyplus1-hall").json()
    empty = [slot for slot in hall["slots"] if not slot["urls"]]
    assert {slot["native_key"] for slot in empty} == {"switch.on", "input.toggle_on"}
    assert all(slot["enabled"] is False for slot in empty)

    # They are inactive, so the graph hides them until "Show inactive actions".
    node = next(
        n for n in client.get("/api/graph").json()["nodes"] if n["id"] == "shellyplus1-hall"
    )
    inactive = [port for port in node["outputs"] if not port["active"]]
    assert {port["label"] for port in inactive} == {"SW1 · button on", "Relay 0 · output on"}


def test_the_first_url_turns_an_empty_slot_into_a_real_webhook(client):
    before = client.get(f"/api/slots/{NEW_HOOK}").json()
    assert before["urls"] == []

    saved = client.put(
        f"/api/slots/{NEW_HOOK}",
        json={"enabled": True, "urls": [built("toggle")], "expect_revision": before["revision"]},
    )
    assert saved.status_code == 200
    body = saved.json()
    # The device names the hook; the slot follows it.
    assert body["id"] != NEW_HOOK
    assert body["id"].startswith("shellyplus1-hall:hook:")
    assert body["enabled"] is True
    assert body["urls"][0]["raw_url"] == "http://192.168.1.11/rpc/Switch.Toggle?id=0"

    assert client.get(f"/api/slots/{NEW_HOOK}").status_code == 404
    assert client.get(f"/api/slots/{body['id']}").status_code == 200
    edge = next(
        e for e in client.get("/api/graph").json()["edges"] if e["sourcePort"] == body["id"]
    )
    assert edge["command"] == "toggle"


def test_saving_an_empty_new_slot_asks_for_a_url_first(client):
    response = client.put(f"/api/slots/{NEW_HOOK}", json={"enabled": True, "urls": []})
    assert response.status_code == 422
    assert response.json()["error"] == "nothing_to_create"
