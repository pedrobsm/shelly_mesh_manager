Read docs/SPEC.md before implementing. Follow §3 pinned versions exactly. Every Phase must pass its Definition of Done checklist before merging.
Use GitHub Issues with one milestone per phase. Turn each Definition-of-Done checkbox into an issue, and file bugs you find during real-LAN testing as issues too. Keep it up-to-date and use it also to hand over manual work for me.
Keep this file updated.

## Project state

**Phase 1 (read-only) is merged to `main`** (PR #16, PR #18). The 2026-10-27 real-LAN pass
(PR #31, docs) filed #27–#30; all four are fixed on the current branch.
**Phase 2 is in progress.** Done: the §3.5 table completed, `generator.py` + the §5.2
roundtrip suite, all of §5.1 (`GET/PUT /api/slots/{id}`, `POST /api/url/build|parse|test`,
`GET/PUT /api/settings`), the §5.3 write adapters with verify-after-write, per-device locking
and the audit log, §5.5 simulated writes, and the §5.4 slot editor. What is left for §5.6: the
hardware pass (#21). The 2026-10-28 round added the two scan buttons, slot editing from the
device drawer, and fixed the Gen1 action write (#35–#38).

### Layout
```
backend/app/   main.py config.py db.py models.py resolver.py generator.py graph.py api.py
               demo_fixtures.py
               discovery/ (__init__.py = scan orchestration, mdns.py, range_scan.py)
               adapters/  (base.py, gen1.py, gen2.py)
backend/tests/ test_resolver.py (§3.5 table), test_demo_graph.py (§3.8 fixtures),
               test_roundtrip.py (§5.2 backbone), test_snapshots.py, test_auth.py, test_api.py
frontend/src/  api.ts, graph/ (GraphView.tsx nodeRenderer.ts styles.ts),
               components/ (DeviceDrawer.tsx EdgePopover.tsx Toolbar.tsx)
Dockerfile · docker-compose.yml · Makefile · README.md
```

### Conventions worth keeping
- The resolver (`resolver.py`) is the single place that knows the §3.5 pattern table, and
  `generator.py` is its exact inverse. `tests/test_roundtrip.py` builds its cases from
  `supported_commands()`, so a command added to one side without a row on the other fails
  immediately. Anything the generator cannot emit is refused with `UnsupportedCommand`, and
  §4's form is built from `supported_commands()` so it can only offer what works.
- Param names are per generation, not translated behind the user's back: Gen1 `timer` is Gen2
  `toggle_after`, a Gen1 dimmer `step` (%) is a Gen2 `fade_rate`. The builder accepts the
  canonical name and reports the native one it emitted.
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
- View toggles (routing, labels, save-positions, the two visibility switches) live in
  `localStorage` under `shelly-mesh-manager.view-options`; only node positions go to the server.
  Cytoscape handlers are registered once, so they read the live values through `optionsRef`.
- `slots.py` owns every write: it resolves `UrlSpec`s (refusing a raw URL aimed at an
  inventoried device), takes the per-device lock, writes through the adapter, re-reads the
  device and compares field by field, then mirrors the result locally and rebuilds the edges.
  A write is never reported as a success on the strength of a 200 alone.
- `SlotDetail.revision` is a hash of the stored slot; the editor sends it back as
  `expect_revision` so a re-scan in the meantime is caught rather than overwritten.
- A `raw_external` URL aimed at an inventoried device is refused — unless it is already stored
  on that slot, which lets the editor keep an unparsed URL it cannot rebuild without letting
  the client compose a new Shelly URL.
- Gen2/3 inventory synthesizes an empty slot per supported event × component with no hook
  (`gen2.synthetic_slots`, id `…:newhook:{event}:{cid}`), which is how the first action on a
  Plus/Gen3 device gets added (#33). Writing one calls `Webhook.Create` and the slot is re-keyed
  to `…:hook:{id}` with the id the device chose, so use `gen2.hook_id_of()` rather than parsing
  the slot id by hand.
- Gen1 action writes hand-build their query string (`gen1.action_query()`): the `urls[]` brackets
  must reach the firmware literally. A normal query builder percent-encodes the parameter *name*
  to `urls%5B%5D`, which the firmware answers 200 to while applying only `enabled` — so a slot
  could be switched on and off but never edited or cleared (#38).
- `POST /api/scan?scope=known` re-reads only the inventoried IPs (`discovery.known_addresses()`)
  and never marks anything offline when the inventory is empty; it refuses with a message instead
  (#35). Both scopes go through `ScanManager`, so the 409 still holds.
- `Database.transaction()` is the only way to run several statements as one unit; `db.execute()`
  takes the connection lock per statement, which let two concurrent `rebuild_edges()` calls (a
  scan finishing while a slot is saved) collide on `edges.id`.
- A save that leaves a slot disabled reports it in the editor with a "show inactive actions"
  shortcut: the edges *are* created, the default view just hides them, which otherwise reads as a
  write that did nothing (#37).
- Ports are painted into the node's image, so Cytoscape cannot hit-test them. `slotAt()` in
  `GraphView.tsx` maps a click back to a port row using the geometry the renderer used; that is
  one way to open the slot editor — a synthetic `tap` carries no position, which means the node
  itself. The other way is the slot rows in the device drawer, which are buttons (#36); the port
  is a small target and a dense graph can make it unhittable.
- `edges` is a derived cache: `rebuild_edges()` runs at the end of every scan, never incrementally.
- Snapshot comparison ignores volatile fields (`adapters/base.py`): `VOLATILE_KEYS` everywhere,
  plus `VOLATILE_IN_CONTAINER` for generic names that are live readings only inside `meters[]`,
  `emeters[]`, `rollers[]`, `relays[]`, `lights[]` — so a setting that merely shares a name such
  as `power` is still compared (#12). What is stored stays the verbatim dump. When a snapshot
  *is* kept, the changed key paths are logged at INFO: that is how an unlisted volatile field
  gets found on real hardware.
- Every scan goes through `ScanManager`, the boot scan included (#11); it is the only thing that
  makes `POST /api/scan` answer 409 while one runs.
- Target classification uses `match_shelly_path()` (path shape alone), command extraction uses
  `match_command()` (path + query), so one host can never split into an external node and a
  ghost (#13).
- `POST /api/devices/{id}/rescan` (#17) re-reads one device through `rescan_device()` and
  rebuilds the edges; it is refused with 409 while a full scan runs. It is one endpoint beyond
  what SPEC §3.6 calls the complete Phase 1 surface — see the handover below.
- Device ids are MAC-derived and stable; IPs are not. Upserts key on the id. `gen1_device_id()`
  in `adapters/base.py` is the only place a Gen1 id is formed — the full-inventory path and the
  partial path used for a device that refuses inventory must agree, or the same device is stored
  twice (#27). The hostname is a fallback only: changing it in the device's UI must not orphan
  everything keyed on it (#28).
- `migrate_device_ids()` runs at startup (outside demo mode, whose fixtures are hand-named) and
  re-keys any record whose stored `raw_info` says it should be called something else. Channels,
  slots and URLs are dropped and rebuilt by the next scan; credentials, config snapshots and the
  node's position move across, because they cannot be re-read from the device.
- A scan that finds another record on the same IP absorbs it when the MAC matches, and parks it
  at `unassigned:` only when it is genuinely a different device.
- `scan_runs` rows left open by a restart are closed at startup (#29); `ScanManager` also writes
  an end on cancellation, which covers the orderly shutdown.
- `DATA_DIR` (dev-only, default `/data`) is the one env var not in the §3.2 table.

### Commands
`make dev` · `make build` · `make demo` · `make up` · `make test` (pytest + tsc).

## Issue tracking

Phase 1 Definition-of-Done checkboxes are issues #1–#8 (label `definition-of-done`), plus #9,
#10 and the enhancement #17; all carry `phase-1`. `docs/PHASE1-ISSUES.md` indexes them. Real-LAN testing filed
bugs #11/#12/#13 and added result comments to #2/#3/#5/#6/#7/#10; #1/#8/#9 are closed as
verified. Full write-up in `docs/PHASE1-REALLAN.md`.

Phase 2 Definition-of-Done checkboxes are #19–#25 (`definition-of-done`, `phase-2`), plus the
spec gaps #26 and #33. The 2026-10-28 round of use filed #35 (second scan button), #36 (edit a
slot from the drawer), #37 (saved action drew no line) and #38 (Gen1 edit/delete ignored);
#35–#37 are closed, #38 waits on hardware. #39 is the one open decision from that round.

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
- [x] **Fix bugs #11 (startup-scan race), #12 (Gen1 snapshot churn), #13 (resolver split node).**
      Fixed with regression tests; #11 and #13 reproduce in the suite and are verifiable here.
- [x] **Validate #12 on the real LAN.** Done 2026-10-26: live readings in `meters[]`/`rollers[]`/
      `relays[]` were the cause; after the fix three untouched scans wrote 0 rows across 11 Gen1
      devices and a rename wrote exactly one. DoD #7 passes. Details in `docs/PHASE1-REALLAN.md`.
- [x] **One more 3-scan run after the container-scoping follow-up.** Done 2026-10-27 on `main`
      @ 83c1a5b: 0 new rows on every device, no `config changed` log lines.
- [x] **Settle the §3.5 dimmer gap.** Done: dimmer rows, plus the two more the generator
      needed (Gen1 `go=to_pos`, Gen2 `Light.Toggle`). SPEC §1.3/§2/§3.5/§4 amended.
- [ ] **Gen2/3 RGBW and white components have no §3.5 row**, so `supported_commands()` returns
      nothing for them and the builder refuses those targets. Needs `RGBW.Set`/`RGB.Set` rows
      before a Gen3 RGBW device can be a target. No such device on the tested LAN.
- [ ] **Decide whether `POST /api/devices/{id}/rescan` (#17) joins SPEC §3.6**, which calls its
      endpoint list the complete Phase 1 surface. The endpoint is read-only towards devices.
- [x] **DoD #4** — validated 2026-10-27 on Gen1 and Gen3: lock, credential entry, and removing
      the password then re-scanning all work. The one defect found (rejected credentials hid the
      form) is fixed.
- [ ] **Gen3 discovery (#2)** — a Gen3 relay (`S3SW-001P16EU`, 192.168.33.175) was found and
      inventoried 2026-10-27. Gen3 RGBW and cover are still not on the LAN.
- [ ] **40-device scan timing (#6)** — no 40-device LAN available. The /24 scan takes 56 s and
      is dominated by dead addresses (see #2).
- [ ] **Credentials follow-up (#4)** — re-run on a chosen test device: set a password, re-scan,
      enter credentials, change the password and re-scan (credentials must be rejected and the
      form must reappear), then remove the password. Needs the device chosen and an explicit
      go-ahead, because setting a password is a write to the device.
- [ ] **File-or-fix from the 2026-10-27 pass:** #27 (protected device gets a second record),
      #28 (Gen1 id from hostname, not MAC), #29 (`scan_runs` left unfinished on restart),
      #30 (external edges to one webhook all read `/webhook`, label decision).
- [x] **Fix the dead action on device `192.168.33.32`** ("Kitchen entrance switch"): now
      `/white/3` on the RGBW2 and resolves `ok`. Verified 2026-10-27.
- [ ] **Confirm the Gen1 write fix (#38) on real hardware** — on a Gen1 device: add a URL,
      change it, then delete it, each time checking the device's own web UI. The bug was that
      the firmware answered 200 and ignored `urls%5B%5D`, so only enable/disable worked; the
      fix sends the brackets literally and is covered by tests, but only hardware proves it.
      Part of the #21 hardware pass.
- [ ] **Decide on #39** — saving a slot re-emits every parsed row through `generator.py`, so a
      legacy URL aimed at a Gen2 device (`/relay/0?turn=on`) is rewritten to its native form
      (`/rpc/Switch.Set?id=0&on=true`) even when the user only toggled `enabled`. Harmless but
      surprising, and visible in the device's own UI. Three options in the issue.
