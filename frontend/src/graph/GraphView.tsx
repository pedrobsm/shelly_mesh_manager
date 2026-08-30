/** Cytoscape canvas: dagre auto-layout, port-anchored edges, selection chains. */
import { useEffect, useRef } from 'react';
import cytoscape from 'cytoscape';
import dagre from 'cytoscape-dagre';
import type { Device, Graph, GraphEdge, GraphNode } from '../api';
import { api } from '../api';
import { geometryFor, renderNode } from './nodeRenderer';
import { stylesheet } from './styles';

cytoscape.use(dagre);

/** How connections are drawn (issue #14). */
export type EdgeRouting = 'direct' | 'orthogonal';

export type ViewOptions = {
  showInactive: boolean;
  showExternal: boolean;
  search: string;
  routing: EdgeRouting;
};

/** Perpendicular offset between two curves leaving the same port, in px. */
const BEND_SPREAD = 26;
/**
 * Orthogonal routing turns at a fraction of the horizontal gap between the two
 * ports, staggered per edge so every connection gets its own vertical channel.
 */
const TURN_BASE = 0.22;
const TURN_STEP = 0.09;
const TURN_CHANNELS = 6;
/** Labels of near-parallel edges are slid along their line so they don't stack. */
const LABEL_LANES = 4;

type Props = {
  graph: Graph;
  devices: Record<string, Device>;
  options: ViewOptions;
  relayoutToken: number;
  onNodeClick: (node: GraphNode | null) => void;
  onEdgeClick: (edge: GraphEdge | null) => void;
};

const LAYOUT = {
  name: 'dagre',
  rankDir: 'LR',
  nodeSep: 80,
  rankSep: 150,
  edgeSep: 20,
  fit: true,
  padding: 40,
  animate: false,
} as any;

/** Nodes that take part in a self-loop or a cross-device cycle (SPEC §1.3). */
export function findCycleNodes(graph: Graph): Set<string> {
  const adjacency = new Map<string, string[]>();
  const cycles = new Set<string>();
  for (const edge of graph.edges) {
    if (edge.source === edge.target) cycles.add(edge.source);
    const list = adjacency.get(edge.source) ?? [];
    list.push(edge.target);
    adjacency.set(edge.source, list);
  }
  const state = new Map<string, number>(); // 0 = visiting, 1 = done
  const stack: string[] = [];
  const visit = (id: string) => {
    if (state.get(id) === 1) return;
    if (state.get(id) === 0) {
      const from = stack.lastIndexOf(id);
      if (from >= 0) stack.slice(from).forEach((member) => cycles.add(member));
      return;
    }
    state.set(id, 0);
    stack.push(id);
    for (const next of adjacency.get(id) ?? []) visit(next);
    stack.pop();
    state.set(id, 1);
  };
  for (const node of graph.nodes) visit(node.id);
  return cycles;
}

function matchesSearch(node: GraphNode, device: Device | undefined, search: string): boolean {
  const needle = search.trim().toLowerCase();
  if (!needle) return true;
  const haystack = [node.label, node.ip, node.model, node.gen ? `gen${node.gen}` : '', device?.name]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
  return haystack.includes(needle);
}

