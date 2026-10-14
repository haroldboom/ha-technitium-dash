"""The Technitium Cluster integration (talks to the Technitium Dash add-on)."""

from __future__ import annotations

from homeassistant.const import CONF_API_KEY, CONF_URL, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import TechnitiumDashClient
from .const import CONF_ADDON_SLUG, DOMAIN
from .coordinator import TechnitiumDashConfigEntry, TechnitiumDashCoordinator
from .entity import cluster_device_info, node_identifier
from .services import async_setup_services

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.NUMBER, Platform.SENSOR, Platform.SWITCH]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: TechnitiumDashConfigEntry) -> bool:
    """Set up a Technitium Cluster from a config entry."""
    client = TechnitiumDashClient(
        async_get_clientsession(hass), entry.data[CONF_URL], entry.data.get(CONF_API_KEY)
    )
    coordinator = TechnitiumDashCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # The cluster device must exist before node devices point at it via via_device.
    device_registry = dr.async_get(hass)
    cluster = cluster_device_info(entry)
    if slug := entry.data.get(CONF_ADDON_SLUG):
        cluster["configuration_url"] = f"homeassistant://hassio/ingress/{slug}"
    device_registry.async_get_or_create(config_entry_id=entry.entry_id, **cluster)

    @callback
    def _sync_versions() -> None:
        """Keep node devices' sw_version in step with the summary."""
        for short, node in coordinator.nodes.items():
            device = device_registry.async_get_device(
                identifiers={node_identifier(entry.entry_id, short)}
            )
            version = node.get("version")
            if device and version and device.sw_version != version:
                device_registry.async_update_device(device.id, sw_version=version)

    entry.async_on_unload(coordinator.async_add_listener(_sync_versions))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TechnitiumDashConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: TechnitiumDashConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow deleting a node device once that node has left the cluster."""
    current = {node_identifier(entry.entry_id, short) for short in entry.runtime_data.nodes}
    cluster = cluster_device_info(entry)["identifiers"]
    return not (device.identifiers & (current | cluster))
