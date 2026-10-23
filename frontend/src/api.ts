/** Typed client for the Phase 1 API (SPEC §2 contract, §3.6 surface). */

export type NodeType = 'shelly' | 'external' | 'unknown_shelly';
export type EdgeStatus = 'ok' | 'disabled' | 'dangling' | 'unparsed';

export type Port = {
  id: string;
  label: string;
  kind: string;
  active?: boolean;
};

export type GraphNode = {
  id: string;
  type: NodeType;
  label: string;
  model?: string;
  ip?: string;
  gen?: 1 | 2 | 3;
  online?: boolean;
  inputs: Port[];
  outputs: Port[];
  position?: { x: number; y: number };
};

export type GraphEdge = {
  id: string;
  source: string;
  sourcePort: string;
  target: string;
  targetPort: string;
  command: string;
  params?: Record<string, number | string>;
  status: EdgeStatus;
  rawUrl: string;
};

export type Graph = { nodes: GraphNode[]; edges: GraphEdge[] };

export type ActionUrl = { id: string; position: number; raw_url: string };

export type ActionSlot = {
  id: string;
  source_kind: string;
  source_idx: number;
  event: string;
  native_key: string;
  enabled: boolean;
  name: string | null;
  label: string;
  urls: ActionUrl[];
};

export type Channel = { id: string; kind: string; idx: number; name: string | null; label: string };

export type Device = {
  id: string;
  ip: string;
  gen: number;
  model: string;
  name: string | null;
  fw_version: string | null;
  profile: string | null;
  auth_required: boolean;
  online: boolean;
  first_seen: string;
  last_seen: string;
  channels: Channel[];
  slots: ActionSlot[];
};

export type DeviceDetail = Device & {
  raw_info: Record<string, unknown> | null;
  has_credentials: boolean;
  snapshot_count: number;
  last_snapshot_at: string | null;
};

export type Health = { status: string; version: string; demo: boolean };
export type ScanStatus = {
  status: 'running' | 'done' | 'error';
  found: number;
  errors: string[];
  method: string;
  started_at: string;
  ended_at: string | null;
};

/** Relative to the document base so the app also works behind an Ingress prefix. */
const base = new URL('api/', document.baseURI).toString();

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(base + path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      message = body.detail || body.error || message;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => request<Health>('health'),
  graph: () => request<Graph>('graph'),
  devices: () => request<Device[]>('devices'),
  device: (id: string) => request<DeviceDetail>(`devices/${encodeURIComponent(id)}`),
  startScan: () => request<{ scan_id: string }>('scan', { method: 'POST' }),
  scanStatus: (id: string) => request<ScanStatus>(`scan/${encodeURIComponent(id)}`),
  saveLayout: (entries: { node_id: string; x: number; y: number }[]) =>
    request<{ ok: boolean }>('layout', { method: 'PUT', body: JSON.stringify(entries) }),
  setCredentials: (id: string, username: string, password: string) =>
    request<{ ok: boolean }>(`devices/${encodeURIComponent(id)}/credentials`, {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
};