function buildElements(
  graph: Graph,
  devices: Record<string, Device>,
  options: ViewOptions,
): cytoscape.ElementDefinition[] {
  const cycles = findCycleNodes(graph);
  const nodes = graph.nodes.filter(
    (node) => options.showExternal || node.type === 'shelly',
  );
  const visible = new Set(nodes.map((node) => node.id));
  const elements: cytoscape.ElementDefinition[] = [];
  const geometry = new Map<string, ReturnType<typeof geometryFor>>();

  for (const node of nodes) {
    const renderOptions = {
      showInactive: options.showInactive,
      authRequired: devices[node.id]?.auth_required ?? false,
      hasCycle: cycles.has(node.id),
    };
    const { image, geometry: geom } = renderNode(node, renderOptions);
    geometry.set(node.id, geom);
    elements.push({
      group: 'nodes',
      data: {
        id: node.id,
        w: geom.width,
        h: geom.height,
        img: image,
        type: node.type,
        label: node.label,
      },
      position: node.position ? { ...node.position } : undefined,
      classes: matchesSearch(node, devices[node.id], options.search) ? '' : 'dimmed',
    });
  }

  /** Vertical offset of a port row from its node's centre, in model px. */
  const portOffset = (nodeId: string, portId: string): number => {
    const geom = geometry.get(nodeId);
    if (!geom) return 0;
    const y = geom.portY[portId];
    return y === undefined ? 0 : Math.round(y - geom.height / 2);
  };

  const drawn = graph.edges.filter(
    (edge) =>
      visible.has(edge.source) &&
      visible.has(edge.target) &&
      (options.showInactive || edge.status !== 'disabled'),
  );

  // Edges are spread apart per source port (curves) and per source node
  // (orthogonal channels) so no two connections are drawn on top of each other.
  const portTotals = new Map<string, number>();
  const nodeTotals = new Map<string, number>();
  for (const edge of drawn) {
    portTotals.set(edge.sourcePort, (portTotals.get(edge.sourcePort) ?? 0) + 1);
    nodeTotals.set(edge.source, (nodeTotals.get(edge.source) ?? 0) + 1);
  }
  const portSeen = new Map<string, number>();
  const nodeSeen = new Map<string, number>();

  for (const edge of drawn) {
    const portIndex = portSeen.get(edge.sourcePort) ?? 0;
    portSeen.set(edge.sourcePort, portIndex + 1);
    const nodeIndex = nodeSeen.get(edge.source) ?? 0;
    nodeSeen.set(edge.source, nodeIndex + 1);

    const portCount = portTotals.get(edge.sourcePort) ?? 1;
    const bend = portCount > 1 ? (portIndex - (portCount - 1) / 2) * BEND_SPREAD : 0;
    const turn = TURN_BASE + (nodeIndex % TURN_CHANNELS) * TURN_STEP;
    const srcDy = portOffset(edge.source, edge.sourcePort);
    const tgtDy = portOffset(edge.target, edge.targetPort);
    const labelLane = (nodeIndex % LABEL_LANES) - (LABEL_LANES - 1) / 2;

    elements.push({
      group: 'edges',
      data: {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        label: edge.command,
        status: edge.status,
        srcEp: `50% ${srcDy}px`,
        tgtEp: `-50% ${tgtDy}px`,
        srcDy,
        tgtDy,
        bend,
        turn,
        labelLane,
        weights: '0.5',
        distances: '0',
      },
      classes: [
        `status-${edge.status}`,
        `routing-${options.routing}`,
        edge.source === edge.target ? 'loop' : '',
      ]
        .filter(Boolean)
        .join(' '),
    });
  }
  return elements;
}


/**
 * True port-to-port orthogonal paths.
 *
 * Cytoscape's taxi router lays its corners out between node boundaries and
 * ignores custom endpoints, which leaves a diagonal stub from every port. The
 * waypoints are therefore computed here — out of the source port, along this
 * edge's own vertical channel, into the target port — and handed to the
 * `segments` curve style, which does honour the endpoints.
 */
export function applyOrthogonalGeometry(cy: cytoscape.Core, edges?: cytoscape.EdgeCollection): void {
  const targets = (edges ?? cy.edges()).filter('.routing-orthogonal');
  targets.forEach((edge) => {
    const source = edge.source();
    const target = edge.target();
    if (source.same(target)) return;

    const sx = source.position('x') + source.width() / 2;
    const sy = source.position('y') + (edge.data('srcDy') ?? 0);
    const tx = target.position('x') - target.width() / 2;
    const ty = target.position('y') + (edge.data('tgtDy') ?? 0);
    const vx = tx - sx;
    const vy = ty - sy;
    const length = Math.hypot(vx, vy);

    if (length < 1 || Math.abs(vy) < 1) {
      // Already a straight horizontal run: one waypoint on the line itself.
      edge.data({ weights: '0.5', distances: '0' });
      return;
    }

    const turnX = sx + (edge.data('turn') ?? 0.5) * vx;
    const project = (px: number, py: number) => {
      const rx = px - sx;
      const ry = py - sy;
      return {
        weight: (rx * vx + ry * vy) / (length * length),
        distance: (ry * vx - rx * vy) / length,
      };
    };
    const first = project(turnX, sy);
    const second = project(turnX, ty);
    edge.data({
      weights: `${first.weight.toFixed(4)} ${second.weight.toFixed(4)}`,
      distances: `${first.distance.toFixed(2)} ${second.distance.toFixed(2)}`,
    });
  });
}

