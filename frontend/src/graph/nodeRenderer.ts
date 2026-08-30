/**
 * Draws one graph node as an SVG card (header + port rows) and reports the
 * geometry the edge endpoints are anchored to.
 *
 * Ports are rendered inside the node image rather than as separate Cytoscape
 * nodes, so dagre lays out real devices and every edge can still terminate on
 * the exact port row (SPEC §1.2).
 */
import type { GraphNode, Port } from '../api';

export const HEADER_H = 46;
export const ROW_H = 20;
export const ROWS_TOP = HEADER_H + 6;
export const BOTTOM_PAD = 10;
export const MIN_WIDTH = 230;
export const MAX_WIDTH = 380;
const CHAR_W = 6.1;

export type NodeGeometry = {
  width: number;
  height: number;
  /** y offset (from the node's top edge) of every rendered port row. */
  portY: Record<string, number>;
};

export type RenderOptions = {
  showInactive: boolean;
  authRequired?: boolean;
  hasCycle?: boolean;
};

export type RenderedNode = { image: string; geometry: NodeGeometry };

const PALETTE = {
  shelly: { fill: '#ffffff', stroke: '#c7d2e0', dash: '' },
  external: { fill: '#f8fafc', stroke: '#94a3b8', dash: '5 4' },
  unknown_shelly: { fill: '#fff5f5', stroke: '#dc2626', dash: '5 4' },
  offline: { fill: '#f1f5f9', stroke: '#cbd5e1', dash: '' },
};

const GEN_COLORS: Record<number, string> = { 1: '#64748b', 2: '#2563eb', 3: '#7c3aed' };

export function visibleOutputs(node: GraphNode, showInactive: boolean): Port[] {
  return showInactive ? node.outputs : node.outputs.filter((port) => port.active);
}

function escapeXml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function truncate(value: string, max: number): string {
  return value.length > max ? `${value.slice(0, Math.max(1, max - 1))}…` : value;
}

function measure(node: GraphNode, outputs: Port[]): number {
  const header = `${node.label} ${node.model ?? ''} ${node.ip ?? ''}`.length * CHAR_W * 0.62 + 60;
  let rows = 0;
  const count = Math.max(node.inputs.length, outputs.length);
  for (let i = 0; i < count; i += 1) {
    const left = node.inputs[i]?.label.length ?? 0;
    const right = outputs[i]?.label.length ?? 0;
    rows = Math.max(rows, (left + right) * CHAR_W + 54);
  }
  return Math.round(Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, header, rows)));
}

export function geometryFor(node: GraphNode, options: RenderOptions): NodeGeometry {
  const outputs = visibleOutputs(node, options.showInactive);
  const rowCount = Math.max(node.inputs.length, outputs.length);
  const width = measure(node, outputs);
  const height = Math.max(72, ROWS_TOP + rowCount * ROW_H + BOTTOM_PAD);
  const portY: Record<string, number> = {};
  node.inputs.forEach((port, index) => {
    portY[port.id] = ROWS_TOP + index * ROW_H + ROW_H / 2;
  });
  outputs.forEach((port, index) => {
    portY[port.id] = ROWS_TOP + index * ROW_H + ROW_H / 2;
  });
  return { width, height, portY };
}

