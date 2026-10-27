"""Every row of the §3.5 pattern table, plus the target/status rules."""

from __future__ import annotations

import pytest

from app.resolver import (
    DeviceRef,
    external_label,
    format_command_label,
    match_command,
    match_shelly_path,
    resolve,
    split_url,
)

HALL = DeviceRef(
    id="hall",
    ip="192.168.1.11",
    gen=2,
    channels={("relay", 0): "hall:relay:0", ("light", 1): "hall:light:1"},
)
BLINDS = DeviceRef(
    id="blinds", ip="192.168.1.21", gen=1, channels={("roller", 0): "blinds:roller:0"}
)
GARAGE = DeviceRef(
    id="garage", ip="192.168.1.13", gen=3, channels={("cover", 0): "garage:cover:0"}
)
INVENTORY = {d.ip: d for d in (HALL, BLINDS, GARAGE)}


@pytest.mark.parametrize(
    "path,kind,idx,command,params",
    [
        ("/relay/0?turn=on", "relay", 0, "on", {}),
        ("/relay/1?turn=off", "relay", 1, "off", {}),
        ("/relay/0?turn=toggle", "relay", 0, "toggle", {}),
        ("/relay/0?turn=on&timer=30", "relay", 0, "on", {"timer": 30}),
        ("/roller/0?go=open", "roller", 0, "open", {}),
        ("/roller/0?go=close&duration=5", "roller", 0, "close", {"duration": 5}),
        ("/roller/0?go=stop", "roller", 0, "stop", {}),
        ("/light/0?turn=on&brightness=80", "light", 0, "on", {"brightness": 80}),
        ("/white/1?turn=off", "white", 1, "off", {}),
        ("/color/0?turn=toggle&timer=10", "rgbw", 0, "toggle", {"timer": 10}),
        ("/rpc/Switch.Set?id=0&on=true", "relay", 0, "on", {}),
        ("/rpc/Switch.Set?id=0&on=false&toggle_after=30", "relay", 0, "off", {"toggle_after": 30}),
        ("/rpc/Switch.Toggle?id=1", "relay", 1, "toggle", {}),
        ("/rpc/Cover.Open?id=0", "cover", 0, "open", {}),
        ("/rpc/Cover.Close?id=0&duration=5", "cover", 0, "close", {"duration": 5}),
        ("/rpc/Cover.Stop?id=0", "cover", 0, "stop", {}),
        ("/rpc/Cover.GoToPosition?id=0&pos=50", "cover", 0, "set", {"pos": 50}),
        ("/rpc/Light.Set?id=0&on=true&brightness=40", "light", 0, "on", {"brightness": 40}),
        ("/rpc/Light.Set?id=0&brightness=40", "light", 0, "set", {"brightness": 40}),
    ],
)
def test_pattern_table(path, kind, idx, command, params):
    _, url_path, query = split_url("http://10.0.0.1" + path)
    match = match_command(url_path, query)
    assert match is not None, path
    assert match.channel_kinds[0] == kind
    assert match.channel_idx == idx
    assert match.command == command
    assert match.params == params


def test_matching_is_case_insensitive_and_tolerates_noise():
    match = match_command("/RPC/switch.set/", {"id": "0", "on": "TRUE", "extra": "x"})
    assert match is not None and match.command == "on"


def test_device_target_ok():
    result = resolve("http://192.168.1.11/relay/0?turn=on", INVENTORY)
    assert result.target_type == "device"
    assert result.target_device_id == "hall"
    assert result.target_channel_id == "hall:relay:0"
    assert result.status == "ok"


def test_disabled_slot_marks_edge_disabled():
    result = resolve("http://192.168.1.13/rpc/Cover.Open?id=0", INVENTORY, slot_enabled=False)
    assert result.status == "disabled"


def test_missing_channel_is_dangling():
    result = resolve("http://192.168.1.11/relay/3?turn=on", INVENTORY)
    assert result.target_type == "device"
    assert result.status == "dangling"


