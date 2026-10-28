"""The write path as it talks to a device (SPEC §5.3), with the device mocked."""

from __future__ import annotations

import asyncio
import importlib
import json
import time
from urllib.parse import parse_qsl

import httpx
import pytest
from fastapi.testclient import TestClient

GEN1_SLOT = "shelly25-livingroom:act:out_on_url:0"
GEN1_IP = "192.168.1.10"
GEN2_SLOT = "shellyplus1-hall:hook:0"
BUILT = {
    "kind": "built",
    "target_device_id": "shellyplus1-hall",
    "channel_id": "shellyplus1-hall:relay:0",
    "command": "on",
    "params": {},
}
BUILT_URL = "http://192.168.1.11/rpc/Switch.Set?id=0&on=true"


class FakeDevice:
    """Records what was written and decides what the re-read reports back."""

    def __init__(self, *, echo: bool = True) -> None:
        self.echo = echo  # False: accept the write but report something else
        self.requests: list[httpx.Request] = []
        self.gen1_slots: dict[tuple[str, int], tuple[bool, list[str]]] = {
            ("out_on_url", 0): (True, []),
        }
        self.hooks: dict[int, dict] = {
            0: {"id": 0, "cid": 0, "enable": True, "event": "switch.off", "urls": ["http://x/"]}
        }
        self.next_hook_id = 7

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        query = dict(parse_qsl(request.url.query.decode()))

        if path == "/settings/actions":
            # A device that only understands a literal `urls[]`, as Gen1 does.
            if "urls%5B%5D" in request.url.query.decode():
                return httpx.Response(200, json={})
            if "name" in query:  # a write
                if self.echo:
                    urls = [
                        value
                        for key, value in parse_qsl(request.url.query.decode())
                        if key == "urls[]" and value
                    ]
                    self.gen1_slots[(query["name"], int(query["index"]))] = (
                        query["enabled"] == "true",
                        urls,
                    )
                return httpx.Response(200, json={})
            actions = {
                key: [{"index": idx, "enabled": enabled, "urls": urls}]
                for (key, idx), (enabled, urls) in self.gen1_slots.items()
            }
            return httpx.Response(200, json={"actions": actions})

        if path == "/rpc":
            body = json.loads(request.content)
            method, params = body["method"], body.get("params") or {}
            if method == "Webhook.List":
                return self._rpc(body, {"hooks": list(self.hooks.values())})
            if method == "Webhook.ListSupported":
                return self._rpc(body, {"types": {"switch.off": {}, "input.toggle_on": {}}})
            if method == "Webhook.Update" and self.echo:
                self.hooks[params["id"]].update(enable=params["enable"], urls=params["urls"])
                return self._rpc(body, {})
            if method == "Webhook.Update":
                return self._rpc(body, {})
            if method == "Webhook.Delete":
                self.hooks.pop(params["id"], None)
                return self._rpc(body, {})
            if method == "Webhook.Create":
                hook_id = self.next_hook_id
                self.hooks[hook_id] = {"id": hook_id, **params}
                return self._rpc(body, {"id": hook_id})
        return httpx.Response(404)

    @staticmethod
    def _rpc(body: dict, result: dict) -> httpx.Response:
        return httpx.Response(200, json={"id": body["id"], "result": result})

    def writes_to(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == path]


@pytest.fixture
def device() -> FakeDevice:
    return FakeDevice()


@pytest.fixture
def client(tmp_path, monkeypatch, device):
    """A non-demo app whose inventory is the fixtures and whose devices are mocked."""
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SCAN_INTERVAL_MIN", "0")
    import app.config
    import app.main

    importlib.reload(app.config)
    importlib.reload(app.main)

    from app.demo_fixtures import seed_demo
    from app.graph import rebuild_edges

    async def seeding_scan(db, settings, method=None, scan_id=None, scope="full"):
        await seed_demo(db)
        await rebuild_edges(db)
        return {"scan_id": scan_id, "status": "done", "found": 6, "errors": []}

    async def fake_client(settings):
        return httpx.AsyncClient(transport=httpx.MockTransport(device.handler))

    monkeypatch.setattr("app.discovery.run_scan", seeding_scan)
    monkeypatch.setattr("app.slots._client", fake_client)
    with TestClient(app.main.app) as test_client:
        # Wait for the whole seeding "scan", not just its first device: a
        # half-written inventory made these tests flaky.
        for _ in range(100):
            if test_client.get(f"/api/slots/{GEN1_SLOT}").status_code == 200 and (
                test_client.get(f"/api/slots/{GEN2_SLOT}").status_code == 200
            ):
                break
            time.sleep(0.02)
        else:  # pragma: no cover - the fixtures seed in milliseconds
            pytest.fail("the seeded inventory never appeared")
        yield test_client


