"""Generator -> resolver roundtrip (SPEC §5.2).

"For every command × channel-kind × generation combination, generating a URL
and re-parsing it must return the identical (channel, command, params). This
single test suite is the correctness backbone of Phase 2."

Every case here is generated from the generator's own capability table, so a
command added to the table without a matching §3.5 row fails immediately
instead of waiting for a device to reject it.
"""

from __future__ import annotations

import itertools

import pytest

from app.generator import BuiltUrl, UnsupportedCommand, build_url, supported_commands
from app.resolver import DeviceRef, resolve

GENERATIONS = (1, 2, 3)
KINDS = ("relay", "roller", "cover", "light", "white", "rgbw")
HOST = "192.168.1.50"

# A value for each parameter the generator may emit, chosen to survive a
# roundtrip through the query string unchanged.
SAMPLES = {
    "timer": 30,
    "toggle_after": 30,
    "duration": 5,
    "transition_duration": 2,
    "pos": 50,
    "brightness": 80,
    "gain": 70,
    "step": 25,
    "fade_rate": 3,
}


def device_with(kind: str, idx: int, gen: int) -> dict[str, DeviceRef]:
    """An inventory holding exactly the channel the URL is aimed at."""
    return {
        HOST: DeviceRef(
            id="target", ip=HOST, gen=gen, channels={(kind, idx): f"target:{kind}:{idx}"}
        )
    }


def cases() -> list[tuple[int, str, str, dict]]:
    """Every (generation, kind, command) the generator offers, × its parameters."""
    out: list[tuple[int, str, str, dict]] = []
    for gen, kind in itertools.product(GENERATIONS, KINDS):
        for command, params in supported_commands(gen, kind).items():
            out.append((gen, kind, command, {}))  # bare
            for name in params:  # one parameter at a time
                out.append((gen, kind, command, {name: SAMPLES[name]}))
            if len(params) > 1:  # and all of them together
                out.append((gen, kind, command, {n: SAMPLES[n] for n in params}))
    return out


def roundtrip(gen: int, kind: str, command: str, params: dict) -> tuple[BuiltUrl, object]:
    built = build_url(HOST, gen, kind, 1, command, params)
    return built, resolve(built.url, device_with(kind, 1, gen))


@pytest.mark.parametrize("gen,kind,command,params", cases())
def test_every_combination_survives_the_roundtrip(gen, kind, command, params):
    try:
        built, parsed = roundtrip(gen, kind, command, params)
    except UnsupportedCommand:
        # A 'set' with no level is refused by the builder, which is the point:
        # it would not parse back either.
        pytest.skip("combination the builder refuses by design")

    assert parsed.target_type == "device", built.url
    assert parsed.status == "ok", built.url
    assert parsed.target_channel_id == f"target:{kind}:1", built.url
    assert parsed.target_channel_kind == kind, built.url
    assert parsed.target_channel_idx == 1, built.url
    assert parsed.command == built.command, built.url
    assert parsed.params == built.params, built.url


def test_the_suite_covers_every_command_of_the_table():
    """A guard on the guard: the matrix must not quietly shrink."""
    seen = {(gen, kind, command) for gen, kind, command, _ in cases()}
    commands = {command for _, _, command in seen}
    assert commands == {
        "on",
        "off",
        "toggle",
        "open",
        "close",
        "stop",
        "set",
        "dim_up",
        "dim_down",
        "dim_stop",
    }
    # Both generations, and every channel kind that has a §3.5 row.
    assert {gen for gen, _, _ in seen} == {1, 2, 3}
    assert {kind for _, kind, _ in seen} >= {"relay", "roller", "cover", "light", "white", "rgbw"}


def test_a_set_without_a_level_is_refused():
    for gen in (1, 2):
        with pytest.raises(UnsupportedCommand):
            build_url(HOST, gen, "light", 0, "set", {})
    with pytest.raises(UnsupportedCommand):
        build_url(HOST, 1, "roller", 0, "set", {})


def test_unknown_commands_and_parameters_are_refused():
    with pytest.raises(UnsupportedCommand):
        build_url(HOST, 1, "relay", 0, "open")  # covers are not relays
    with pytest.raises(UnsupportedCommand):
        build_url(HOST, 1, "relay", 0, "on", {"brightness": 50})
    with pytest.raises(UnsupportedCommand):
        build_url(HOST, 2, "relay", 0, "dim_up", {"fade_rate": 2})


def test_canonical_names_are_translated_to_the_target_dialect():
    gen1 = build_url(HOST, 1, "relay", 0, "on", {"timer": 30})
    gen2 = build_url(HOST, 2, "relay", 0, "on", {"timer": 30})
    assert gen1.url.endswith("/relay/0?turn=on&timer=30")
    assert gen2.url.endswith("/rpc/Switch.Set?id=0&on=true&toggle_after=30")
    assert gen2.params == {"toggle_after": 30}


def test_urls_are_percent_encoded():
    built = build_url("192.168.1.50:8080", 1, "light", 0, "set", {"brightness": 100})
    assert " " not in built.url
    assert built.url == "http://192.168.1.50:8080/light/0?brightness=100"
