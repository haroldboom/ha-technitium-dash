"""Sensors for the Technitium Cluster integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import TechnitiumDashConfigEntry, TechnitiumDashCoordinator
from .entity import TechnitiumClusterEntity, TechnitiumNodeEntity, async_add_node_entities

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class StatSensorDescription(SensorEntityDescription):
    """A sensor reading one key of a `stats` block (cluster or node)."""

    stat: str


STAT_SENSORS: tuple[StatSensorDescription, ...] = (
    StatSensorDescription(
        key="queries",
        translation_key="queries",
        stat="totalQueries",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    StatSensorDescription(
        key="blocked",
        translation_key="blocked",
        stat="totalBlocked",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    StatSensorDescription(
        key="blocked_pct",
        translation_key="blocked_pct",
        stat="blockedPct",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    StatSensorDescription(
        key="cached_pct",
        translation_key="cached_pct",
        stat="cachedPct",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    StatSensorDescription(
        key="nxdomain",
        translation_key="nxdomain",
        stat="totalNxDomain",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    StatSensorDescription(
        key="server_failure",
        translation_key="server_failure",
        stat="totalServerFailure",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    StatSensorDescription(
        key="clients",
        translation_key="clients",
        stat="totalClients",
        state_class=SensorStateClass.MEASUREMENT,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TechnitiumDashConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            *(ClusterStatSensor(coordinator, d) for d in STAT_SENSORS),
            DriftDifferencesSensor(coordinator),
        ]
    )

    def _node_entities(coord: TechnitiumDashCoordinator, short: str) -> list[SensorEntity]:
        return [
            *(NodeStatSensor(coord, short, d) for d in STAT_SENSORS),
            NodeVersionSensor(coord, short),
        ]

    async_add_node_entities(entry, async_add_entities, _node_entities)


class ClusterStatSensor(TechnitiumClusterEntity, SensorEntity):
    """Cluster-wide query statistic."""

    entity_description: StatSensorDescription

    def __init__(
        self, coordinator: TechnitiumDashCoordinator, description: StatSensorDescription
    ) -> None:
        """Initialise."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | int | None:
        """Value from summary.cluster."""
        return (self.cluster or {}).get(self.entity_description.stat)


class NodeStatSensor(TechnitiumNodeEntity, SensorEntity):
    """Per-node query statistic (unavailable while the node is unreachable)."""

    entity_description: StatSensorDescription

    def __init__(
        self,
        coordinator: TechnitiumDashCoordinator,
        short: str,
        description: StatSensorDescription,
    ) -> None:
        """Initialise."""
        super().__init__(coordinator, short, description.key)
        self.entity_description = description

    @property
    def _stats(self) -> dict[str, Any] | None:
        node = self.node
        return node.get("stats") if node else None

    @property
    def available(self) -> bool:
        """Only while the node answered."""
        return super().available and isinstance(self._stats, dict)

    @property
    def native_value(self) -> float | int | None:
        """Value from node.stats."""
        return (self._stats or {}).get(self.entity_description.stat)


class NodeVersionSensor(TechnitiumNodeEntity, SensorEntity):
    """Technitium version of a node."""

    _attr_translation_key = "version"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: TechnitiumDashCoordinator, short: str) -> None:
        """Initialise."""
        super().__init__(coordinator, short, "version")

    @property
    def native_value(self) -> str | None:
        """Version string."""
        return (self.node or {}).get("version")


class DriftDifferencesSensor(TechnitiumClusterEntity, SensorEntity):
    """Number of settings/zone differences between nodes."""

    _attr_translation_key = "drift_differences"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: TechnitiumDashCoordinator) -> None:
        """Initialise."""
        super().__init__(coordinator, "drift_differences")

    @property
    def _drift(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get("drift") or {}

    @property
    def native_value(self) -> int | None:
        """Total differences."""
        return self._drift.get("differences")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Per-section counts and when drift was last computed."""
        drift = self._drift
        return {
            "checked_at": drift.get("checkedAt"),
            **{f"{k}_differences": v for k, v in (drift.get("sections") or {}).items()},
        }
