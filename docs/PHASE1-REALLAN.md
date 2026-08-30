# Phase 1 — real-LAN test results (2026-08-30)

Run against a live network: **15 Shelly devices** on `192.168.33.0/24`, from the
Docker image (`docker compose up`, host networking), `SCAN_INTERVAL_MIN=0`.

Hardware present:

| Gen | Models | Count |
|---|---|---|
| Gen1 | SHSW-25 (×8), SHSW-44 / 4 Pro (×2), SHIX3-1, SHRGBW2 (white mode), SHEM | 13 |
| Gen2 | SNSW-102P16EU / Plus2PM — 2× switch profile, 1× cover profile | 2 |

No Gen3 and no auth-enabled device were available, so DoD #4 and the Gen3 half
of #2 are still open.

GitHub note: the tooling in the session that ran this is **read-only** for
issues, so the issue edits below (3 new bugs, comments on #2/#3/#5/#6/#7/#10)
must be filed by hand. Ready-to-paste bodies are in this file.

---

## Definition-of-done results

| DoD | Result |
|---|---|
| #2 mDNS + range-scan fallback | **mDNS PASS** — `method=mdns`, all 15 devices, ~11–12 s. **range PASS** — `SCAN_SUBNET=192.168.33.0/24`, all 15, but **~56 s** for a /24 (see bug note below). Gen3 untested (none present). |
| #3 device→device edges + one HA node | **PASS with caveats** — `/relay/{i}?turn=…`, `/rpc/Switch.Toggle`, HA `/api/webhook/…` all resolve correctly; every HA URL collapses onto one `Home Assistant` node. Caveats: (a) resolver bug with `?dim=`/`?brightness=` URLs — new bug #C; (b) `?dim=`/`?brightness=`-only action URLs aren't in the §3.5 table at all, so a real dimmer-step action to an inventoried device would land as `unparsed`. |
| #4 auth device shows lock, completes from creds | **UNTESTED** — no auth-enabled device on the LAN. |
| #5 unplugged device dims, keeps edges; unknown IP dangles | **PASS** — simulated by dropping `192.168.33.11` at the host firewall + re-scan: node went `online=false`, its 1 inbound and 15 outbound edges all still rendered. 3 real `dangling` edges also present (below). |
| #6 layout persists across container restart; 40-dev scan < 30 s | **layout PASS** — a `PUT /api/layout` position survived `docker compose restart` (the `./data` volume). **Timing: not proven** — mDNS scan of 15 devices ~12 s (fine); a /24 range scan ~56 s (state of the world, not 40 devices, but worth fixing — bug note). No 40-device LAN available. |
| #7 snapshot per device; changed setting → new snapshot | **FAIL** — new bug #B: every Gen1 device writes a fresh `config_snapshot` on **every** scan with no config change (Gen2 is stable). Volatile fields in Gen1 `/settings` — exactly the risk called out in the issue. |
| #8 zero console errors; `/api/health` 200 | **PASS** — headless-Chromium render of the real 15-device graph: zero console/page errors; `/api/health` 200. |

Concurrency: `POST /api/scan` correctly returns **409** while a manual scan runs —
**except** during the first few seconds after boot (new bug #A).

---

## New bugs to file

### Bug #A — Startup scan bypasses ScanManager (concurrent scans, no 409 on boot)

**Labels:** `bug`, `phase-1` · **Milestone:** Phase 1

`backend/app/main.py:63` launches the boot scan as
`asyncio.create_task(run_scan(db, settings))` — calling `run_scan()` directly and
bypassing `ScanManager`. `ScanManager.running` stays `False` for the whole boot
scan, so `POST /api/scan` (`api.py:133`) starts a **second** concurrent scan.

Reproduced 100%: POST at t+3 s after container start → `202`; `scan_runs` shows
two overlapping rows both completing:

```
('78bfde8d','mdns',15,'2026-08-30T15:28:00Z','2026-08-30T15:28:12Z')  startup
('b90bf204','mdns',15,'2026-08-30T15:28:01Z','2026-08-30T15:28:12Z')  POST, got 202
```

After ~t+4 s further POSTs return `409` as expected.

Impact: violates SPEC §3.6; double mDNS browse + double inventory + two
`rebuild_edges()` racing on `edges`; doubles first-boot snapshots (feeds bug #B —
after one boot + one manual scan every device had 2 snapshots).

Fix: `app.state.startup_scan = asyncio.create_task(scans.start())` (and expose the
inner task for shutdown). Regression test: `POST /api/scan` → 409 while the
startup task runs.

### Bug #B — Gen1 config snapshot churns on every scan (volatile `/settings` fields)

**Labels:** `bug`, `phase-1`, `needs-hardware` · **Milestone:** Phase 1 · relates to #7

Three consecutive scans of an unchanged network:

```
snapshots/device:  scan1 -> scan2 -> scan3
  every SHSW-25 / SHSW-44 / SHIX3 / SHRGBW2 / SHEM:  1 -> 2 -> 3
  every Plus2PM (Gen2):                              1 -> 1 -> 1
```

Gen1 `/settings` (and/or `/settings/actions`) carries fields that change between
reads — candidates: `time`, `unixtime`, `wifi_sta.rssi`, `ram_free`, `uptime`,
`ratio`/meter readings. The Gen1 adapter snapshots `{settings, actions}` verbatim
and compares JSON equality, so every scan looks like a config change and the
10-snapshot ring fills with noise in ~10 scans.

Fix: strip a denylist of volatile keys (recursively) before the equality check in
the Gen1 snapshot path. Add a test: two `store_snapshot` calls with only volatile
fields differing produce **one** row. Check the Gen2 path for the same (it looked
clean here but `Shelly.GetStatus`-style fields would bite).

### Bug #C — Resolver: un-inventoried Shelly path with a non-`turn` query is classified `external`, not `unknown_shelly`

**Labels:** `bug`, `phase-1`, `spec` · **Milestone:** Phase 1 · relates to #10

Device `192.168.33.22` (SHIX3) has three action URLs to `192.168.33.25`, which is
**not** an inventoried device:

| URL | resolved as |
|---|---|
| `http://192.168.33.25/light/0?turn=toggle` | `unknown_shelly` → `dangling` ✅ |
| `http://192.168.33.25/light/0?dim=up&step=25` | `external` / `ok` ❌ |
| `http://192.168.33.25/light/0?dim=down&step=25` | `external` / `ok` ❌ |

`match_command` (`resolver.py`) only recognises `/light/{i}` when `turn=` is
present, so the `dim=` variants fall through to the generic `external` branch.
Result: **the same host renders as two nodes** — an `external` node *and* an
`unknown_shelly` ghost — which contradicts SPEC §1.1. §3.5 rule 3 says to "match
by path shape only"; the `?turn=` in the table rows is being treated as
mandatory.

Decide: either (a) match `/light|white|color/{i}`, `/relay/{i}`, `/roller/{i}`,
`/rpc/{KnownMethod}` on path shape alone for the *target-classification* step
(keep the query check only for extracting command/params), or (b) document that
non-`turn` Shelly URLs are `external`. (a) matches the spec's stated intent and
removes the split node.

---

## Comments to add to existing issues

**#2** — mDNS PASS (`method=mdns`, 15/15 devices, ~12 s). Range-scan fallback PASS
(`SCAN_SUBNET`, 15/15) but ~56 s for a /24 — every dead IP burns the full
`HTTP_TIMEOUT_S` at concurrency 16 (~254/16 × 3 s). Consider a shorter connect
timeout for the sweep, or higher concurrency, or TCP-connect probe before the
HTTP GET. Gen3 still untested (none on this LAN).

**#3** — device→device labels verified on real URLs: `/relay/0?turn=toggle` →
`toggle`, `/rpc/Switch.Toggle?id=0` → `toggle` (incl. a `disabled` slot), HA
webhooks collapse to one node. Two gaps found — see new bugs #C (split node) and
the note that `?dim=`/`?brightness=` URLs have no §3.5 row. No false `unparsed` in
this network (`0 unparsed`).

**#5** — verified by firewall-dropping `192.168.33.11` + re-scan: `online=false`,
1 inbound + 15 outbound edges retained. Plus 3 genuine `dangling` edges in the
live graph (see below). Physical unplug still worth doing once but the code path
is exercised.

**#6** — layout persistence across `docker compose restart` PASS. Timing still
open: no 40-device LAN; /24 range scan ~56 s (see #2). mDNS path ~12 s.

**#7** — FAILS on real Gen1 hardware — see new bug #B.

**#10** — real-world weight for this decision: `192.168.33.11` alone fans **15**
webhook URLs (`btn_on`, `out_off`, `roller_stop`, …) to one external target, all
currently drawn as identical unlabeled `other` edges. The Gen1 action key
(`btn_on_url` etc.) is right there and would disambiguate every one of them.

---

## Live-graph dangling edges (real config issues in the tested network, not tool bugs)

1–2. `192.168.33.32` ("Kitchen entrance switch") `out_on_url` / `out_off_url` →
   `http://192.168.33.35/white/30?turn=on|off`. The RGBW2 at `.35` has white
   channels **0–3**; channel **30** does not exist → correctly `dangling`. The
   action is dead — likely meant `/white/3` or `/white/0`.
3. `192.168.33.22` (SHIX3) → `http://192.168.33.25/light/0?turn=toggle` — nothing
   at `.25` → correctly `dangling` (ghost node).
