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

export type Channel = {
  id: string;
  kind: string;
  idx: number;
  name: string | null;
  label: string;
  /** Commands the builder can emit for this channel, with their parameters. */
  commands: Record<string, string[]>;
};

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

export type RescanResult = { ok: boolean; online: boolean; error: string | null };

// --- Phase 2: the guided action builder (SPEC §4, §5.1) ----------------------

export type UrlParse = {
  raw_url: string;
  target_type: NodeType;
  status: EdgeStatus;
  command: string;
  label: string;
  params: Record<string, number | string>;
  target_device_id: string | null;
  target_device_name: string | null;
  channel_id: string | null;
  channel_label: string | null;
  external_host: string | null;
  external_path: string | null;
  webhook_id: string | null;
  summary: string;
};

export type SlotUrl = ActionUrl & { parsed: UrlParse };

export type SlotDetail = {
  id: string;
  revision: string;
  device_id: string;
  device_name: string | null;
  device_gen: number;
  source_kind: string;
  source_idx: number;
  event: string;
  native_key: string;
  enabled: boolean;
  name: string | null;
  label: string;
  urls: SlotUrl[];
};

export type UrlSpec =
  | {
      kind: 'built';
      target_device_id: string;
      channel_id: string;
      command: string;
      params: Record<string, number | string>;
    }
  | { kind: 'ha_webhook'; webhook_id: string }
  | { kind: 'raw_external'; url: string };

export type SlotUpdate = {
  enabled: boolean;
  urls: UrlSpec[];
  expect_revision?: string;
  confirm_delete?: boolean;
};

export type BuiltUrl = {
  url: string;
  command: string;
  channel_id: string;
  channel_kind: string;
  channel_idx: number;
  params: Record<string, number | string>;
  summary: string;
};

export type UrlTestResult = {
  status_code: number | null;
  elapsed_ms: number;
  body_snippet: string;
  error: string | null;
  simulated: boolean;
};

export type AppSettings = { ha_base_url: string | null };

/** An API error that carries the server's {error, detail, ...} body. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly body: Record<string, unknown>,
  ) {
    super(message);
  }
}

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
    let body: Record<string, unknown> = {};
    try {
      body = await response.json();
      message = (body.detail as string) || (body.error as string) || message;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(message, response.status, (body.error as string) ?? 'http_error', body);
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
  rescanDevice: (id: string) =>
    request<RescanResult>(`devices/${encodeURIComponent(id)}/rescan`, { method: 'POST' }),
  slot: (id: string) => request<SlotDetail>(`slots/${encodeURIComponent(id)}`),
  saveSlot: (id: string, update: SlotUpdate) =>
    request<SlotDetail>(`slots/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: JSON.stringify(update),
    }),
  buildUrl: (spec: Extract<UrlSpec, { kind: 'built' }>) =>
    request<BuiltUrl>('url/build', {
      method: 'POST',
      body: JSON.stringify({
        target_device_id: spec.target_device_id,
        channel_id: spec.channel_id,
        command: spec.command,
        params: spec.params,
      }),
    }),
  testUrl: (url: string) =>
    request<UrlTestResult>('url/test', { method: 'POST', body: JSON.stringify({ url }) }),
  settings: () => request<AppSettings>('settings'),
  saveSettings: (ha_base_url: string | null) =>
    request<AppSettings>('settings', { method: 'PUT', body: JSON.stringify({ ha_base_url }) }),
  setCredentials: (id: string, username: string, password: string) =>
    request<{ ok: boolean }>(`devices/${encodeURIComponent(id)}/credentials`, {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
};
