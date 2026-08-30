/** Cytoscape stylesheet — edge statuses of SPEC §1.3 and node states of §1.1. */
import type { Stylesheet } from 'cytoscape';

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
        'curve-style': 'taxi',
        'taxi-direction': 'rightward',
        'taxi-turn': 24,
        'taxi-turn-min-distance': 8,
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
      },
    },
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
