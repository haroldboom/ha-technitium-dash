"""Buttons for the Technitium Cluster integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import TechnitiumDashError
from .const import DOMAIN
from .coordinator import TechnitiumDashConfigEntry, TechnitiumDashCoordinator
from .entity import TechnitiumClusterEntity, TechnitiumNodeEntity, async_add_node_entities

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TechnitiumDashConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up buttons."""
    coordinator = entry.runtime_data
    async_add_entities([ClusterFlushCacheButton(coordinator), RecheckDriftButton(coordinator)])
    async_add_node_entities(
        entry,
        async_add_entities,
        lambda coord, short: [NodeFlushCacheButton(coord, short)],
    )


class NodeFlushCacheButton(TechnitiumNodeEntity, ButtonEntity):
    """Flush one node's DNS cache."""

    _attr_translation_key = "flush_cache"

    def __init__(self, coordinator: TechnitiumDashCoordinator, short: str) -> None:
        """Initialise."""
        super().__init__(coordinator, short, "flush_cache")

    async def async_press(self) -> None:
        """POST tapi/cache/flush for this node."""
        node = self.node
        if node is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="unknown_node",
                translation_placeholders={
                    "node": self.short,
                    "nodes": ", ".join(self.coordinator.nodes),
                },
            )
        await self.coordinator.async_write_nodes("cache/flush", [node], {})
        await self.coordinator.async_request_refresh()


class ClusterFlushCacheButton(TechnitiumClusterEntity, ButtonEntity):
    """Flush the DNS cache on every node."""

    _attr_translation_key = "flush_cache_all"

    def __init__(self, coordinator: TechnitiumDashCoordinator) -> None:
        """Initialise."""
        super().__init__(coordinator, "flush_cache")

    async def async_press(self) -> None:
        """POST tapi/cache/flush once per node."""
        await self.coordinator.async_write_nodes(
            "cache/flush", self.coordinator.resolve_nodes(None), {}
        )
        await self.coordinator.async_request_refresh()


class RecheckDriftButton(TechnitiumClusterEntity, ButtonEntity):
    """Force a drift recompute, then refresh."""

    _attr_translation_key = "recheck_drift"

    def __init__(self, coordinator: TechnitiumDashCoordinator) -> None:
        """Initialise."""
        super().__init__(coordinator, "recheck_drift")

    async def async_press(self) -> None:
        """GET api/drift?force=1 then refresh the summary."""
        try:
            await self.coordinator.client.force_drift()
        except TechnitiumDashError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="drift_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        await self.coordinator.async_request_refresh()
