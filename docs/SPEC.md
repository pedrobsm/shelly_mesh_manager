# Shelly Mesh Manager — Build Spec v2

Self-hosted web app (Docker) that discovers Shelly devices on the LAN, visualizes the automation mesh created by their HTTP action URLs as an interactive graph, and (in later phases) edits actions and applies bulk configuration. Designed for later repackaging as a Home Assistant add-on (Ingress-safe: all paths relative, config via env vars, state in `/data`).

**Environment:** 15–40 devices, static IPs / DHCP reservations. Generations: Gen1 (2.5, 1PM, …), Gen2 Plus, Gen3. Device-to-device automation is HTTP action URLs only. Auth: Basic (Gen1), digest (Gen2/3).

---

## 1. Graph semantics (normative)

### 1.1 Node types
- **Shelly node** — one per discovered device. Header: name, model, IP, generation badge, online/offline dot.
- **External node** — one per distinct URL host that is *not* an inventoried Shelly. Groups all URLs to that host. Classification by path:
  - `/api/webhook/{id}` → labeled **"Home Assistant — webhook {id}"** (one port per webhook id).
  - anything else → **"External — {host}"** (one port per distinct path).
- **Unknown-Shelly node** — a URL host that *looks like* a Shelly (path matches Shelly command patterns) but isn't in the inventory → rendered as a ghost Shelly node and every edge into it flagged `dangling`.

### 1.2 Ports
- **Input ports (left side): the device's controllable channels.** One port per relay / roller / light / white channel (e.g., Shelly 2.5 in relay mode → 2 input ports; in roller mode → 1). **Always visible**, even with no inbound edges. Note: these are the *relay/cover channels* other devices call — not the physical SW terminals. Physical inputs surface as the *event source* of actions (below).
- **Output ports (right side): action slots.** One port per action slot, labeled with its event source, e.g. `SW1 · button on`, `Relay 0 · output off`, `Roller · stopped`. **Visible by default only when the slot is enabled AND has ≥1 URL.** A view toggle ("Show inactive actions") reveals disabled or empty slots, rendered dimmed. Rationale: a disabled action is a common root cause when an automation stops working; it must be findable, just not cluttering the default view.

### 1.3 Edges
- One edge per action URL: from the source device's action port to the target's input port (or external port).
- **Every edge is labeled with the normalized command**: `on`, `off`, `toggle`, `open`, `close`, `stop`, `set`, or `other` — with suffixes when parameters exist, e.g. `on · 30s` (timer), `open · 50%` (duration/position), `set · b=80` (brightness).
- Edge status → styling:
  - `ok` — solid line.
  - `disabled` — dashed, dimmed (only rendered when "Show inactive" is on).
  - `dangling` — red: host not found in inventory and not reachable, or parsed channel doesn't exist on the target.
  - `unparsed` — amber dotted: URL host resolved to a device but the path/command was not recognized; label `?`, raw URL in tooltip.
- Self-loops and cross-device cycles get a cycle badge on the participating nodes.

### 1.4 Interaction (Phase 1 = read-only)
- Auto-layout left→right (dagre), manual node repositioning persisted server-side.
- Click node → detail drawer (all channels, all action slots with raw URLs, firmware, links to device web UI).
- Click edge → raw URL, source event, parse result.
- Select node → highlight full upstream/downstream chain.
- Search/filter by name, IP, model, generation; toggle for external nodes; toggle for inactive actions.

---

## 2. Data model

SQLite in `/data/mesh.db`. IDs: device primary key is the stable Shelly device id (MAC-derived), never the IP.

