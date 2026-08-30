Read docs/SPEC.md before implementing. Follow §3 pinned versions exactly. Every Phase must pass its Definition of Done checklist before merging.
Use GitHub Issues with one milestone per phase. Turn each Definition-of-Done checkbox into an issue, and file bugs you find during real-LAN testing as issues too. Keep it up-to-date and use it also to hand over manual work for me.
Keep this file updated.

## Project state

**Phase 1 (read-only: discovery + inventory + graph) is implemented.** Phase 2+ not started.

### Layout
```
backend/app/   main.py config.py db.py models.py resolver.py graph.py api.py demo_fixtures.py
               discovery/ (__init__.py = scan orchestration, mdns.py, range_scan.py)
               adapters/  (base.py, gen1.py, gen2.py)
backend/tests/ test_resolver.py (§3.5 pattern table), test_demo_graph.py (§3.8 fixtures)
frontend/src/  api.ts, graph/ (GraphView.tsx nodeRenderer.ts styles.ts),
               components/ (DeviceDrawer.tsx EdgePopover.tsx Toolbar.tsx)
Dockerfile · docker-compose.yml · Makefile · README.md
```

### Conventions worth keeping
- The resolver (`resolver.py`) is the single place that knows the §3.5 pattern table; Phase 2's
  URL generator must be its exact inverse and reuse `DeviceRef`/`Resolution`.
- Node cards are drawn as SVG inside `nodeRenderer.ts`; edges anchor to port rows through
  Cytoscape `source-endpoint`/`target-endpoint` offsets. Cytoscape ignores custom endpoints on
  self-loops, hence the dedicated `edge.loop` style.
- `edges` is a derived cache: `rebuild_edges()` runs at the end of every scan, never incrementally.
- Device ids are MAC-derived and stable; IPs are not. Upserts key on the id.
- `DATA_DIR` (dev-only, default `/data`) is the one env var not in the §3.2 table.

### Commands
`make dev` · `make build` · `make demo` · `make up` · `make test` (pytest + tsc).

## Issue tracking

Phase 1 Definition-of-Done checkboxes are issues #1–#8 (label `definition-of-done`), plus #9
and #10; all carry `phase-1`. `docs/PHASE1-ISSUES.md` indexes them with what each one still
needs. The available GitHub tooling cannot create milestones — see below.

## Manual work handed over

- [x] **Create the milestone *Phase 1 — discovery, inventory, graph* and attach #1–#10.** Done.
- [x] **Verify `make demo` on a machine with Docker registry access** (#9). Verified 2026-08-30:
      `docker compose --profile demo up --build` builds the two-stage image (`node:20-alpine`
      → `python:3.12-slim`) and serves the fixture network on :8099. `/api/health` 200
      (`demo:true`); `/api/graph` returns 8 nodes / 7 edges; SPA + hashed assets load; bogus
      `/api/*` paths 404; SPA deep links fall back to 200. Headless-Chromium render shows the
      6 Shelly devices, the Home Assistant external node, the dangling `192.168.1.99` node and
      all 7 command-labelled edges, with **zero console/page errors**.
- [ ] **Real-LAN testing** (#2–#7: mDNS discovery, range-scan fallback, auth-protected device,
      offline device, 40-device timing, snapshot-on-change). File bugs as issues. Still the only
      outstanding Phase 1 handover item.
