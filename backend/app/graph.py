"""Edge derivation (SPEC §3.5) and the graph payload (SPEC §2)."""

from __future__ import annotations

import json
from .adapters.base import channel_label, slot_label
from .db import Database
from .models import Edge, Graph, Node, Port
from .resolver import DeviceRef, format_command_label, resolve

EXT_PREFIX = "ext:"
UNKNOWN_PREFIX = "unknown:"


async def load_device_refs(db: Database) -> dict[str, DeviceRef]:
    """Inventoried devices keyed by IP — the resolver's view of the inventory."""
    devices = await db.fetchall("SELECT id, ip, gen FROM devices")
    channels = await db.fetchall("SELECT id, device_id, kind, idx FROM channels")
    by_device: dict[str, dict[tuple[str, int], str]] = {}
    for row in channels:
        by_device.setdefault(row["device_id"], {})[(row["kind"], row["idx"])] = row["id"]
    refs: dict[str, DeviceRef] = {}
    for row in devices:
        refs[str(row["ip"]).lower()] = DeviceRef(
            id=row["id"],
            ip=row["ip"],
            gen=row["gen"],
            channels=by_device.get(row["id"], {}),
        )
    return refs


async def rebuild_edges(db: Database) -> int:
    """Re-derive the whole `edges` cache from `action_urls` (run after every scan)."""
    refs = await load_device_refs(db)
    rows = await db.fetchall(
        """
        SELECT u.id AS url_id, u.raw_url AS raw_url, s.id AS slot_id, s.enabled AS enabled
        FROM action_urls u
        JOIN action_slots s ON s.id = u.slot_id
        ORDER BY u.slot_id, u.position
        """
    )
    records = []
    for row in rows:
        result = resolve(row["raw_url"], refs, slot_enabled=bool(row["enabled"]))
        records.append(
            (
                row["url_id"],
                row["url_id"],
                row["slot_id"],
                result.target_type,
                result.target_device_id,
                result.target_channel_id,
                result.external_host,
                result.external_path,
                result.command,
                json.dumps(result.params) if result.params else None,
                result.status,
            )
        )
    await db.execute("DELETE FROM edges")
    await db.executemany(
        """
        INSERT INTO edges (
          id, action_url_id, src_slot_id, target_type, target_device_id, target_channel_id,
          external_host, external_path, command, params, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        records,
    )
    return len(records)


def _external_node_id(host: str) -> str:
    return f"{EXT_PREFIX}{host}"


def _unknown_node_id(host: str) -> str:
    return f"{UNKNOWN_PREFIX}{host}"


def _webhook_id_of(path: str) -> str | None:
    prefix = "/api/webhook/"
    if path.startswith(prefix):
        return path[len(prefix) :].split("/", 1)[0] or None
    return None


async def build_graph(db: Database) -> Graph:
    """Assemble the frontend graph contract from the inventory + edge cache."""
    device_rows = await db.fetchall(
        "SELECT id, ip, gen, model, name, online FROM devices ORDER BY name, id"
    )
    channel_rows = await db.fetchall(
        "SELECT id, device_id, kind, idx, name FROM channels ORDER BY device_id, kind, idx"
    )
    slot_rows = await db.fetchall(
        """
        SELECT s.id, s.device_id, s.source_kind, s.source_idx, s.event, s.enabled, s.name,
               (SELECT COUNT(*) FROM action_urls u WHERE u.slot_id = s.id) AS url_count
        FROM action_slots s
        ORDER BY s.device_id, s.source_kind, s.source_idx, s.event
        """
    )
    edge_rows = await db.fetchall(
        """
        SELECT e.*, u.raw_url AS raw_url, s.device_id AS src_device_id
        FROM edges e
        JOIN action_urls u ON u.id = e.action_url_id
        JOIN action_slots s ON s.id = e.src_slot_id
        ORDER BY e.id
        """
    )
    layout_rows = await db.fetchall("SELECT node_id, x, y FROM node_layout")
    positions = {row["node_id"]: {"x": row["x"], "y": row["y"]} for row in layout_rows}

    nodes: dict[str, Node] = {}
    for row in device_rows:
        nodes[row["id"]] = Node(
            id=row["id"],
            type="shelly",
            label=row["name"] or row["id"],
            model=row["model"],
            ip=row["ip"],
            gen=row["gen"],
            online=bool(row["online"]),
            inputs=[],
            outputs=[],
            position=positions.get(row["id"]),
        )

    for row in channel_rows:
        node = nodes.get(row["device_id"])
        if node is None:
            continue
        node.inputs.append(
            Port(
                id=row["id"],
                label=row["name"] or channel_label(row["kind"], row["idx"]),
                kind=row["kind"],
            )
        )

    for row in slot_rows:
        node = nodes.get(row["device_id"])
        if node is None:
            continue
        node.outputs.append(
            Port(
                id=row["id"],
                label=slot_label(row["source_kind"], row["source_idx"], row["event"]),
                kind=row["source_kind"],
                active=bool(row["enabled"]) and row["url_count"] > 0,
            )
        )

    edges: list[Edge] = []
    seen_ports: set[str] = set()

    def ensure_port(node: Node, side: str, port: Port) -> None:
        if port.id in seen_ports:
            return
        seen_ports.add(port.id)
        (node.inputs if side == "in" else node.outputs).append(port)

    for node in nodes.values():
        seen_ports.update(port.id for port in node.inputs)
        seen_ports.update(port.id for port in node.outputs)

    for row in edge_rows:
        params = json.loads(row["params"]) if row["params"] else None
        target_type = row["target_type"]
        target_id: str
        target_port: str

        if target_type == "device" and row["target_device_id"] in nodes:
            target_node = nodes[row["target_device_id"]]
            target_id = target_node.id
            if row["target_channel_id"]:
                target_port = row["target_channel_id"]
            elif row["status"] == "unparsed":
                target_port = f"{target_node.id}:unparsed"
                ensure_port(
                    target_node, "in", Port(id=target_port, label="unparsed", kind="unparsed")
                )
            else:
                # Parsed channel does not exist on the target -> dangling into a ghost port.
                target_port = f"{target_node.id}:missing"
                ensure_port(
                    target_node,
                    "in",
                    Port(id=target_port, label="missing channel", kind="missing"),
                )
        elif target_type == "unknown_shelly":
            host = row["external_host"] or "unknown"
            target_id = _unknown_node_id(host)
            node = nodes.get(target_id)
            if node is None:
                node = Node(
                    id=target_id,
                    type="unknown_shelly",
                    label=host,
                    ip=host,
                    online=False,
                    inputs=[],
                    outputs=[],
                    position=positions.get(target_id),
                )
                nodes[target_id] = node
            target_port = f"{target_id}:port"
            ensure_port(node, "in", Port(id=target_port, label="unknown channel", kind="unknown"))
        else:
            host = row["external_host"] or "unknown"
            path = row["external_path"] or "/"
            target_id = _external_node_id(host)
            webhook_id = _webhook_id_of(path)
            node = nodes.get(target_id)
            if node is None:
                node = Node(
                    id=target_id,
                    type="external",
                    label=f"External — {host}",
                    ip=host,
                    inputs=[],
                    outputs=[],
                    position=positions.get(target_id),
                )
                nodes[target_id] = node
            if webhook_id:
                # §1.1 — an HA webhook host is labelled 'Home Assistant', one port per id.
                node.label = "Home Assistant"
                target_port = f"{target_id}:webhook:{webhook_id}"
                ensure_port(
                    node, "in", Port(id=target_port, label=f"webhook {webhook_id}", kind="webhook")
                )
            else:
                target_port = f"{target_id}:{path}"
                ensure_port(node, "in", Port(id=target_port, label=path, kind="path"))

        edges.append(
            Edge(
                id=row["id"],
                source=row["src_device_id"],
                sourcePort=row["src_slot_id"],
                target=target_id,
                targetPort=target_port,
                command=format_command_label(row["command"], params, row["status"]),
                params=params,
                status=row["status"],
                rawUrl=row["raw_url"],
            )
        )

    return Graph(nodes=list(nodes.values()), edges=edges)