export default function GraphView({
  graph,
  devices,
  options,
  relayoutToken,
  onNodeClick,
  onEdgeClick,
}: Props) {
  const container = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const graphRef = useRef(graph);
  graphRef.current = graph;

  useEffect(() => {
    if (!container.current || cyRef.current) return;
    const cy = cytoscape({
      container: container.current,
      style: stylesheet(),
      minZoom: 0.15,
      maxZoom: 2.5,
      wheelSensitivity: 0.2,
    });
    cyRef.current = cy;
    // Exposed so the graph can be inspected from the console and by end-to-end checks.
    (window as unknown as { __cy?: cytoscape.Core }).__cy = cy;

    cy.on('tap', 'node', (event) => {
      const id = event.target.id();
      const node = graphRef.current.nodes.find((candidate) => candidate.id === id) ?? null;
      onNodeClick(node);
      onEdgeClick(null);
      highlightChain(cy, id);
    });
    cy.on('tap', 'edge', (event) => {
      const id = event.target.id();
      const edge = graphRef.current.edges.find((candidate) => candidate.id === id) ?? null;
      onEdgeClick(edge);
      onNodeClick(null);
    });
    cy.on('tap', (event) => {
      if (event.target === cy) {
        onNodeClick(null);
        onEdgeClick(null);
        cy.elements().removeClass('faded chain');
      }
    });
    cy.on('position', 'node', (event) => {
      applyOrthogonalGeometry(cy, event.target.connectedEdges());
    });
    cy.on('dragfree', 'node', (event) => {
      const node = event.target;
      void api
        .saveLayout([{ node_id: node.id(), x: node.position('x'), y: node.position('y') }])
        .catch(() => undefined);
    });

    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [onNodeClick, onEdgeClick]);

  // Rebuild elements whenever the graph or the view toggles change.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const remembered = new Map<string, cytoscape.Position>();
    cy.nodes().forEach((node) => {
      remembered.set(node.id(), { ...node.position() });
    });

    cy.batch(() => {
      cy.elements().remove();
      cy.add(buildElements(graph, devices, options));
      cy.nodes().forEach((node) => {
        const stored = graph.nodes.find((candidate) => candidate.id === node.id())?.position;
        const previous = stored ?? remembered.get(node.id());
        if (previous) node.position({ ...previous });
      });
    });

    const unplaced = cy.nodes().filter((node) => {
      const stored = graph.nodes.find((candidate) => candidate.id === node.id())?.position;
      return !stored && !remembered.has(node.id());
    });
    applyOrthogonalGeometry(cy);
    if (unplaced.length > 0) {
      runLayout(cy, true);
    } else {
      cy.fit(undefined, 40);
    }
  }, [graph, devices, options]);

  // Explicit "Re-layout" button.
  useEffect(() => {
    if (relayoutToken === 0 || !cyRef.current) return;
    runLayout(cyRef.current, true);
  }, [relayoutToken]);

  return <div className="graph-canvas" ref={container} data-testid="graph-canvas" />;
}

function runLayout(cy: cytoscape.Core, persist: boolean): void {
  const layout = cy.layout(LAYOUT);
  layout.one('layoutstop', () => {
    applyOrthogonalGeometry(cy);
    cy.fit(undefined, 40);
    if (!persist) return;
    const entries = cy.nodes().map((node) => ({
      node_id: node.id(),
      x: node.position('x'),
      y: node.position('y'),
    }));
    if (entries.length > 0) void api.saveLayout(entries).catch(() => undefined);
  });
  layout.run();
}

/** Select a node -> highlight its full upstream/downstream chain (SPEC §1.4). */
function highlightChain(cy: cytoscape.Core, nodeId: string): void {
  const start = cy.getElementById(nodeId);
  if (start.empty()) return;
  const chain = start.union(start.successors()).union(start.predecessors());
  cy.elements().addClass('faded').removeClass('chain');
  chain.removeClass('faded').addClass('chain');
}
