"""Config/options flow tests."""

from __future__ import annotations

from unittest.mock import patch

import aiohttp
import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.technitium_dash.const import DOMAIN

from .conftest import API_KEY, SUMMARY, URL


@pytest.fixture(autouse=True)
def no_setup():
    """Don't actually set the entry up after the flow creates it."""
    with patch("custom_components.technitium_dash.async_setup_entry", return_value=True) as m:
        yield m


async def _start_user(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def test_user_flow_success(hass: HomeAssistant, mock_addon: AiohttpClientMocker) -> None:
    """URL is normalised, both endpoints are hit, the key is sent as a bearer token."""
    result = await _start_user(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": " HTTP://Addon.LOCAL:8081/ ", "api_key": API_KEY}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Technitium Cluster"
    assert result["data"] == {"url": URL, "api_key": API_KEY}
    assert result["result"].unique_id == URL
    paths = [str(c[1].path) for c in mock_addon.mock_calls]
    assert paths == ["/api/health", "/api/summary"]
    assert mock_addon.mock_calls[1][3]["Authorization"] == f"Bearer {API_KEY}"


async def test_user_flow_no_key(hass: HomeAssistant, mock_addon: AiohttpClientMocker) -> None:
    """No key -> no Authorization header and no api_key in data."""
    result = await _start_user(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": "addon.local:8081"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"url": URL}
    assert "Authorization" not in mock_addon.mock_calls[1][3]


@pytest.mark.parametrize(
    ("mock_kwargs", "error"),
    [
        ({"exc": aiohttp.ClientConnectionError()}, "cannot_connect"),
        ({"exc": TimeoutError()}, "cannot_connect"),
        ({"status": 500, "text": "boom"}, "cannot_connect"),
        ({"status": 401, "json": {"status": "error"}}, "invalid_auth"),
        ({"exc": RuntimeError("weird")}, "unknown"),
    ],
)
async def test_user_flow_errors_then_recover(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_kwargs, error
) -> None:
    """Summary failure maps to the right error; fixing it then succeeds."""
    aioclient_mock.get(f"{URL}/api/health", json={"ok": True})
    aioclient_mock.get(f"{URL}/api/summary", **mock_kwargs)
    result = await _start_user(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": URL, "api_key": "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{URL}/api/health", json={"ok": True})
    aioclient_mock.get(f"{URL}/api/summary", json=SUMMARY)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": URL, "api_key": API_KEY}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_health_unreachable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """api/health failing is cannot_connect (summary never tried)."""
    aioclient_mock.get(f"{URL}/api/health", exc=aiohttp.ClientConnectionError())
    result = await _start_user(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"url": URL})
    assert result["errors"] == {"base": "cannot_connect"}
    assert len(aioclient_mock.mock_calls) == 1


async def test_user_flow_invalid_url(hass: HomeAssistant) -> None:
    """Garbage URL is caught before any request."""
    result = await _start_user(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"url": "http://"})
    assert result["errors"] == {"url": "invalid_url"}


async def test_user_flow_already_configured(
    hass: HomeAssistant, mock_addon: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Same (normalised) URL aborts."""
    config_entry.add_to_hass(hass)
    result = await _start_user(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": "http://ADDON.local:8081/"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_addon.call_count == 0


DISCOVERY = HassioServiceInfo(
    config={"host": "a0d7b954-technitium-dash", "port": 8081, "api_key": "disc-key"},
    name="Technitium Cluster Dashboard",
    slug="a0d7b954_technitium_dash",
    uuid="1234",
)
DISC_URL = "http://a0d7b954-technitium-dash:8081"


async def test_hassio_flow(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Discovery -> confirm -> entry keyed by add-on slug."""
    aioclient_mock.get(f"{DISC_URL}/api/health", json={"ok": True})
    aioclient_mock.get(f"{DISC_URL}/api/summary", json=SUMMARY)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_HASSIO}, data=DISCOVERY
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "hassio_confirm"
    assert result["description_placeholders"] == {"addon": DISCOVERY.name}
    assert aioclient_mock.call_count == 0

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        "url": DISC_URL,
        "api_key": "disc-key",
        "addon_slug": DISCOVERY.slug,
    }
    assert result["result"].unique_id == DISCOVERY.slug
    assert aioclient_mock.mock_calls[1][3]["Authorization"] == "Bearer disc-key"


async def test_hassio_flow_confirm_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A dead add-on shows cannot_connect on the confirm form."""
    aioclient_mock.get(f"{DISC_URL}/api/health", exc=aiohttp.ClientConnectionError())
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_HASSIO}, data=DISCOVERY
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_hassio_flow_already_configured_updates(hass: HomeAssistant) -> None:
    """Re-discovery of the same slug aborts and refreshes host/port/key."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DISCOVERY.slug,
        data={"url": "http://old:1", "api_key": "old", "addon_slug": DISCOVERY.slug},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_HASSIO}, data=DISCOVERY
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data["url"] == DISC_URL
    assert entry.data["api_key"] == "disc-key"


async def test_hassio_flow_matches_manual_entry(hass: HomeAssistant) -> None:
    """A manual entry for the same URL also blocks discovery."""
    MockConfigEntry(domain=DOMAIN, unique_id=DISC_URL, data={"url": DISC_URL}).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_HASSIO}, data=DISCOVERY
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_flow(
    hass: HomeAssistant, mock_addon: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Reauth stores the new key."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"api_key": "new-key"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data["api_key"] == "new-key"


async def test_options_flow(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Interval is stored as int."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"scan_interval": 60.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {"scan_interval": 60}