def audit(tmp_path) -> list[tuple[str, str]]:
    import sqlite3

    connection = sqlite3.connect(tmp_path / "mesh.db", timeout=10)
    try:
        return connection.execute("SELECT action, result FROM audit_log ORDER BY rowid").fetchall()
    finally:
        connection.close()


# --- Gen1 ---------------------------------------------------------------------


def test_the_url_brackets_reach_the_device_literally():
    """`urls%5B%5D` is a parameter Gen1 does not know, so only `enabled` applied."""
    from app.adapters.gen1 import action_query

    query = action_query("btn_on_url", 0, True, ["http://192.168.1.11/relay/0?turn=on&timer=30"])
    sent = httpx.Request(
        "GET", httpx.URL("http://d/settings/actions").copy_with(query=query.encode())
    ).url.raw_path.decode()

    assert "urls[]=" in sent
    assert "urls%5B%5D" not in sent
    # The value is still encoded, or its own ? and & would read as more params.
    assert "turn%3Don%26timer%3D30" in sent
    assert action_query("btn_on_url", 1, False, []).endswith("enabled=false&urls[]=")


def test_gen1_write_uses_the_documented_query_shape(client, device, tmp_path):
    response = client.put(f"/api/slots/{GEN1_SLOT}", json={"enabled": True, "urls": [BUILT]})
    assert response.status_code == 200

    write = device.writes_to("/settings/actions")[0]
    params = parse_qsl(write.url.query.decode())
    assert ("index", "0") in params
    assert ("name", "out_on_url") in params
    assert ("enabled", "true") in params
    assert ("urls[]", BUILT_URL) in params

    assert response.json()["urls"][0]["raw_url"] == BUILT_URL
    assert audit(tmp_path) == [("slot_update", "verified")]


def test_clearing_a_gen1_slot_sends_one_empty_url(client, device):
    client.put(f"/api/slots/{GEN1_SLOT}", json={"enabled": True, "urls": [BUILT]})
    device.requests.clear()
    client.put(f"/api/slots/{GEN1_SLOT}", json={"enabled": False, "urls": []})

    params = parse_qsl(
        device.writes_to("/settings/actions")[0].url.query.decode(), keep_blank_values=True
    )
    assert ("urls[]", "") in params  # a single empty one clears the slot
    assert ("enabled", "false") in params


def test_a_device_that_stores_something_else_is_not_a_success(client, device, tmp_path):
    """Verify-after-write is mandatory: no silent success (DoD #23)."""
    device.echo = False  # accepts the write, keeps reporting the old state
    response = client.put(f"/api/slots/{GEN1_SLOT}", json={"enabled": True, "urls": [BUILT]})

    assert response.status_code == 502
    body = response.json()
    assert body["error"] == "verify_mismatch"
    assert body["sent"]["urls"] == [BUILT_URL]
    assert body["stored"]["urls"] == []
    assert audit(tmp_path) == [("slot_update", "failed:verify_mismatch")]
    # Nothing was recorded locally either.
    assert client.get(f"/api/slots/{GEN1_SLOT}").json()["urls"] == []


def test_a_device_that_refuses_the_write_reports_the_failure(client, device, tmp_path):
    device.handler = lambda request: httpx.Response(500, text="nope")
    response = client.put(f"/api/slots/{GEN1_SLOT}", json={"enabled": True, "urls": [BUILT]})
    assert response.status_code == 502
    assert response.json()["error"] == "write_failed"
    assert audit(tmp_path)[0][1].startswith("failed:")


# --- Gen2/3 -------------------------------------------------------------------


def test_gen2_update_and_verify(client, device, tmp_path):
    response = client.put(f"/api/slots/{GEN2_SLOT}", json={"enabled": False, "urls": [BUILT]})
    assert response.status_code == 200
    assert device.hooks[0]["urls"] == [BUILT_URL]
    assert device.hooks[0]["enable"] is False
    assert response.json()["enabled"] is False
    assert audit(tmp_path) == [("slot_update", "verified")]


