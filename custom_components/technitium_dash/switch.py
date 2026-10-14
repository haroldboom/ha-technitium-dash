"""Blocking switch for the Technitium Cluster integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import TechnitiumDashConfigEntry, TechnitiumDashCoordinator
from .entity import TechnitiumClusterEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TechnitiumDashConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switch."""
    async_add_entities([ClusterBlockingSwitch(entry.runtime_data)])


class ClusterBlockingSwitch(TechnitiumClusterEntity, SwitchEntity):
    """Blocking on every node. Off is a timed pause; Technitium turns blocking back on
    when it ends, and the switch follows on the next update."""

    _attr_translation_key = "blocking"

    def __init__(self, coordinator: TechnitiumDashCoordinator) -> None:
        """Initialise."""
        super().__init__(coordinator, "blocking")

    @property
    def _states(self) -> list[dict[str, Any]]:
        out = []
        for node in self.coordinator.nodes.values():
            blocking = node.get("blocking")
            if isinstance(blocking, dict) and blocking.get("enabled") is not None:
                out.append(blocking)
        return out

    @property
    def available(self) -> bool:
        """Unavailable when no node reported its blocking state."""
        return super().available and bool(self._states)

    @property
    def is_on(self) -> bool:
        """On only while every reachable node is blocking."""
        return all(b["enabled"] for b in self._states)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """When the pause ends."""
        paused = [b["pausedUntil"] for b in self._states if b.get("pausedUntil")]
        return {"paused_until": max(paused) if paused else None}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Resume blocking everywhere (ends a running pause)."""
        await self.coordinator.async_write_cluster("settings/set", {"enableBlocking": True})
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Pause blocking everywhere for the Blocking pause length."""
        await self.coordinator.async_write_cluster(
            "settings/temporaryDisableBlocking", {"minutes": self.coordinator.pause_minutes}
        )
        await self.coordinator.async_request_refresh()