```sql
CREATE TABLE devices (
  id            TEXT PRIMARY KEY,          -- e.g. 'shelly25-A4CF12F45B10' / Gen2 device id
  ip            TEXT NOT NULL UNIQUE,
  gen           INTEGER NOT NULL,          -- 1, 2, 3
  model         TEXT NOT NULL,             -- 'SHSW-25', 'SNSW-001P16EU', ...
  name          TEXT,
  fw_version    TEXT,
  profile       TEXT,                      -- 'relay' | 'roller' | 'cover' | 'light' | NULL
  auth_required INTEGER NOT NULL DEFAULT 0,
  online        INTEGER NOT NULL DEFAULT 1,
  first_seen    TEXT NOT NULL,             -- ISO 8601
  last_seen     TEXT NOT NULL,
  raw_info      TEXT                       -- JSON dump of /shelly | Shelly.GetDeviceInfo
);

CREATE TABLE channels (                    -- input ports of the graph
  id         TEXT PRIMARY KEY,             -- '{device_id}:relay:0'
  device_id  TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  kind       TEXT NOT NULL,                -- 'relay'|'roller'|'cover'|'light'|'white'|'rgbw'
  idx        INTEGER NOT NULL,
  name       TEXT,
  UNIQUE(device_id, kind, idx)
);

CREATE TABLE action_slots (                -- output ports of the graph
  id           TEXT PRIMARY KEY,           -- '{device_id}:act:{gen1_key}:{idx}' or '{device_id}:hook:{hook_id}'
  device_id    TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  source_kind  TEXT NOT NULL,              -- 'input'|'relay'|'roller'|'cover'|'light'|'sensor'
  source_idx   INTEGER NOT NULL,
  event        TEXT NOT NULL,              -- normalized: 'btn_on','btn_off','longpush','shortpush',
                                           -- 'out_on','out_off','roller_open','roller_close','roller_stop', ...
  native_key   TEXT NOT NULL,              -- Gen1 action key ('btn_on_url') or Gen2 event ('input.toggle_on') + hook id
  enabled      INTEGER NOT NULL DEFAULT 0,
  name         TEXT                        -- Gen2 webhook name, if any
);

CREATE TABLE action_urls (
  id         TEXT PRIMARY KEY,             -- '{slot_id}#0'
  slot_id    TEXT NOT NULL REFERENCES action_slots(id) ON DELETE CASCADE,
  position   INTEGER NOT NULL,
  raw_url    TEXT NOT NULL
);

CREATE TABLE edges (                       -- derived cache; rebuilt after every scan
  id                TEXT PRIMARY KEY,      -- = action_urls.id
  action_url_id     TEXT NOT NULL REFERENCES action_urls(id) ON DELETE CASCADE,
  src_slot_id       TEXT NOT NULL,
  target_type       TEXT NOT NULL,         -- 'device'|'external'|'unknown_shelly'
  target_device_id  TEXT,                  -- when target_type='device'
  target_channel_id TEXT,
  external_host     TEXT,                  -- when external/unknown
  external_path     TEXT,
  command           TEXT NOT NULL,         -- 'on'|'off'|'toggle'|'open'|'close'|'stop'|'set'|'other'
  params            TEXT,                  -- JSON: {"timer":30} / {"brightness":80} / {"duration":5}
  status            TEXT NOT NULL          -- 'ok'|'disabled'|'dangling'|'unparsed'
);

CREATE TABLE credentials (
  device_id TEXT PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
  username  TEXT NOT NULL,
  password  TEXT NOT NULL                  -- /data is the trust boundary; document this
);

CREATE TABLE config_snapshots (            -- captured on every successful scan; feeds device replacement
  id         TEXT PRIMARY KEY,
  device_id  TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  taken_at   TEXT NOT NULL,
  config     TEXT NOT NULL                 -- JSON: full /settings + /settings/actions (Gen1)
                                           --       or GetConfig + Webhook.List (Gen2/3)
);

CREATE TABLE scan_runs (
  id         TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  ended_at   TEXT,
  method     TEXT NOT NULL,                -- 'mdns'|'range'|'demo'
  found      INTEGER DEFAULT 0,
  errors     TEXT                          -- JSON array
);

CREATE TABLE audit_log (                   -- every write to a physical device (Phase 2+)
  id        TEXT PRIMARY KEY,
  ts        TEXT NOT NULL,
  device_id TEXT NOT NULL,
  action    TEXT NOT NULL,                 -- 'slot_update'|'webhook_create'|...
  payload   TEXT NOT NULL,                 -- JSON of what was sent
  result    TEXT NOT NULL                  -- 'verified'|'failed:{reason}'
);

CREATE TABLE node_layout (
  node_id TEXT PRIMARY KEY,               -- device id or 'ext:{host}'
  x REAL NOT NULL, y REAL NOT NULL
);
```

