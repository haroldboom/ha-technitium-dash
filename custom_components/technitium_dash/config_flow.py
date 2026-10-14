"""Config flow for the Technitium Cluster integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .api import (
    TechnitiumDashAuthError,
    TechnitiumDashClient,
    TechnitiumDashConnectionError,
    normalize_url,
)
from .const import (
    CLUSTER_NAME,
    CONF_ADDON_SLUG,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)
from .coordinator import TechnitiumDashConfigEntry

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
        vol.Optional(CONF_API_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


async def _validate(hass: HomeAssistant, url: str, api_key: str | None) -> str | None:
    """Return an error key, or None when the add-on answered api/health and api/summary."""
    client = TechnitiumDashClient(async_get_clientsession(hass), url, api_key)
    try:
        await client.health()
        await client.summary()
    except TechnitiumDashAuthError:
        return "invalid_auth"
    except TechnitiumDashConnectionError:
        return "cannot_connect"
    except Exception:
        _LOGGER.exception("Unexpected error validating the Technitium Dash add-on")
        return "unknown"
    return None


class TechnitiumDashConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow: add-on URL + optional API key, or Supervisor discovery."""

    VERSION = 1

    _discovery: dict[str, Any]

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: TechnitiumDashConfigEntry,
    ) -> TechnitiumDashOptionsFlow:
        """Options flow."""
        return TechnitiumDashOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manual setup."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                url = normalize_url(user_input[CONF_URL])
            except ValueError:
                errors[CONF_URL] = "invalid_url"
            else:
                await self.async_set_unique_id(url)
                self._abort_if_unique_id_configured()
                self._async_abort_entries_match({CONF_URL: url})
                api_key = (user_input.get(CONF_API_KEY) or "").strip() or None
                if not (error := await _validate(self.hass, url, api_key)):
                    data = {CONF_URL: url}
                    if api_key:
                        data[CONF_API_KEY] = api_key
                    return self.async_create_entry(title=CLUSTER_NAME, data=data)
                errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_hassio(self, discovery_info: HassioServiceInfo) -> ConfigFlowResult:
        """Supervisor discovered the Technitium Dash add-on."""
        config = discovery_info.config
        url = normalize_url(f"http://{config['host']}:{config['port']}")
        data: dict[str, Any] = {CONF_URL: url, CONF_ADDON_SLUG: discovery_info.slug}
        if api_key := config.get(CONF_API_KEY):
            data[CONF_API_KEY] = api_key
        await self.async_set_unique_id(discovery_info.slug)
        # Re-discovery (new port / key) updates the existing entry and reloads it.
        self._abort_if_unique_id_configured(updates=data)
        self._async_abort_entries_match({CONF_URL: url})
        self._discovery = {"data": data, "name": discovery_info.name}
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the discovered add-on."""
        errors: dict[str, str] = {}
        data = self._discovery["data"]
        if user_input is not None:
            if not (error := await _validate(self.hass, data[CONF_URL], data.get(CONF_API_KEY))):
                return self.async_create_entry(title=CLUSTER_NAME, data=data)
            errors["base"] = error
        return self.async_show_form(
            step_id="hassio_confirm",
            description_placeholders={"addon": self._discovery["name"]},
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The add-on started answering 401."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new API key."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            api_key = (user_input.get(CONF_API_KEY) or "").strip() or None
            if not (error := await _validate(self.hass, entry.data[CONF_URL], api_key)):
                data = {k: v for k, v in entry.data.items() if k != CONF_API_KEY}
                if api_key:
                    data[CONF_API_KEY] = api_key
                return self.async_update_reload_and_abort(entry, data=data)
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_API_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            description_placeholders={"url": entry.data[CONF_URL]},
            errors=errors,
        )


class TechnitiumDashOptionsFlow(OptionsFlowWithReload):
    """Polling interval."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])}
            )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=86400,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
