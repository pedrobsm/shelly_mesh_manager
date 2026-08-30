"""mDNS discovery (SPEC §3.4.1): browse for 10 s, keep hosts whose name says 'shelly'."""

from __future__ import annotations

import asyncio
import logging
from typing import Iterable

log = logging.getLogger(__name__)

SERVICE_TYPES = ["_http._tcp.local.", "_shelly._tcp.local."]
BROWSE_SECONDS = 10.0


async def browse(seconds: float = BROWSE_SECONDS) -> list[str]:
    """Return candidate IPv4 addresses of services whose name contains 'shelly'."""
    try:
        from zeroconf import IPVersion, ServiceStateChange
        from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf
    except Exception as exc:  # pragma: no cover - zeroconf missing/unusable
        log.warning("mDNS unavailable: %s", exc)
        return []

    found: set[str] = set()
    pending: list[asyncio.Task] = []
    aiozc = AsyncZeroconf(ip_version=IPVersion.V4Only)

    async def resolve(service_type: str, name: str) -> None:
        info = AsyncServiceInfo(service_type, name)
        try:
            if await info.async_request(aiozc.zeroconf, 3000):
                for address in info.parsed_scoped_addresses():
                    if ":" not in address:
                        found.add(address)
        except Exception as exc:  # pragma: no cover - transient mDNS failures
            log.debug("mDNS resolve failed for %s: %s", name, exc)

    def on_change(zeroconf, service_type, name, state_change, **_: object) -> None:
        if state_change is ServiceStateChange.Removed:
            return
        if "shelly" not in name.lower():
            return
        pending.append(asyncio.ensure_future(resolve(service_type, name)))

    browser = AsyncServiceBrowser(aiozc.zeroconf, SERVICE_TYPES, handlers=[on_change])
    try:
        await asyncio.sleep(seconds)
    finally:
        await browser.async_cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await aiozc.async_close()
    return sorted(found)


def merge_candidates(*groups: Iterable[str]) -> list[str]:
    """Union of candidate lists, order-stable."""
    seen: dict[str, None] = {}
    for group in groups:
        for ip in group:
            seen.setdefault(ip, None)
    return list(seen)
