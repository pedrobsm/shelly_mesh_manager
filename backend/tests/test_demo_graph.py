"""The demo fixture network must match SPEC §3.8 bullet for bullet."""

from __future__ import annotations

import pytest
import pytest_asyncio

from app.db import Database
from app.demo_fixtures import seed_demo
from app.graph import build_graph, rebuild_edges


@pytest_asyncio.fixture
async def graph(tmp_path):
    db = Database(tmp_path / "mesh.db")
    await db.connect()
    await seed_demo(db)
    await rebuild_edges(db)
    result = await build_graph(db)
    await db.close()
    return result


def node(graph, node_id):
    found = [n for n in graph.nodes if n.id == node_id]
    assert found, f"missing node {node_id}"
    return found[0]


def edge_from(graph, slot_id):
    found = [e for e in graph.edges if e.sourcePort == slot_id]
    assert found, f"missing edge from slot {slot_id}"
    return found[0]


def test_all_fixture_devices_present(graph):
    ids = {n.id for n in graph.nodes if n.type == "shelly"}
    assert ids == {
        "shelly25-livingroom",
        "shelly25-blinds",
        "shelly25-blinds2",
        "shellyplus1-hall",
        "shelly1pm-porch",
        "shellyplus2pm-garage",
    }


def test_input_ports_always_present(graph):
    assert [p.label for p in node(graph, "shelly25-livingroom").inputs] == [
        "Ceiling light",
        "Reading lamp",
    ]
    assert len(node(graph, "shelly25-blinds").inputs) == 1  # roller mode -> one channel


def test_output_port_activity_flags(graph):
    outputs = {p.id: p for p in node(graph, "shelly25-livingroom").outputs}
    assert outputs["shelly25-livingroom:act:btn_on_url:0"].active is True
    assert outputs["shelly25-livingroom:act:btn_on_url:0"].label == "SW1 · button on"
    # enabled but no URL, and disabled: both inactive -> hidden until "Show inactive"
    assert outputs["shelly25-livingroom:act:out_on_url:0"].active is False
    assert outputs["shelly25-livingroom:act:btn_on_url:1"].active is False


def test_livingroom_button_to_hall_relay(graph):
    edge = edge_from(graph, "shelly25-livingroom:act:btn_on_url:0")
    assert edge.target == "shellyplus1-hall"
    assert edge.targetPort == "shellyplus1-hall:relay:0"
    assert edge.command == "on"
    assert edge.status == "ok"


def test_livingroom_out_off_to_home_assistant(graph):
    edge = edge_from(graph, "shelly25-livingroom:act:out_off_url:0")
    ha = node(graph, "ext:192.168.1.5")
    assert ha.type == "external"
    assert ha.label == "Home Assistant"
    assert [p.label for p in ha.inputs] == ["webhook lights_all_off"]
    assert edge.target == ha.id
    assert edge.status == "ok"


def test_blinds_roller_stop_edge(graph):
    edge = edge_from(graph, "shelly25-blinds:act:roller_stop_url:0")
    assert edge.target == "shelly25-blinds2"
    assert edge.targetPort == "shelly25-blinds2:roller:0"
    assert edge.command == "stop"
    assert edge.status == "ok"
    assert [p.label for p in node(graph, "shelly25-blinds").outputs] == ["Roller · stopped"]


def test_hall_hook_carries_timer_suffix(graph):
    edge = edge_from(graph, "shellyplus1-hall:hook:0")
    assert edge.target == "shelly1pm-porch"
    assert edge.command == "off · 30s"
    assert edge.params == {"toggle_after": 30}
    assert edge.status == "ok"


def test_porch_points_at_unknown_shelly_ghost(graph):
    edge = edge_from(graph, "shelly1pm-porch:act:btn_on_url:0")
    ghost = node(graph, "unknown:192.168.1.99")
    assert ghost.type == "unknown_shelly"
    assert edge.target == ghost.id
    assert edge.status == "dangling"


def test_garage_disabled_self_loop(graph):
    edge = edge_from(graph, "shellyplus2pm-garage:hook:0")
    assert edge.source == edge.target == "shellyplus2pm-garage"
    assert edge.status == "disabled"
    assert edge.command == "open"


def test_garage_unparsed_edge(graph):
    edge = edge_from(graph, "shellyplus2pm-garage:hook:1")
    assert edge.target == "shellyplus1-hall"
    assert edge.status == "unparsed"
    assert edge.command == "?"
    assert edge.rawUrl == "http://192.168.1.11/status"


def test_every_edge_status_is_exercised(graph):
    assert {e.status for e in graph.edges} == {"ok", "disabled", "dangling", "unparsed"}
    assert {n.type for n in graph.nodes} == {"shelly", "external", "unknown_shelly"}