function headerSvg(node: GraphNode, width: number, options: RenderOptions): string {
  const online = node.online !== false;
  const parts: string[] = [];

  if (node.type === 'shelly') {
    const dot = online ? '#22c55e' : '#94a3b8';
    parts.push(`<circle cx="16" cy="18" r="5" fill="${dot}"/>`);
    const badges: string[] = [];
    let badgeX = width - 12;
    if (node.gen) {
      badgeX -= 26;
      const color = GEN_COLORS[node.gen] ?? '#64748b';
      badges.push(
        `<rect x="${badgeX}" y="9" rx="4" width="26" height="16" fill="${color}"/>` +
          `<text x="${badgeX + 13}" y="21" font-size="10" font-weight="600" fill="#ffffff" text-anchor="middle">G${node.gen}</text>`,
      );
    }
    if (options.authRequired) {
      badgeX -= 20;
      badges.push(
        `<text x="${badgeX + 8}" y="22" font-size="13" text-anchor="middle">🔒</text>`,
      );
    }
    if (options.hasCycle) {
      badgeX -= 20;
      badges.push(
        `<circle cx="${badgeX + 8}" cy="17" r="8" fill="#fef3c7" stroke="#f59e0b"/>` +
          `<text x="${badgeX + 8}" y="21" font-size="10" fill="#b45309" text-anchor="middle">↻</text>`,
      );
    }
    const nameWidth = Math.floor((badgeX - 26) / CHAR_W);
    parts.push(
      `<text x="26" y="22" font-size="13" font-weight="600" fill="${online ? '#0f172a' : '#64748b'}">${escapeXml(truncate(node.label, nameWidth))}</text>`,
    );
    parts.push(...badges);
    const subtitle = [node.model, node.ip, online ? null : 'offline'].filter(Boolean).join(' · ');
    parts.push(
      `<text x="16" y="38" font-size="10.5" fill="#64748b">${escapeXml(truncate(subtitle, Math.floor((width - 30) / 5.6)))}</text>`,
    );
  } else {
    const isGhost = node.type === 'unknown_shelly';
    parts.push(
      `<text x="16" y="22" font-size="13" font-weight="600" fill="${isGhost ? '#b91c1c' : '#334155'}">${escapeXml(truncate(node.label, Math.floor((width - 30) / CHAR_W)))}</text>`,
    );
    const subtitle = isGhost ? `unknown Shelly · ${node.ip ?? ''}` : (node.ip ?? 'external');
    parts.push(`<text x="16" y="38" font-size="10.5" fill="#64748b">${escapeXml(subtitle)}</text>`);
  }
  return parts.join('');
}

function portRowsSvg(node: GraphNode, geometry: NodeGeometry, options: RenderOptions): string {
  const outputs = visibleOutputs(node, options.showInactive);
  const parts: string[] = [];
  const { width } = geometry;

  node.inputs.forEach((port, index) => {
    const y = ROWS_TOP + index * ROW_H + ROW_H / 2;
    const missing = port.kind === 'missing' || port.kind === 'unparsed' || port.kind === 'unknown';
    const color = missing ? '#dc2626' : '#0ea5e9';
    parts.push(`<circle cx="0.5" cy="${y}" r="4.5" fill="${color}"/>`);
    parts.push(
      `<text x="12" y="${y + 3.5}" font-size="11" fill="${missing ? '#b91c1c' : '#334155'}">${escapeXml(truncate(port.label, 24))}</text>`,
    );
  });

  outputs.forEach((port, index) => {
    const y = ROWS_TOP + index * ROW_H + ROW_H / 2;
    const active = port.active !== false;
    const color = active ? '#f59e0b' : '#cbd5e1';
    parts.push(`<circle cx="${width - 0.5}" cy="${y}" r="4.5" fill="${color}"/>`);
    parts.push(
      `<text x="${width - 12}" y="${y + 3.5}" font-size="11" text-anchor="end" fill="${active ? '#334155' : '#94a3b8'}">${escapeXml(truncate(port.label, 26))}</text>`,
    );
  });

  return parts.join('');
}

export function renderNode(node: GraphNode, options: RenderOptions): RenderedNode {
  const geometry = geometryFor(node, options);
  const { width, height } = geometry;
  const offline = node.type === 'shelly' && node.online === false;
  const theme = offline ? PALETTE.offline : PALETTE[node.type];
  const dash = theme.dash ? ` stroke-dasharray="${theme.dash}"` : '';

  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" font-family="ui-sans-serif,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">` +
    `<rect x="0.5" y="0.5" width="${width - 1}" height="${height - 1}" rx="10" fill="${theme.fill}" stroke="${theme.stroke}"${dash} stroke-width="1.5"/>` +
    `<line x1="10" y1="${HEADER_H}" x2="${width - 10}" y2="${HEADER_H}" stroke="#e2e8f0"/>` +
    headerSvg(node, width, options) +
    portRowsSvg(node, geometry, options) +
    `</svg>`;

  return { image: `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`, geometry };
}
