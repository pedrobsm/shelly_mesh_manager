/** Cytoscape stylesheet — edge statuses of SPEC §1.3 and node states of §1.1. */
import type { Stylesheet } from 'cytoscape';

/** Widest a label may slide along its edge, per lane (px). */
const LABEL_LANE_WIDTH = 52;
/** Half a label plus a margin — how much room it needs at the end of an edge. */
const LABEL_MARGIN = 26;

/**
 * Slide an edge's label along its own line so labels of near-parallel edges do
 * not stack. The step shrinks on short edges so a label never slides under the
 * node it points at.
 */
function labelOffset(element: any): number {
  const lane = (element.data('labelLane') as number) ?? 0;
  if (lane === 0) return 0;
  const source = element.source();
  const target = element.target();
  const gap =
    Math.abs(target.position('x') - source.position('x')) -
    (source.width() + target.width()) / 2;
  const room = gap / 2 - LABEL_MARGIN;
  const step = Math.max(0, Math.min(LABEL_LANE_WIDTH, room / Math.abs(lane)));
  return lane * step;
}

export const EDGE_COLORS = {
  ok: '#475569',
  disabled: '#94a3b8',
  dangling: '#dc2626',
  unparsed: '#d97706',
} as const;

export function stylesheet(): Stylesheet[] {
  return [
    {
      selector: 'node',
      style: {
        shape: 'round-rectangle',
        width: 'data(w)',
        height: 'data(h)',
        'background-opacity': 0,
        'background-image': (element: any) => element.data('img'),
        'background-fit': 'cover',
        'background-clip': 'none',
        'border-width': 0,
        label: '',
        'transition-property': 'opacity',
        'transition-duration': '120ms' as any,
      },
    },
    {
      selector: 'node:selected',
      style: {
        'border-width': 2,
        'border-color': '#2563eb',
        'border-opacity': 1,
      },
    },
    {
      selector: 'edge',
      style: {
        width: 1.6,
        'line-color': (element: any) => EDGE_COLORS[element.data('status') as keyof typeof EDGE_COLORS],
        'target-arrow-color': (element: any) =>
          EDGE_COLORS[element.data('status') as keyof typeof EDGE_COLORS],
        'target-arrow-shape': 'triangle',
        'arrow-scale': 0.9,
        'source-endpoint': (element: any) => element.data('srcEp'),
        'target-endpoint': (element: any) => element.data('tgtEp'),
        label: 'data(label)',
        'font-size': 10,
        'font-weight': 600,
        color: (element: any) => EDGE_COLORS[element.data('status') as keyof typeof EDGE_COLORS],
        'text-background-color': '#ffffff',
        'text-background-opacity': 0.92,
        'text-background-padding': '2px' as any,
        'text-background-shape': 'roundrectangle',
        'text-rotation': 'none' as any,
        'text-margin-x': labelOffset,
      },
    },
    {
      // Direct routing: one curve per connection, bowed by `bend` so edges that
      // leave the same port separate immediately instead of overlapping.
      selector: 'edge.routing-direct',
      style: {
        'curve-style': 'unbundled-bezier',
        'control-point-distances': 'data(bend)',
        'control-point-weights': 0.5,
      },
    },
    {
      // Orthogonal routing: the wiring-diagram look, with one vertical channel
      // per connection. Waypoints come from applyOrthogonalGeometry() because
      // Cytoscape's own taxi router ignores per-port endpoints.
      selector: 'edge.routing-orthogonal',
      style: {
        'curve-style': 'round-segments',
        // Measure the waypoints from the port endpoints, not the node
        // intersections, so they land exactly where they were computed.
        'edge-distances': 'endpoints',
        'segment-weights': (element: any) => element.data('weights'),
        'segment-distances': (element: any) => element.data('distances'),
        'segment-radii': 6,
        'radius-type': 'arc-radius',
      } as any,
    },
    // Labels on hover only (issue #15): hidden until the pointer is on the edge,
    // and always shown for the edge the user has selected.
    { selector: 'edge.labels-hover', style: { label: '' } },
    { selector: 'edge.labels-hover.hovered', style: { label: 'data(label)' } },
    { selector: 'edge.labels-hover:selected', style: { label: 'data(label)' } },
    { selector: 'edge.status-disabled', style: { 'line-style': 'dashed', opacity: 0.65 } },
    { selector: 'edge.status-unparsed', style: { 'line-style': 'dotted' } },
    { selector: 'edge.status-dangling', style: { width: 2 } },
    {
      // Cytoscape renders self-loops with its own arc; make it big enough to
      // escape the node card so the loop is actually visible (SPEC §1.3).
      selector: 'edge.loop',
      style: {
        'curve-style': 'bezier',
        'loop-direction': '0deg',
        'loop-sweep': '-45deg',
        'control-point-step-size': 70,
      },
    },
    { selector: 'edge:selected', style: { width: 3.2 } },
    { selector: '.faded', style: { opacity: 0.12 } },
    { selector: '.dimmed', style: { opacity: 0.25 } },
    { selector: '.chain', style: { opacity: 1 } },
    { selector: 'edge.chain', style: { width: 2.6 } },
  ];
}
