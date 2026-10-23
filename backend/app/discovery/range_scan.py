"""Range-scan fallback (SPEC §3.4.1): every host address in SCAN_SUBNET."""

from __future__ import annotations

import ipaddress
import logging

log = logging.getLogger(__name__)

MAX_HOSTS = 4096


def expand(subnet: str) -> list[str]:
    """Expand a CIDR into host addresses; empty list for an unusable value."""
    subnet = (subnet or "").strip()
    if not subnet:
        return []
    try:
        network = ipaddress.ip_network(subnet, strict=False)
    except ValueError as exc:
        log.warning("invalid SCAN_SUBNET %r: %s", subnet, exc)
        return []
    if network.num_addresses > MAX_HOSTS:
        log.warning("SCAN_SUBNET %s too large (%d addresses)", subnet, network.num_addresses)
        return []
    hosts = network.hosts() if network.num_addresses > 2 else network
    return [str(ip) for ip in hosts]