### Graph API payload (frontend contract)

```typescript
type Graph = { nodes: Node[]; edges: Edge[] };

type Node = {
  id: string;
  type: 'shelly' | 'external' | 'unknown_shelly';
  label: string;                     // name or 'HA — webhook xyz'
  model?: string; ip?: string; gen?: 1 | 2 | 3;
  online?: boolean;
  inputs: Port[];                    // channels — always present
  outputs: Port[];                   // action slots — includes inactive; UI filters
  position?: { x: number; y: number };
};

type Port = {
  id: string;
  label: string;                     // 'Relay 0' | 'SW1 · button on'
  kind: string;
  active?: boolean;                  // outputs only: enabled && urls>0
};

type Edge = {
  id: string;
  source: string; sourcePort: string;
  target: string; targetPort: string;
  command: string;                   // rendered as the edge label
  params?: Record<string, number | string>;
  status: 'ok' | 'disabled' | 'dangling' | 'unparsed';
  rawUrl: string;
};
```

---

## 3. Phase 1 — detailed build plan (read-only: discovery + inventory + graph)

**Goal:** `docker compose up` → open browser → see the mesh. Must work first try; a built-in demo mode guarantees a verifiable UI even with zero devices reachable.

### 3.1 Stack (pinned — do not substitute)
- **Backend:** Python 3.12, FastAPI 0.115.x, uvicorn 0.30.x, httpx 0.27.x (with `httpx`'s `DigestAuth` for Gen2/3), zeroconf 0.132.x, aiosqlite 0.20.x.
- **Frontend:** React 18 + TypeScript + Vite 5, Cytoscape.js 3.30.x + `cytoscape-dagre`. Build output served **statically by FastAPI** (single origin — no CORS, no proxy config, one container).
- **Container:** multi-stage Dockerfile (node:20-alpine build → python:3.12-slim runtime). `docker-compose.yml` with `network_mode: host` (required for mDNS) and `./data:/data`. README must state the fallback for environments without host networking: set `SCAN_SUBNET` and use range scan.

### 3.2 Configuration (env vars only)
| Var | Default | Purpose |
|---|---|---|
| `PORT` | `8099` | HTTP port |
| `SCAN_SUBNET` | *(empty)* | CIDR for range scan fallback, e.g. `192.168.1.0/24` |
| `SCAN_INTERVAL_MIN` | `15` | periodic re-scan; `0` disables |
| `DEMO_MODE` | `false` | serve fixture network, no network I/O |
| `HTTP_TIMEOUT_S` | `3` | per-device request timeout |

### 3.3 Repository layout
```
backend/app/ main.py, config.py, db.py, models.py,
             discovery/ (mdns.py, range_scan.py),
             adapters/ (base.py, gen1.py, gen2.py),
             resolver.py, graph.py, api.py, demo_fixtures.py
frontend/src/ api.ts, graph/ (GraphView.tsx, nodeRenderer.ts, styles.ts),
              components/ (DeviceDrawer.tsx, EdgePopover.tsx, Toolbar.tsx)
Dockerfile · docker-compose.yml · Makefile · README.md
```

### 3.4 Discovery algorithm (exact)
1. mDNS browse `_http._tcp.local.` and `_shelly._tcp.local.` for 10 s; candidate = any host whose service name contains `shelly` (case-insensitive) **plus** every IP in `SCAN_SUBNET` if set.
2. For each candidate, concurrently (semaphore = 16): `GET http://{ip}/shelly` (unauthenticated on all generations).
   - Response with `"gen": 2|3` → Gen2/3 path. Response with `"type"`/`"mac"` and no `gen` → Gen1 path. Non-JSON / timeout → not a Shelly, drop.
3. **Gen1 inventory:** `GET /settings` (device name, mode, relays/rollers, channel names) and `GET /settings/actions` (`{"actions": {"btn_on_url": [{"index":0, "urls":[...], "enabled":true}], ...}}`). Map every `*_url` key × index to an `action_slot`; map key→normalized event via a static table in `gen1.py` (e.g. `btn_on_url→btn_on`, `out_off_url→out_off`, `roller_open_url→roller_open`, `longpush_url→longpush`).
4. **Gen2/3 inventory:** `POST /rpc` `Shelly.GetDeviceInfo`, `Shelly.GetConfig` (channels from `switch:N` / `cover:N` / `light:N` keys, profile), `Webhook.ListSupported`, `Webhook.List` (`hooks[]: {id, cid, enable, event, name, urls[]}`). Each hook = one `action_slot`; `event` like `switch.on`, `input.toggle_on`, `cover.stopped` → normalized event table in `gen2.py`.
5. `401` responses: store device with `auth_required=1, online=1` and partial info; if credentials exist in DB, retry with Basic (Gen1) / digest (Gen2/3). UI shows a lock icon; `POST /api/devices/{id}/credentials` stores creds and triggers a single-device re-scan.
6. Upsert by device id (IP may change); mark devices missing from a full scan as `online=0` (never auto-delete). For every fully inventoried device, store a `config_snapshot` if its config changed since the last snapshot (keep the last 10 per device). **Snapshot capture starts in Phase 1** so that a device that dies before Phase 4 ships can still be replaced from its history. Rebuild the `edges` table (3.5) at the end of every scan.

### 3.5 URL resolver (exact rules)
For each `action_urls.raw_url`:
1. Parse with a URL parser (never regex over the whole URL). Extract host (strip port), path, query.
2. **Target:** host equals an inventoried device IP → `device`. Else if path matches a Shelly command pattern (below) → `unknown_shelly` (status `dangling`). Else → `external`; if path starts with `/api/webhook/` → HA webhook classification.
3. **Command patterns** (apply to any target generation — Gen2/3 accept Gen1-style URLs, so match by path shape only):

| Path pattern | Channel | Command | Params |
|---|---|---|---|
| `/relay/{i}?turn=on\|off\|toggle` | relay i | on/off/toggle | `timer` |
| `/roller/{i}?go=open\|close\|stop` | roller i | open/close/stop | `duration` |
| `/light/{i}?turn=…` · `/white/{i}?turn=…` · `/color/{i}?turn=…` | light/white i | on/off/toggle | `brightness`, `timer` |
| `/rpc/Switch.Set?id={i}&on=true\|false` | relay i | on/off | `toggle_after` |
| `/rpc/Switch.Toggle?id={i}` | relay i | toggle | |
| `/rpc/Cover.Open\|Close\|Stop?id={i}` | cover i | open/close/stop | `duration` |
| `/rpc/Cover.GoToPosition?id={i}&pos={p}` | cover i | set | `pos` |
| `/rpc/Light.Set?id={i}&…` | light i | on/off/set | `brightness` |

4. Device target + matched pattern + channel exists → `ok` (or `disabled` if the slot is disabled). Channel doesn't exist → `dangling`. Device target, pattern not matched → `unparsed` (keep the edge, label `?`). Case-insensitive matching; tolerate trailing slashes and extra query params.

### 3.6 Backend API (complete Phase 1 surface)
```
GET  /api/health                      → {status, version, demo}
POST /api/scan                        → {scan_id}   (409 if a scan is running)
GET  /api/scan/{id}                   → {status: running|done|error, found, errors[]}
GET  /api/devices                     → Device[] (with channels + slots + urls nested)
GET  /api/devices/{id}                → full detail incl. raw_info
POST /api/devices/{id}/credentials    → {ok} ; body {username, password}; triggers re-probe
GET  /api/graph                       → Graph (contract in §2)
PUT  /api/layout                      → {ok} ; body {node_id, x, y}[]
```
Errors: JSON `{error, detail}` with correct status codes. Log per-device failures into `scan_runs.errors`; a scan never aborts because one device misbehaves.

### 3.7 Frontend behavior (Phase 1)
Toolbar: Scan button with progress, search box, toggles (external nodes / inactive actions), re-layout button. Graph per §1. Drawer and popover per §1.4. Dangling/unparsed counts shown as badges in the toolbar. No editing UI anywhere in Phase 1.

### 3.8 Demo mode (the "runs at first try" guarantee)
`DEMO_MODE=true` seeds the DB from `demo_fixtures.py` and disables all network I/O. Fixture network (build exactly this):
- `shelly25-livingroom` (Gen1, relay mode): SW1 `btn_on` → relay 0 of `shellyplus1-hall` (`on`); `out_off` → HA webhook `lights_all_off`.
- `shelly25-blinds` (Gen1, roller mode): `roller_stop` → `/roller/0?go=stop` of `shelly25-blinds2` (Gen1).
- `shellyplus1-hall` (Gen2): hook `switch.off` → `/rpc/Switch.Set?id=0&on=false&toggle_after=30` on `shelly1pm-porch` → edge label `off · 30s`.
- `shelly1pm-porch` (Gen1): action enabled → URL to `192.168.1.99/relay/0?turn=on` (no such device) → **dangling** red edge to an unknown-Shelly ghost node.
- `shellyplus2pm-garage` (Gen3, cover profile): hook `input.toggle_on`, **disabled**, URL to `Cover.Open` of itself → hidden by default, dashed self-loop when "Show inactive" is on; plus one `unparsed` URL (`/status`) to `shellyplus1-hall`.
- External node "Home Assistant" with the webhook port.

This fixture exercises every node type, every edge status, both port-visibility rules, self-loop rendering, and all label formats.

### 3.9 Build & run (must be exactly this)
```
make dev      # backend on :8099 with DEMO_MODE=true, vite dev server proxied
make build    # docker build
make demo     # docker compose --profile demo up   (DEMO_MODE=true)
make up       # docker compose up                  (real scan)
```

### 3.10 Definition of done — Phase 1
- [ ] `make demo` → graph renders the full fixture network correctly on first load, matching every rule in §1 (verify each fixture bullet visually).
- [ ] `make up` on a real LAN discovers mixed Gen1/Gen2/Gen3 devices via mDNS; with mDNS unavailable, `SCAN_SUBNET` range scan finds them.
- [ ] Every action URL between real devices appears as an edge with the correct command label; URLs to HA appear under one external node.
- [ ] Password-protected device shows lock icon; submitting credentials completes its inventory without a full re-scan.
- [ ] Unplugging a device + re-scan: node dims (offline), inbound edges keep rendering, its own outbound edges unchanged; a URL to a never-seen IP renders as a red dangling edge.
- [ ] Node positions survive container restart. Scan of 40 devices completes < 30 s.
- [ ] After a scan, every online device has a config snapshot in the DB; changing a setting on a device via its own web UI and re-scanning produces a new snapshot.
- [ ] Zero browser console errors; `GET /api/health` returns 200.

---

## 4. Guided action builder (Phase 2 — normative)

When creating or editing an action that targets another Shelly, **the app builds the URL; the user never types it.** Selecting an output port on the graph opens the action form:

1. **Target type** (select): `Shelly device` · `Home Assistant webhook` · `Other external URL`.
2. **Shelly device path** (the mandatory guided path):
   - **Device** — searchable select over the inventory, showing name, model, IP, generation, online state. Offline devices selectable but flagged.
   - **Channel** — select populated only with channels that actually exist on the chosen device (from `channels`).
   - **Command** — select constrained by channel kind: relay → on / off / toggle; roller/cover → open / close / stop / go-to-position; light/white → on / off / toggle / set-brightness.
   - **Parameters** — optional numeric fields shown only when applicable: timer (s), position (%), brightness (%), duration (s).
   - The app generates the URL in the **target's native syntax** (Gen1 path style for Gen1 targets; `/rpc/...` for Gen2/3) and shows it **read-only** for confirmation. There is no free-text URL field for Shelly targets.
3. **Home Assistant webhook path:** `HA_BASE_URL` is configured once in app settings; the form asks only for the webhook id (with autocomplete from webhook ids already seen in the mesh).
4. **Other external:** plain URL field with syntax validation — the only place raw URLs can be typed.

Editing an existing URL: if the resolver (§3.5) parses it, the selects are pre-populated from the parse result; if `unparsed`, the raw URL is shown with a one-way "rebuild with the wizard" action. Before save: target reachability check, channel existence check, and an optional **Test** button that fires the URL once and reports the HTTP result. Save writes to the source device (Gen1 `/settings/actions`; Gen2/3 `Webhook.Create/Update/Delete`), re-reads to verify, and refreshes the graph.

## 5. Phase 2 — detailed build plan (guided action editing)

**Goal:** everything from Phase 1 stays read-only-safe; add exactly one write capability — editing action slots through the guided builder (§4). No bulk config, no replacement, no firmware.

### 5.1 New/changed backend API
```
GET  /api/slots/{slot_id}          → slot detail: enabled, urls[] each with resolver parse result
PUT  /api/slots/{slot_id}          → body {enabled, urls: UrlSpec[]} ; writes to device, verifies, returns final state
POST /api/url/build                → {target_device_id, channel_id, command, params} → {url}
POST /api/url/parse                → {raw_url} → resolver result (pre-populates the form for existing URLs)
POST /api/url/test                 → {url} → {status_code, elapsed_ms, body_snippet}  (fired server-side)
GET/PUT /api/settings              → {ha_base_url}

UrlSpec = {kind:'built', target_device_id, channel_id, command, params}   // server generates the URL
        | {kind:'ha_webhook', webhook_id}                                  // server builds from HA_BASE_URL
        | {kind:'raw_external', url}                                       // only allowed for non-Shelly hosts —
                                                                           // server REJECTS raw URLs whose host is an inventoried device
```
The server, not the client, generates every Shelly-target URL — the strict-builder rule (§4) is enforced at the API layer, not just in the UI.

### 5.2 URL generator
Exact inverse of the resolver table (§3.5), emitting the **target's native syntax** (Gen1 path style for Gen1 targets, `/rpc/...` for Gen2/3), percent-encoded. **Required test: generator→resolver roundtrip.** For every command × channel-kind × generation combination, generating a URL and re-parsing it must return the identical (channel, command, params). This single test suite is the correctness backbone of Phase 2 — build it first.

### 5.3 Write adapters (exact)
- **Gen1:** `GET /settings/actions?index={i}&name={native_key}&enabled=true|false&urls[]={url}&urls[]={url2}` (Gen1 accepts writes via GET query params; multiple `urls[]` entries; empty `urls[]=` clears).
- **Gen2/3:** `Webhook.Update {id, enable, urls[]}` for existing hooks; `Webhook.Create {cid, event, enable, urls[], name}` when the slot has no hook yet (event from the normalized-event table, validated against `Webhook.ListSupported`); `Webhook.Delete {id}` when the last URL is removed and the user confirms.
- **Verify-after-write (mandatory):** re-read the slot from the device, compare field-by-field with what was sent; mismatch → the API returns an error with both states — never a silent success. Every write attempt is recorded in `audit_log`.
- One in-flight write per device (per-device async lock); writes rejected with a clear error when the device is offline.

### 5.4 Frontend
- Clicking an output port opens the slot editor drawer: enable/disable toggle, URL list where each row shows the parsed summary ("→ Hall · Relay 0 · on · 30s") or the raw URL if external/unparsed.
- "Add URL" launches the wizard steps from §4 (target type → device → channel → command → params). **Test** button per row fires `POST /api/url/test` and shows the HTTP result inline. Unparsed rows show "Rebuild with wizard".
- Optimistic-lock guard: the drawer stores the slot state it loaded; if a re-scan changed the device config meanwhile, save is blocked with "device changed — reload".
- On successful save the graph updates from the API response alone (no full re-scan).

### 5.5 Demo mode in Phase 2
`DEMO_MODE=true` supports the full editing flow: writes mutate the fixture DB only and are marked `simulated` in `audit_log`; `POST /api/url/test` returns a fake 200 after 150 ms. Every Phase 2 UI flow must be exercisable in demo mode.

### 5.6 Definition of done — Phase 2
- [ ] Generator→resolver roundtrip tests pass for every row of the §3.5 table, both generations.
- [ ] In demo mode: add a built URL to an empty slot → slot becomes active, new edge appears with correct label, without a rescan.
- [ ] On real hardware: create an action on a Gen1 device via the wizard → visible in the device's own web UI; same for a Gen2/3 webhook (create, update, delete).
- [ ] `PUT /api/slots/...` with a raw URL pointing at an inventoried Shelly is rejected (strict-builder enforcement).
- [ ] Disabling a slot dashes/hides its edges per §1.2–1.3 rules; verify-after-write mismatch produces a visible error, not a fake success.
- [ ] Test button reports the real HTTP status from a target device.
- [ ] Editing blocked with a clear message when the source device is offline; audit_log has one row per write attempt.



## 6. Device replacement wizard (Phase 4 — normative)

Purpose: a damaged device is swapped for a new unit and the mesh is restored without manual reconfiguration. Runs as a step-by-step wizard:

1. **Select the device to replace** — typically shown offline; source of truth is its **latest config snapshot** (§2), so the wizard works even though the old device is dead.
2. **Select the replacement** — pick from freshly discovered, unconfigured devices (highlighted as "new"), or enter an IP manually; the app verifies reachability and reads its identity.
3. **Compatibility check:**
   - *Same model* → full 1:1 config copy (names, channel settings, default states, timers, schedules, actions).
   - *Same generation, different model* → copy the intersecting settings; list every skipped item explicitly.
   - *Cross-generation (e.g. dead Gen1 2.5 → Plus 2PM)* → the adapter layer translates: channel config and names map to `Switch.SetConfig`/`Cover.SetConfig`, Gen1 action URLs become Gen2 webhooks with equivalent events. Untranslatable items are listed, never silently dropped. (Same-model replacement is P0; cross-generation translation is P1 but the adapter interfaces must support it from the start.)
4. **Network identity** — choose one:
   - *Take over the old IP*: write static-IP config to the new device (mesh URLs keep working unchanged), or
   - *Keep the new IP*: the app rewrites **every action URL on every other device** that pointed to the old device, using the guided-builder generator so rewritten URLs are always syntactically correct.
5. **Dry-run diff → apply → verify:** full preview of every write (to the new device and to every device whose URLs get rewritten); a pre-write snapshot of the new device is taken for rollback; after applying, everything is re-read and compared, ending in a per-device success report.
6. The old device is marked `replaced` (history retained, hidden from the graph by default); edges are rebuilt.

## 7. Later phases (updated scope)
- **Phase 2:** action editing = the guided builder (§4), built per the detailed plan in §5.
- **Phase 3:** bulk base configuration with per-generation adapters, dry-run diff, apply report.
- **Phase 4:** device replacement wizard (§6), firmware management, full-network backup export/import, link auto-repair, HA add-on packaging (Ingress).

## Non-goals (v1)
MQTT edges · drag-to-draw edge creation · Pro/Wave/BLU devices · multi-user auth for the UI · HA add-on packaging (until Phase 4).
