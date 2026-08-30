# Phase 1 — issue index

The Definition-of-Done checkboxes of SPEC §3.10 are filed as GitHub issues
[#1–#8](https://github.com/pedrobsm/shelly_mesh_manager/issues?q=label%3Adefinition-of-done),
plus #9 (Docker image build) and #10 (external edge labels). All carry the
`phase-1` label.

**Milestone:** *Phase 1 — discovery, inventory, graph* created and #1–#10 attached. Done.

Verification status as of the Phase 1 branch. `make demo` / #9 verified 2026-08-30
on a host with Docker registry access; #2–#7 still need a real LAN and Shelly hardware.

| Issue | Checkbox | Status |
|---|---|---|
| #1 | `make demo` renders the fixture network on first load | **Verified** — `docker compose --profile demo up --build` serves the fixture network on :8099; headless-Chromium render shows all nodes/edges, zero console errors |
| #2 | mDNS discovery on a real LAN, range scan when mDNS is unavailable | Needs hardware |
| #3 | Every action URL becomes an edge with the correct command label | Pattern table and fixtures covered by tests; real URL shapes need hardware |
| #4 | Lock icon and credential-driven single-device re-probe | Needs a device with auth enabled |
| #5 | Offline device dims and keeps its edges; unknown IP renders dangling | Dangling verified in the fixture; offline path needs hardware |
| #6 | Positions survive a container restart; 40-device scan < 30 s | Persistence verified across backend restarts, not container restarts; timing unmeasured |
| #7 | A config snapshot per online device; a changed setting adds one | Verified in demo mode; needs hardware (watch for volatile fields) |
| #8 | Zero browser console errors; `/api/health` 200 | Verified (re-confirmed in the demo container 2026-08-30) |
| #9 | Docker image builds and `make demo` comes up | **Verified** 2026-08-30 — two-stage build (`node:20-alpine` → `python:3.12-slim`), `/api/health` 200, `/api/graph` 8 nodes / 7 edges, SPA + assets load |
| #10 | External nodes carry edge labels | Verified in the fixture render (HA `webhook lights_all_off`, dangling/unparsed edges labelled) |