def test_gen2_delete_removes_the_hook_from_the_device(client, device, tmp_path):
    response = client.put(
        f"/api/slots/{GEN2_SLOT}", json={"enabled": False, "urls": [], "confirm_delete": True}
    )
    assert response.status_code == 404  # the slot no longer exists anywhere
    assert 0 not in device.hooks
    assert audit(tmp_path) == [("webhook_delete", "verified")]


# --- DoD #25: offline devices and the audit trail -----------------------------


def test_an_offline_device_cannot_be_edited(client, device, tmp_path):
    import sqlite3

    connection = sqlite3.connect(tmp_path / "mesh.db", timeout=10)
    connection.execute("UPDATE devices SET online = 0 WHERE id = 'shelly25-livingroom'")
    connection.commit()
    connection.close()

    response = client.put(f"/api/slots/{GEN1_SLOT}", json={"enabled": True, "urls": [BUILT]})
    assert response.status_code == 409
    assert response.json()["error"] == "device_offline"
    assert "offline" in response.json()["detail"]
    assert device.writes_to("/settings/actions") == []
    assert audit(tmp_path) == [("slot_update", "failed:device_offline")]


def test_every_attempt_is_audited(client, device, tmp_path):
    client.put(f"/api/slots/{GEN1_SLOT}", json={"enabled": True, "urls": [BUILT]})
    device.echo = False
    client.put(f"/api/slots/{GEN1_SLOT}", json={"enabled": False, "urls": []})
    assert [result for _, result in audit(tmp_path)] == ["verified", "failed:verify_mismatch"]


# --- §5.3: one write at a time per device --------------------------------------


@pytest.mark.asyncio
async def test_writes_to_one_device_are_serialised():
    from app.slots import device_lock

    lock = device_lock("a-device")
    assert device_lock("a-device") is lock  # the same lock every time
    assert device_lock("another") is not lock

    order: list[str] = []

    async def writer(name: str) -> None:
        async with device_lock("a-device"):
            order.append(f"{name}-start")
            await asyncio.sleep(0.01)
            order.append(f"{name}-end")

    await asyncio.gather(writer("first"), writer("second"))
    assert order in (
        ["first-start", "first-end", "second-start", "second-end"],
        ["second-start", "second-end", "first-start", "first-end"],
    )


# --- #33: creating a webhook on a device that has none -------------------------


def test_creating_a_webhook_validates_the_event_and_follows_the_new_id(client, device, tmp_path):
    new_slot = "shellyplus1-hall:newhook:input.toggle_on:0"
    response = client.put(f"/api/slots/{new_slot}", json={"enabled": True, "urls": [BUILT]})
    assert response.status_code == 200

    created = response.json()
    assert created["id"] == f"shellyplus1-hall:hook:{device.next_hook_id}"
    assert device.hooks[device.next_hook_id]["event"] == "input.toggle_on"
    assert device.hooks[device.next_hook_id]["cid"] == 0
    assert device.hooks[device.next_hook_id]["urls"] == [BUILT_URL]
    assert audit(tmp_path) == [("slot_update", "verified")]

    # A second save goes to Update, not another Create.
    device.requests.clear()
    client.put(
        f"/api/slots/{created['id']}",
        json={"enabled": False, "urls": [BUILT], "expect_revision": created["revision"]},
    )
    methods = [json.loads(r.content)["method"] for r in device.writes_to("/rpc")]
    assert "Webhook.Create" not in methods
    assert "Webhook.Update" in methods
    assert len(device.hooks) == 2  # the original and the one just created


def test_an_event_the_device_does_not_support_is_refused(client, device):
    device.handler_types = {"switch.off": {}}  # input.toggle_on is not offered

    original = device.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rpc":
            body = json.loads(request.content)
            if body["method"] == "Webhook.ListSupported":
                return httpx.Response(
                    200, json={"id": body["id"], "result": {"types": {"switch.off": {}}}}
                )
        return original(request)

    device.handler = handler
    response = client.put(
        "/api/slots/shellyplus1-hall:newhook:input.toggle_on:0",
        json={"enabled": True, "urls": [BUILT]},
    )
    assert response.status_code == 502
    assert "does not support" in response.json()["detail"]
