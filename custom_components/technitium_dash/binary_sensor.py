"""Binary sensors for the Technitium Cluster integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import TechnitiumDashConfigEntry, TechnitiumDashCoordinator
from .entity import TechnitiumClusterEntity, TechnitiumNodeEntity, async_add_node_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TechnitiumDashConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    async_add_entities([NodesInSyncBinarySensor(entry.runtime_data)])
    async_add_node_entities(
        entry,
        async_add_entities,
        lambda coord, short: [
            NodeConnectivityBinarySensor(coord, short),
            NodeBlockingBinarySensor(coord, short),
        ],
    )


class NodeConnectivityBinarySensor(TechnitiumNodeEntity, BinarySensorEntity):
    """On while the add-on can reach the node."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "connectivity"

    def __init__(self, coordinator: TechnitiumDashCoordinator, short: str) -> None:
        """Initialise."""
        super().__init__(coordinator, short, "connectivity")

    @property
    def is_on(self) -> bool:
        """Reachable."""
        return bool((self.node or {}).get("reachable"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Cluster role/state as Technitium reports it."""
        node = self.node or {}
        return {
            "name": node.get("name"),
            "role": node.get("role"),
            "state": node.get("state"),
        }


class NodeBlockingBinarySensor(TechnitiumNodeEntity, BinarySensorEntity):
    """On while ad blocking is enabled on the node."""

    _attr_translation_key = "blocking"

    def __init__(self, coordinator: TechnitiumDashCoordinator, short: str) -> None:
        """Initialise."""
        super().__init__(coordinator, short, "blocking")

    @property
    def _blocking(self) -> dict[str, Any] | None:
        blocking = (self.node or {}).get("blocking")
        return blocking if isinstance(blocking, dict) else None

    @property
    def available(self) -> bool:
        """Unavailable when blocking state is unknown (node unreachable)."""
        return (
            super().available
            and self._blocking is not None
            and self._blocking.get("enabled") is not None
        )

    @property
    def is_on(self) -> bool | None:
        """Blocking enabled."""
        return bool((self._blocking or {}).get("enabled"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """When a temporary pause ends."""
        return {"paused_until": (self._blocking or {}).get("pausedUntil")}


class NodesInSyncBinarySensor(TechnitiumClusterEntity, BinarySensorEntity):
    """Problem sensor: on when the nodes have drifted apart."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "nodes_in_sync"

    def __init__(self, coordinator: TechnitiumDashCoordinator) -> None:
        """Initialise."""
        super().__init__(coordinator, "nodes_in_sync")

    @property
    def _drift(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get("drift") or {}

    @property
    def is_on(self) -> bool | None:
        """True (problem) when drift.ok is false; unknown until drift has run."""
        ok = self._drift.get("ok")
        if ok is None:
            return None
        return not ok

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Difference counts."""
        drift = self._drift
        return {
            "differences": drift.get("differences"),
            "checked_at": drift.get("checkedAt"),
            "sections": drift.get("sections"),
        }
