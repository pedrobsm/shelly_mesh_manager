# Phase 1 — issue index

The Definition-of-Done checkboxes of SPEC §3.10 are filed as GitHub issues
[#1–#8](https://github.com/pedrobsm/shelly_mesh_manager/issues?q=label%3Adefinition-of-done),
plus #9 (Docker image build) and #10 (external edge labels). All carry the
`phase-1` label.

**Still to do by hand:** create the milestone *Phase 1 — discovery, inventory,
graph* and attach #1–#10 to it. The GitHub tooling available to the session can
create issues and labels but not milestones.

Verification status as of the Phase 1 branch, in a container with no LAN, no
Shelly hardware and no Docker registry access:

| Issue | Checkbox | Status |
|---|---|---|
| #1 | `make demo` renders the fixture network on first load | Fixture network verified natively; `make demo` itself unverified (blocked by #9) |
| #2 | mDNS discovery on a real LAN, range scan when mDNS is unavailable | Needs hardware |
| #3 | Every action URL becomes an edge with the correct command label | Pattern table and fixtures covered by tests; real URL shapes need hardware |
| #4 | Lock icon and credential-driven single-device re-probe | Needs a device with auth enabled |
| #5 | Offline device dims and keeps its edges; unknown IP renders dangling | Dangling verified in the fixture; offline path needs hardware |
| #6 | Positions survive a container restart; 40-device scan < 30 s | Persistence verified across backend restarts, not container restarts; timing unmeasured |
| #7 | A config snapshot per online device; a changed setting adds one | Verified in demo mode; needs hardware (watch for volatile fields) |
| #8 | Zero browser console errors; `/api/health` 200 | Verified |