def test_unknown_host_with_shelly_pattern_is_unknown_shelly():
    result = resolve("http://192.168.1.99/relay/0?turn=on", INVENTORY)
    assert result.target_type == "unknown_shelly"
    assert result.status == "dangling"
    assert result.external_host == "192.168.1.99"


def test_device_target_without_pattern_is_unparsed():
    result = resolve("http://192.168.1.11/status", INVENTORY)
    assert result.target_type == "device"
    assert result.status == "unparsed"
    assert format_command_label(result.command, result.params, result.status) == "?"


def test_home_assistant_webhook():
    result = resolve("http://192.168.1.5:8123/api/webhook/lights_all_off", INVENTORY)
    assert result.target_type == "external"
    assert result.webhook_id == "lights_all_off"
    assert result.status == "ok"


def test_plain_external_host():
    result = resolve("http://example.com/hook?x=1", INVENTORY)
    assert result.target_type == "external"
    assert result.external_host == "example.com"
    assert result.webhook_id is None


def test_scheme_less_and_credentialed_urls():
    assert split_url("192.168.1.11/relay/0?turn=on")[0] == "192.168.1.11"
    assert split_url("http://admin:pw@192.168.1.11:8080/relay/0")[0] == "192.168.1.11"


def test_roller_channel_falls_back_to_cover_kind():
    result = resolve("http://192.168.1.13/roller/0?go=stop", INVENTORY)
    assert result.target_channel_id == "garage:cover:0"
    assert result.status == "ok"


# --- issue #13: classification by path shape, not by the query ------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.33.25/light/0?turn=toggle",
        "http://192.168.33.25/light/0?dim=up&step=25",
        "http://192.168.33.25/light/0?dim=down&step=25",
        "http://192.168.33.25/relay/0",
        "http://192.168.33.25/rpc/Switch.Set?id=0&on=maybe",
    ],
)
def test_shelly_shaped_urls_to_an_unknown_host_are_one_ghost(url):
    result = resolve(url, INVENTORY)
    assert result.target_type == "unknown_shelly"
    assert result.status == "dangling"
    assert result.external_host == "192.168.33.25"


def test_unrecognised_query_to_an_unknown_host_keeps_command_other():
    result = resolve("http://192.168.33.25/light/0?effect=3", INVENTORY)
    assert result.command == "other"
    assert result.params == {}
    # The channel is still known from the path, which the ghost node's port uses.
    assert (result.target_channel_kind, result.target_channel_idx) == ("light", 0)


def test_non_shelly_path_on_an_unknown_host_stays_external():
    for url in ("http://192.168.33.25/rpc/Shelly.GetStatus", "http://192.168.33.25/status"):
        result = resolve(url, INVENTORY)
        assert result.target_type == "external", url


def test_unrecognised_query_on_an_inventoried_device_is_still_unparsed():
    # A query no §3.5 row covers still keeps its '?' label on a known device.
    result = resolve("http://192.168.1.11/light/1?effect=3", INVENTORY)
    assert result.target_type == "device"
    assert result.status == "unparsed"
    assert result.target_channel_id is None


# --- dimmer rows added to the §3.5 table --------------------------------------


@pytest.mark.parametrize(
    "path,command,params",
    [
        ("/light/0?dim=up&step=25", "dim_up", {"step": 25}),
        ("/light/0?dim=down&step=10", "dim_down", {"step": 10}),
        ("/light/0?dim=stop", "dim_stop", {}),
        ("/white/1?dim=up&step=5", "dim_up", {"step": 5}),
        ("/light/0?brightness=40", "set", {"brightness": 40}),
        ("/white/1?brightness=100&timer=5", "set", {"brightness": 100, "timer": 5}),
        ("/color/0?gain=70", "set", {"gain": 70}),
        ("/rpc/Light.DimUp?id=0&fade_rate=3", "dim_up", {"fade_rate": 3}),
        ("/rpc/Light.DimDown?id=0", "dim_down", {}),
        ("/rpc/Light.DimStop?id=0", "dim_stop", {}),
    ],
)
def test_dimmer_rows(path, command, params):
    _, url_path, query = split_url("http://10.0.0.1" + path)
    match = match_command(url_path, query)
    assert match is not None, path
    assert match.command == command
    assert match.params == params


