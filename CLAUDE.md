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

## Manual work handed over

- [ ] **Verify `make demo` on a machine with Docker registry access.** The image could not be
      built in the dev container: the egress policy returns 403 for
      `production.cloudfront.docker.com`, so `node:20-alpine` / `python:3.12-slim` cannot be
      pulled. The identical stack was verified natively instead (Vite build served by FastAPI
      with `DEMO_MODE=true`, headless-Chromium render of the fixture network, zero console
      errors) — see the Phase 1 milestone issues.
- [ ] **Real-LAN testing** (mDNS discovery, range-scan fallback, auth-protected device,
      offline device, 40-device timing, snapshot-on-change). File bugs as issues.
