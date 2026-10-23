"""Per-generation device adapters."""

from .base import (
    DeviceInventory,
    InventoryChannel,
    InventorySlot,
    ProbeResult,
    channel_label,
    probe,
    slot_label,
)

__all__ = [
    "DeviceInventory",
    "InventoryChannel",
    "InventorySlot",
    "ProbeResult",
    "channel_label",
    "probe",
    "slot_label",
]
