"""Base entities and device helpers for the Technitium Cluster integration."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CLUSTER_KEY, CLUSTER_MODEL, CLUSTER_NAME, DOMAIN, MANUFACTURER, NODE_MODEL
from .coordinator import TechnitiumDashConfigEntry, TechnitiumDashCoordinator


def cluster_identifier(entry_id: str) -> tuple[str, str]:
    """Device identifier of the cluster device."""
    return (DOMAIN, f"{entry_id}_{CLUSTER_KEY}")


def node_identifier(entry_id: str, short: str) -> tuple[str, str]:
    """Device identifier of a node device."""
    return (DOMAIN, f"{entry_id}_{short}")


def cluster_device_info(entry: TechnitiumDashConfigEntry) -> DeviceInfo:
    """DeviceInfo for the cluster device."""
    return DeviceInfo(
        identifiers={cluster_identifier(entry.entry_id)},
        name=CLUSTER_NAME,
        manufacturer=MANUFACTURER,
        model=CLUSTER_MODEL,
    )


class TechnitiumDashEntity(CoordinatorEntity[TechnitiumDashCoordinator]):
    """Common base."""

    _attr_has_entity_name = True


class TechnitiumClusterEntity(TechnitiumDashEntity):
    """An entity on the cluster device."""

    def __init__(self, coordinator: TechnitiumDashCoordinator, key: str) -> None:
        """Initialise."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{CLUSTER_KEY}_{key}"
        self._attr_device_info = cluster_device_info(entry)

    @property
    def cluster(self) -> dict[str, Any] | None:
        """Cluster-level data from the latest summary."""
        data = self.coordinator.data or {}
        return data.get("cluster") or None


class TechnitiumNodeEntity(TechnitiumDashEntity):
    """An entity on a node device; identified by the node's short name."""

    def __init__(self, coordinator: TechnitiumDashCoordinator, short: str, key: str) -> None:
        """Initialise."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self.short = short
        node = coordinator.nodes.get(short, {})
        self._attr_unique_id = f"{entry.entry_id}_{short}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={node_identifier(entry.entry_id, short)},
            name=short,
            manufacturer=MANUFACTURER,
            model=NODE_MODEL,
            sw_version=node.get("version"),
            via_device=cluster_identifier(entry.entry_id),
        )

    @property
    def node(self) -> dict[str, Any] | None:
        """This node in the latest summary (None once it has left the cluster)."""
        return self.coordinator.nodes.get(self.short)

    @property
    def available(self) -> bool:
        """Unavailable when the node has disappeared from the summary."""
        return super().available and self.node is not None


def async_add_node_entities(
    entry: TechnitiumDashConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[TechnitiumDashCoordinator, str], Iterable[Entity]],
) -> None:
    """Add node entities now, and for any node that appears in a later poll."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = [s for s in coordinator.nodes if s not in known]
        if not new:
            return
        known.update(new)
        async_add_entities(e for s in new for e in factory(coordinator, s))

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))
