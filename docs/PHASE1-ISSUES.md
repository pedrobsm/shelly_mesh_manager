# Phase 1 — issue backlog (to be filed on GitHub)

CLAUDE.md asks for one milestone per phase and one issue per Definition-of-Done
checkbox. This session could not create them: the GitHub App has read-only access
to `pedrobsm/shelly_mesh_manager` (`403 Resource not accessible by integration`
on both `POST /issues` and `POST /git/refs`). File these once write access is
restored, under milestone **Phase 1 — discovery, inventory, graph**.

Status column: what this session was able to verify in a container with no LAN
and no Docker registry access.

| # | Issue (from SPEC §3.10) | Status |
|---|---|---|
| 1 | `make demo` → graph renders the full fixture network correctly on first load, matching every rule in §1 (verify each fixture bullet visually) | Verified natively (Vite build served by FastAPI, `DEMO_MODE=true`, headless Chromium: 8 nodes, 7 edges, all four edge statuses, zero console errors). **`make demo` itself is unverified** — see issue 9 |
| 2 | `make up` on a real LAN discovers mixed Gen1/Gen2/Gen3 devices via mDNS; with mDNS unavailable, `SCAN_SUBNET` range scan finds them | Not verifiable here — needs real hardware |
| 3 | Every action URL between real devices appears as an edge with the correct command label; URLs to HA appear under one external node | Covered by `backend/tests/` for fixtures; needs real-LAN confirmation |
| 4 | Password-protected device shows lock icon; submitting credentials completes its inventory without a full re-scan | Implemented (`POST /api/devices/{id}/credentials` → single-device re-probe); needs real hardware |
| 5 | Unplugging a device + re-scan: node dims (offline), inbound edges keep rendering, its own outbound edges unchanged; a URL to a never-seen IP renders as a red dangling edge | Dangling path verified in the fixture; offline path needs real hardware |
| 6 | Node positions survive container restart. Scan of 40 devices completes < 30 s | Layout persistence implemented (`node_layout` + `PUT /api/layout`); both need real runs |
| 7 | After a scan, every online device has a config snapshot in the DB; changing a setting on a device via its own web UI and re-scanning produces a new snapshot | Snapshot-on-change implemented and exercised in demo mode; needs real hardware |
| 8 | Zero browser console errors; `GET /api/health` returns 200 | Verified |

## Additional issues to file

| # | Issue | Notes |
|---|---|---|
| 9 | Build and run the Docker image once, on a machine with registry access | The dev container's egress policy returns 403 for `production.cloudfront.docker.com`, so `node:20-alpine` and `python:3.12-slim` cannot be pulled. `docker compose config` was validated; the image build itself was not run |
| 10 | Decide the edge label for external (non-Shelly) targets | §1.3 allows only the normalized commands, so an HA webhook edge is labelled `other`. Readable, but "webhook" would say more — a spec change, not a bug |
