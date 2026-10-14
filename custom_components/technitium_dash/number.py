"""Blocking pause length slider for the Technitium Cluster integration."""

from __future__ import annotations

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DEFAULT_PAUSE_MINUTES, PAUSE_MAX, PAUSE_MIN, PAUSE_MINUTES, PAUSE_STEP
from .coordinator import TechnitiumDashConfigEntry, TechnitiumDashCoordinator
from .entity import TechnitiumClusterEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TechnitiumDashConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the slider."""
    async_add_entities([PauseLengthNumber(entry.runtime_data)])


class PauseLengthNumber(TechnitiumClusterEntity, RestoreNumber):
    """How long the Blocking switch pauses for. Kept in HA; nothing is sent to Technitium."""

    _attr_translation_key = "pause_length"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = PAUSE_MIN
    _attr_native_max_value = PAUSE_MAX
    _attr_native_step = PAUSE_STEP
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, coordinator: TechnitiumDashCoordinator) -> None:
        """Initialise."""
        super().__init__(coordinator, "pause_length")

    async def async_added_to_hass(self) -> None:
        """Restore the last length after a restart."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            value = int(last.native_value)
            if value in PAUSE_MINUTES:
                self.coordinator.pause_minutes = value
        else:
            self.coordinator.pause_minutes = DEFAULT_PAUSE_MINUTES

    @property
    def native_value(self) -> float:
        """Current length."""
        return self.coordinator.pause_minutes

    async def async_set_native_value(self, value: float) -> None:
        """Store a new length."""
        self.coordinator.pause_minutes = int(value)
        self.async_write_ha_state()