def test_a_dimmer_url_to_an_inventoried_light_resolves(caplog):
    result = resolve("http://192.168.1.11/light/1?dim=up&step=25", INVENTORY)
    assert result.target_type == "device"
    assert result.status == "ok"
    assert result.target_channel_id == "hall:light:1"
    assert format_command_label(result.command, result.params, result.status) == "dim up · 25%"


def test_turn_still_wins_over_a_level():
    _, path, query = split_url("http://10.0.0.1/light/0?turn=on&brightness=40")
    match = match_command(path, query)
    assert match is not None and match.command == "on"
    assert match.params == {"brightness": 40}


@pytest.mark.parametrize(
    "path,kinds,idx",
    [
        ("/relay/2", ("relay",), 2),
        ("/roller/0", ("roller", "cover"), 0),
        ("/white/3", ("white", "light"), 3),
        ("/rpc/Cover.Open", ("cover", "roller"), 0),
    ],
)
def test_match_shelly_path_ignores_the_query(path, kinds, idx):
    assert match_shelly_path(path, {}) == (kinds, idx)


@pytest.mark.parametrize("path", ["/", "/status", "/api/webhook/x", "/rpc/Shelly.GetStatus"])
def test_match_shelly_path_rejects_other_paths(path):
    assert match_shelly_path(path, {}) is None


@pytest.mark.parametrize(
    "command,params,status,expected",
    [
        ("on", {}, "ok", "on"),
        ("on", {"timer": 30}, "ok", "on · 30s"),
        ("off", {"toggle_after": 30}, "ok", "off · 30s"),
        ("open", {"duration": 5}, "ok", "open · 5s"),
        ("set", {"pos": 50}, "ok", "set · 50%"),
        ("set", {"brightness": 80}, "ok", "set · b=80"),
        ("other", {}, "unparsed", "?"),
    ],
)
def test_edge_labels(command, params, status, expected):
    assert format_command_label(command, params, status) == expected


# --- §1.3 external labels (issues #10, #30) -----------------------------------


@pytest.mark.parametrize(
    "url,webhook_id,expected",
    [
        ("http://192.168.1.5:8123/api/webhook/lights_all_off", "lights_all_off", "webhook"),
        ("http://192.168.1.180:11505/webhook?data=btn_on_url?0?192.168.1.11", None,
         "/webhook · btn_on_url"),
        ("http://192.168.1.180:11505/webhook?data=roller_stop_url?0?192.168.1.11", None,
         "/webhook · roller_stop_url"),
        ("http://example.com/hook", None, "/hook"),
        ("http://example.com/hook?id=42", None, "/hook · 42"),
        ("http://example.com/hook?empty=&id=42", None, "/hook · 42"),
        ("http://example.com/", None, "/"),
    ],
)
def test_external_labels(url, webhook_id, expected):
    assert external_label(url, webhook_id) == expected


def test_external_labels_tell_same_path_urls_apart():
    """#30: fifteen actions posting to one path must not read identically."""
    urls = [
        f"http://192.168.1.180:11505/webhook?data={event}?0?192.168.1.11"
        for event in ("btn_on_url", "btn_off_url", "roller_open_url", "roller_stop_url")
    ]
    labels = [external_label(url) for url in urls]
    assert len(set(labels)) == len(labels)


def test_a_long_token_is_truncated():
    label = external_label("http://example.com/hook?data=" + "x" * 50)
    assert label == "/hook · " + "x" * 16
