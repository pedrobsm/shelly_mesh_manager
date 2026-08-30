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
- Two edge routings (issue #14), both drawing one line per connection: `direct` bows curves
  apart per source port, `orthogonal` runs each edge through its own vertical channel.
  Cytoscape's own `taxi` router ignores per-port endpoints, so orthogonal waypoints are
  computed in `applyOrthogonalGeometry()` and fed to `round-segments` — which needs
  `edge-distances: 'endpoints'`, otherwise the offsets are measured from node intersections
  and every path comes out skewed. The waypoints are model coordinates, so they are recomputed
  on every node `position` event and after each layout.
- `edges` is a derived cache: `rebuild_edges()` runs at the end of every scan, never incrementally.
- Device ids are MAC-derived and stable; IPs are not. Upserts key on the id.
- `DATA_DIR` (dev-only, default `/data`) is the one env var not in the §3.2 table.

### Commands
`make dev` · `make build` · `make demo` · `make up` · `make test` (pytest + tsc).

## Issue tracking

Phase 1 Definition-of-Done checkboxes are issues #1–#8 (label `definition-of-done`), plus #9
and #10; all carry `phase-1`. `docs/PHASE1-ISSUES.md` indexes them. Real-LAN testing filed
bugs #11/#12/#13 and added result comments to #2/#3/#5/#6/#7/#10; #1/#8/#9 are closed as
verified. Full write-up in `docs/PHASE1-REALLAN.md`.

## Manual work handed over

- [x] **Create the milestone *Phase 1 — discovery, inventory, graph* and attach #1–#10.** Done.
- [x] **Verify `make demo` on a machine with Docker registry access** (#9). Verified 2026-08-30:
      two-stage image builds, `/api/health` 200, `/api/graph` 8 nodes / 7 edges, SPA + assets
      load, `/api/*` 404s, SPA deep-link fallback, headless-Chromium render of the fixture
      network with **zero console/page errors**. Details in `docs/PHASE1-REALLAN.md` / #9.
- [x] **Real-LAN testing (#2–#7)** — done 2026-08-30 against a live 15-device LAN
      (`192.168.33.0/24`, Gen1 + Gen2). **mDNS #2, offline #5, layout-persist #6, console #8
      PASS.** **#7 FAILS** (Gen1 snapshot churn — #12). **#3 caveats** (#13). Bugs #11/#12/#13
      filed, comments on #2/#3/#5/#6/#7/#10, #1/#8/#9 closed. See `docs/PHASE1-REALLAN.md`.
- [ ] **Fix bugs #11 (startup-scan race), #12 (Gen1 snapshot churn), #13 (resolver split node).**
- [ ] **DoD #4** — needs a Shelly with authentication enabled (none on the tested LAN).
- [ ] **Gen3 discovery (#2)** and **40-device scan timing (#6)** — no Gen3 / no 40-device LAN
      available during testing.
- [ ] **Fix the dead action on device `192.168.33.32`** ("Kitchen entrance switch"): its
      on/off actions point at `192.168.33.35/white/30`, but that RGBW2 only has white channels
      0–3. Real config bug in the network, correctly flagged `dangling` by the tool.
