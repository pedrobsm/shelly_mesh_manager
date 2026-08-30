# Phase 1 — issue index

The Definition-of-Done checkboxes of SPEC §3.10 are filed as GitHub issues
[#1–#8](https://github.com/pedrobsm/shelly_mesh_manager/issues?q=label%3Adefinition-of-done),
plus #9 (Docker image build) and #10 (external edge labels). All carry the
`phase-1` label.

**Milestone:** *Phase 1 — discovery, inventory, graph* created and #1–#10 attached. Done.

Verification status. `make demo` / #9 verified 2026-08-30 on a host with Docker
registry access. #2–#7 tested the same day against a **live 15-device LAN**
(`192.168.33.0/24`) — full results and 3 new bugs in
[`PHASE1-REALLAN.md`](PHASE1-REALLAN.md). Still open: #4 (no auth device
available), Gen3 discovery (no Gen3 present), 40-device scan timing.

| Issue | Checkbox | Status |
|---|---|---|
| #1 | `make demo` renders the fixture network on first load | **Verified** — `docker compose --profile demo up --build` serves the fixture network on :8099; headless-Chromium render shows all nodes/edges, zero console errors |
| #2 | mDNS discovery on a real LAN, range scan when mDNS is unavailable | **mDNS verified** (15/15 devices, ~12 s); **range scan verified** (15/15) but ~56 s for a /24 — see PHASE1-REALLAN.md. Gen3 untested. |
| #3 | Every action URL becomes an edge with the correct command label | **Verified with caveats** on real URLs — new **bug #C** (resolver splits an un-inventoried host into two nodes for `?dim=` URLs); `?dim=`/`?brightness=` have no §3.5 row |
| #4 | Lock icon and credential-driven single-device re-probe | **Still needs a device with auth enabled** — none on the tested LAN |
| #5 | Offline device dims and keeps its edges; unknown IP renders dangling | **Verified** — firewall-drop + re-scan: node offline, inbound + 15 outbound edges kept; 3 real dangling edges in the live graph |
| #6 | Positions survive a container restart; 40-device scan < 30 s | **Persistence verified** across `docker compose restart`; **timing not proven** (no 40-device LAN; /24 range scan ~56 s) |
| #7 | A config snapshot per online device; a changed setting adds one | **FAILS on real Gen1 hardware** — new **bug #B**: every Gen1 device writes a snapshot on every scan (volatile `/settings` fields) |
| #8 | Zero browser console errors; `/api/health` 200 | **Verified** — re-confirmed against the real 15-device graph render |
| #9 | Docker image builds and `make demo` comes up | **Verified** 2026-08-30 — two-stage build (`node:20-alpine` → `python:3.12-slim`), `/api/health` 200, `/api/graph` 8 nodes / 7 edges, SPA + assets load |
| #10 | External nodes carry edge labels | Verified in the fixture render; real LAN adds weight — one Gen1 fans 15 unlabeled `other` edges to a single external target |

New bugs from real-LAN testing (file by hand — session GitHub access is read-only):

| # | Title | Labels |
|---|---|---|
| A | Startup scan bypasses ScanManager — concurrent scans, no 409 on boot | `bug` `phase-1` |
| B | Gen1 config snapshot churns on every scan (volatile `/settings` fields) | `bug` `phase-1` `needs-hardware` |
| C | Resolver classifies non-`turn` Shelly path as `external` not `unknown_shelly` | `bug` `phase-1` `spec` |
