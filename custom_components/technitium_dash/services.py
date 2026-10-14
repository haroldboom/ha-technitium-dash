"""Services (actions) for the Technitium Cluster integration."""

from __future__ import annotations

import re

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
import homeassistant.helpers.config_validation as cv

from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_DOMAIN,
    ATTR_MINUTES,
    ATTR_NODE,
    DOMAIN,
    PAUSE_MINUTES,
    SERVICE_PAUSE_BLOCKING,
    SERVICE_RESUME_BLOCKING,
    SERVICE_WRITE_PATHS,
)
from .coordinator import TechnitiumDashConfigEntry

SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DOMAIN): cv.string,
        vol.Optional(ATTR_NODE): cv.string,
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)

PAUSE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_MINUTES): vol.All(vol.Coerce(int), vol.In(PAUSE_MINUTES)),
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)
RESUME_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string})

_LABEL = re.compile(r"^(?!-)[a-z0-9_-]{1,63}(?<!-)$")


def normalize_domain(raw: str) -> str:
    """Loosely clean a user-supplied domain.

    Accepts things like " HTTPS://Ads.Example.com/path?x=1 " and returns
    "ads.example.com". Raises ValueError when nothing domain-like is left.
    """
    value = raw.strip().lower()
    value = re.sub(r"^[a-z][a-z0-9+.-]*://", "", value)  # scheme
    value = re.split(r"[/?#]", value, maxsplit=1)[0]  # path, query, fragment
    value = value.rsplit("@", 1)[-1]  # userinfo
    if value.startswith("[") or value.count(":") > 1:
        raise ValueError(raw)  # IPv6 literal is not a zone name
    value = value.split(":", 1)[0]  # port
    value = value.removeprefix("*.").strip(".")
    if not value:
        raise ValueError(raw)
    try:
        value = value.encode("idna").decode("ascii")
    except UnicodeError as err:
        raise ValueError(raw) from err
    labels = value.split(".")
    if len(value) > 253 or not all(_LABEL.match(label) for label in labels):
        raise ValueError(raw)
    return value


def _get_entry(hass: HomeAssistant, call: ServiceCall) -> TechnitiumDashConfigEntry:
    entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
    if entry_id:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_found",
                translation_placeholders={"entry_id": entry_id},
            )
        entries = [entry]
    else:
        entries = hass.config_entries.async_entries(DOMAIN)
        if len(entries) > 1:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="entry_required"
            )
    if not entries or entries[0].state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="entry_not_loaded")
    return entries[0]


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the domain-write actions (once, from async_setup)."""

    async def _handle(call: ServiceCall) -> None:
        raw = call.data[ATTR_DOMAIN]
        try:
            domain = normalize_domain(raw)
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_domain",
                translation_placeholders={"domain": raw},
            ) from err
        entry = _get_entry(hass, call)
        coordinator = entry.runtime_data
        node = call.data.get(ATTR_NODE)
        nodes = coordinator.resolve_nodes(node)
        if not nodes:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_node",
                translation_placeholders={
                    "node": node or "",
                    "nodes": ", ".join(coordinator.nodes) or "none",
                },
            )
        await coordinator.async_write_nodes(
            SERVICE_WRITE_PATHS[call.service], nodes, {"domain": domain}
        )
        await coordinator.async_request_refresh()

    for service in SERVICE_WRITE_PATHS:
        hass.services.async_register(DOMAIN, service, _handle, schema=SERVICE_SCHEMA)

    async def _pause(call: ServiceCall) -> None:
        coordinator = _get_entry(hass, call).runtime_data
        await coordinator.async_write_cluster(
            "settings/temporaryDisableBlocking", {"minutes": call.data[ATTR_MINUTES]}
        )
        await coordinator.async_request_refresh()

    async def _resume(call: ServiceCall) -> None:
        coordinator = _get_entry(hass, call).runtime_data
        await coordinator.async_write_cluster("settings/set", {"enableBlocking": True})
        await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, SERVICE_PAUSE_BLOCKING, _pause, schema=PAUSE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RESUME_BLOCKING, _resume, schema=RESUME_SCHEMA)
