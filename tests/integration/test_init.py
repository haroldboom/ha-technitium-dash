"""Setup, entities, coordinator, services and buttons."""

from __future__ import annotations

import copy
from datetime import timedelta
import json

import aiohttp
import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.technitium_dash.const import DOMAIN
from custom_components.technitium_dash.services import normalize_domain

from .conftest import API_KEY, SUMMARY, URL

NODES = ("cyberdyne", "umbrel")
STAT_KEYS = (
    "queries",
    "blocked",
    "blocked_pct",
    "cached_pct",
    "nxdomain",
    "server_failure",
    "clients",
)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _eid(hass: HomeAssistant, platform: str, uid: str) -> str:
    eid = er.async_get(hass).async_get_entity_id(platform, DOMAIN, uid)
    assert eid, uid
    return eid


def _state(hass: HomeAssistant, platform: str, uid: str):
    return hass.states.get(_eid(hass, platform, uid))


def _posts(mock: AiohttpClientMocker) -> list[tuple[str, dict, dict]]:
    return [
        (str(url.path), json.loads(data) if isinstance(data, (str, bytes)) else data, headers)
        for method, url, data, headers in mock.mock_calls
        if method == "POST"
    ]


def _replace_summary(mock: AiohttpClientMocker, summary: dict) -> None:
    mock.clear_requests()
    mock.get(f"{URL}/api/summary", json=summary)


async def test_setup_entities_and_states(
    hass: HomeAssistant, mock_addon: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Every expected entity exists with node-name unique ids and fixture states."""
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert mock_addon.mock_calls[0][3]["Authorization"] == f"Bearer {API_KEY}"

    ent_reg = er.async_get(hass)
    uids = {e.unique_id for e in er.async_entries_for_config_entry(ent_reg, "entry1")}
    expected = {
        f"entry1_cluster_{k}"
        for k in (
            *STAT_KEYS, "drift_differences", "nodes_in_sync", "flush_cache", "recheck_drift",
            "blocking", "pause_length",
        )
    }
    for n in NODES:
        expected |= {
            f"entry1_{n}_{k}"
            for k in (*STAT_KEYS, "version", "connectivity", "blocking", "flush_cache")
        }
    assert uids == expected

    assert _state(hass, "sensor", "entry1_cluster_queries").state == "1263"
    assert _state(hass, "sensor", "entry1_cluster_blocked_pct").state == "2.1"
    assert (
        _state(hass, "sensor", "entry1_cluster_blocked_pct").attributes["unit_of_measurement"]
        == "%"
    )
    assert _state(hass, "sensor", "entry1_cluster_cached_pct").state == "94.9"
    assert _state(hass, "sensor", "entry1_cluster_nxdomain").state == "763"
    assert _state(hass, "sensor", "entry1_cluster_server_failure").state == "0"
    assert _state(hass, "sensor", "entry1_cluster_clients").state == "14"
    drift = _state(hass, "sensor", "entry1_cluster_drift_differences")
    assert drift.state == "0"
    assert drift.attributes["settings_differences"] == 0
    assert _state(hass, "sensor", "entry1_cyberdyne_queries").state == "900"
    assert _state(hass, "sensor", "entry1_umbrel_blocked").state == "6"
    assert _state(hass, "sensor", "entry1_umbrel_version").state == "15.6.1"

    assert _state(hass, "binary_sensor", "entry1_cyberdyne_connectivity").state == STATE_ON
    assert _state(hass, "binary_sensor", "entry1_cyberdyne_blocking").state == STATE_ON
    umbrel_blocking = _state(hass, "binary_sensor", "entry1_umbrel_blocking")
    assert umbrel_blocking.state == STATE_OFF
    assert umbrel_blocking.attributes["paused_until"] == "2026-10-14T01:30:00Z"
    in_sync = _state(hass, "binary_sensor", "entry1_cluster_nodes_in_sync")
    assert in_sync.state == STATE_OFF  # problem class: off = in sync
    assert in_sync.attributes["device_class"] == "problem"

    # Entity ids come from has_entity_name + device name + translation.
    assert _eid(hass, "sensor", "entry1_cyberdyne_queries") == "sensor.cyberdyne_queries"
    assert _eid(hass, "sensor", "entry1_cluster_queries") == "sensor.technitium_cluster_queries"
    assert _eid(hass, "binary_sensor", "entry1_cluster_nodes_in_sync") == (
        "binary_sensor.technitium_cluster_nodes_in_sync"
    )
    assert _eid(hass, "button", "entry1_cyberdyne_flush_cache") == "button.cyberdyne_flush_cache"
    assert (
        ent_reg.async_get(_eid(hass, "sensor", "entry1_umbrel_version")).entity_category
        == "diagnostic"
    )


async def test_devices(
    hass: HomeAssistant, mock_addon: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A cluster device, and one device per node hanging off it."""
    await _setup(hass, config_entry)
    dev_reg = dr.async_get(hass)
    cluster = dev_reg.async_get_device(identifiers={(DOMAIN, "entry1_cluster")})
    assert cluster and cluster.name == "Technitium Cluster"
    for n in NODES:
        dev = dev_reg.async_get_device(identifiers={(DOMAIN, f"entry1_{n}")})
        assert dev.name == n
        assert dev.model == "Technitium DNS Server"
        assert dev.sw_version == "15.6.1"
        assert dev.via_device_id == cluster.id


async def test_setup_retry_when_addon_down(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Unreachable add-on -> SETUP_RETRY, not a crash."""
    aioclient_mock.get(f"{URL}/api/summary", exc=aiohttp.ClientConnectionError())
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_auth_failure_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """401 -> SETUP_ERROR plus a reauth flow."""
    aioclient_mock.get(f"{URL}/api/summary", status=401, json={"status": "error"})
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_node_unreachable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, summary, config_entry
) -> None:
    """A dead node: connectivity off, its stats/blocking unavailable, rest unaffected."""
    umbrel = summary["nodes"][1]
    umbrel.update(reachable=False, stats=None, blocking=None)
    summary["errors"] = [{"node": "umbrel", "error": "timed out"}]
    aioclient_mock.get(f"{URL}/api/summary", json=summary)
    await _setup(hass, config_entry)

    assert _state(hass, "binary_sensor", "entry1_umbrel_connectivity").state == STATE_OFF
    assert _state(hass, "sensor", "entry1_umbrel_queries").state == STATE_UNAVAILABLE
    assert _state(hass, "binary_sensor", "entry1_umbrel_blocking").state == STATE_UNAVAILABLE
    assert _state(hass, "sensor", "entry1_umbrel_version").state == "15.6.1"
    assert _state(hass, "sensor", "entry1_cyberdyne_queries").state == "900"
    assert _state(hass, "binary_sensor", "entry1_cyberdyne_connectivity").state == STATE_ON


async def test_drift_and_poll_update(
    hass: HomeAssistant, mock_addon: AiohttpClientMocker, summary, config_entry
) -> None:
    """A later poll with drift flips the problem sensor; add-on outage -> unavailable."""
    await _setup(hass, config_entry)
    drifted = copy.deepcopy(summary)
    drifted["drift"] = {
        "ok": False,
        "differences": 3,
        "checkedAt": "x",
        "sections": {"settings": 1, "allowed": 2, "blocked": 0, "apps": 0},
    }
    _replace_summary(mock_addon, drifted)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=301))
    await hass.async_block_till_done()
    assert _state(hass, "binary_sensor", "entry1_cluster_nodes_in_sync").state == STATE_ON
    assert _state(hass, "sensor", "entry1_cluster_drift_differences").state == "3"

    drifted["drift"] = {"ok": None, "differences": None}
    _replace_summary(mock_addon, drifted)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=602))
    await hass.async_block_till_done()
    assert _state(hass, "binary_sensor", "entry1_cluster_nodes_in_sync").state == STATE_UNKNOWN

    mock_addon.clear_requests()
    mock_addon.get(f"{URL}/api/summary", exc=aiohttp.ClientConnectionError())
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=903))
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "entry1_cluster_queries").state == STATE_UNAVAILABLE


async def test_nodes_appear_and_disappear(
    hass: HomeAssistant, mock_addon: AiohttpClientMocker, summary, config_entry
) -> None:
    """New node -> new entities/device; vanished node -> unavailable, removable."""
    await _setup(hass, config_entry)
    grown = copy.deepcopy(summary)
    grown["nodes"].append(
        {
            "short": "skynet",
            "name": "skynet.dns.home.arpa",
            "role": "Secondary",
            "state": "Connected",
            "version": "15.7.0",
            "reachable": True,
            "blocking": {"enabled": True, "pausedUntil": None},
            "stats": SUMMARY["nodes"][0]["stats"],
        }
    )
    _replace_summary(mock_addon, grown)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=301))
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "entry1_skynet_queries").state == "900"
    assert _state(hass, "button", "entry1_skynet_flush_cache") is not None
    dev_reg = dr.async_get(hass)
    skynet = dev_reg.async_get_device(identifiers={(DOMAIN, "entry1_skynet")})
    assert skynet.sw_version == "15.7.0"
    # existing entities keep their ids
    assert _eid(hass, "sensor", "entry1_cyberdyne_queries") == "sensor.cyberdyne_queries"

    shrunk = copy.deepcopy(summary)
    shrunk["nodes"] = [n for n in shrunk["nodes"] if n["short"] != "umbrel"]
    _replace_summary(mock_addon, shrunk)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=602))
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "entry1_umbrel_queries").state == STATE_UNAVAILABLE
    assert _state(hass, "binary_sensor", "entry1_umbrel_connectivity").state == STATE_UNAVAILABLE
    assert _state(hass, "sensor", "entry1_cyberdyne_queries").state == "900"

    from custom_components.technitium_dash import async_remove_config_entry_device

    umbrel = dev_reg.async_get_device(identifiers={(DOMAIN, "entry1_umbrel")})
    cyber = dev_reg.async_get_device(identifiers={(DOMAIN, "entry1_cyberdyne")})
    cluster = dev_reg.async_get_device(identifiers={(DOMAIN, "entry1_cluster")})
    assert await async_remove_config_entry_device(hass, config_entry, umbrel)
    assert not await async_remove_config_entry_device(hass, config_entry, cyber)
    assert not await async_remove_config_entry_device(hass, config_entry, cluster)


async def test_version_update_syncs_device(
    hass: HomeAssistant, mock_addon: AiohttpClientMocker, summary, config_entry
) -> None:
    """sw_version follows the summary after an upgrade."""
    await _setup(hass, config_entry)
    upgraded = copy.deepcopy(summary)
    upgraded["nodes"][0]["version"] = "15.7.0"
    _replace_summary(mock_addon, upgraded)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=301))
    await hass.async_block_till_done()
    dev = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "entry1_cyberdyne")})
    assert dev.sw_version == "15.7.0"


async def test_options_change_interval(
    hass: HomeAssistant, mock_addon: AiohttpClientMocker, config_entry
) -> None:
    """Options set the coordinator interval (after reload)."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, options={"scan_interval": 45})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.runtime_data.update_interval == timedelta(seconds=45)


async def test_unload(hass: HomeAssistant, mock_addon, config_entry) -> None:
    """Unload works; services stay registered (they live in async_setup)."""
    await _setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert hass.services.has_service(DOMAIN, "block_domain")
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "block_domain", {"domain": "x.com"}, blocking=True)
    assert err.value.translation_key == "entry_not_loaded"


# ---------------- services ----------------


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("ads.example.com", "ads.example.com"),
        ("  ADS.Example.COM. ", "ads.example.com"),
        ("https://ads.example.com/path?q=1#x", "ads.example.com"),
        ("http://user@ads.example.com:8080/", "ads.example.com"),
        ("*.tracker.net", "tracker.net"),
        ("bücher.de", "xn--bcher-kva.de"),
        ("_dmarc.example.com", "_dmarc.example.com"),
    ],
)
def test_normalize_domain(raw: str, clean: str) -> None:
    """Loose cleaning."""
    assert normalize_domain(raw) == clean


@pytest.mark.parametrize("raw", ["", "  ", "https://", "foo bar.com", "-bad-.com", "a..b", "[::1]"])
def test_normalize_domain_rejects(raw: str) -> None:
    """Junk is rejected."""
    with pytest.raises(ValueError):
        normalize_domain(raw)


@pytest.mark.parametrize(
    ("service", "path"),
    [
        ("block_domain", "/tapi/blocked/add"),
        ("unblock_domain", "/tapi/blocked/delete"),
        ("allow_domain", "/tapi/allowed/add"),
        ("unallow_domain", "/tapi/allowed/delete"),
    ],
)
async def test_service_all_nodes(
    hass: HomeAssistant, mock_addon: AiohttpClientMocker, config_entry, service, path
) -> None:
    """Default target is every node; one POST each, with X-Dash and bearer."""
    await _setup(hass, config_entry)
    mock_addon.post(f"{URL}{path}", json={"status": "ok"})
    await hass.services.async_call(
        DOMAIN, service, {"domain": "HTTPS://Ads.Example.com/x"}, blocking=True
    )
    posts = _posts(mock_addon)
    assert sorted(p[1]["node"] for p in posts) == [
        "cyberdyne.dns.home.arpa",
        "umbrel.dns.home.arpa",
    ]
    for p_path, body, headers in posts:
        assert p_path == path
        assert body["domain"] == "ads.example.com"
        assert set(body) == {"domain", "node"}
        assert headers["X-Dash"] == "1"
        assert headers["Authorization"] == f"Bearer {API_KEY}"


@pytest.mark.parametrize("node", ["umbrel", "UMBREL.dns.home.arpa"])
async def test_service_one_node(
    hass: HomeAssistant, mock_addon: AiohttpClientMocker, config_entry, node
) -> None:
    """node= restricts to one node (short or full name)."""
    await _setup(hass, config_entry)
    mock_addon.post(f"{URL}/tapi/blocked/add", json={"status": "ok"})
    await hass.services.async_call(
        DOMAIN, "block_domain", {"domain": "ads.example.com", "node": node}, blocking=True
    )
    posts = _posts(mock_addon)
    assert [p[1] for p in posts] == [{"domain": "ads.example.com", "node": "umbrel.dns.home.arpa"}]


async def test_service_node_failure_raises(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, summary, config_entry
) -> None:
    """One node's Technitium error is surfaced in the HomeAssistantError."""
    aioclient_mock.get(f"{URL}/api/summary", json=summary)
    await _setup(hass, config_entry)

    calls = []

    async def _post(method, url, data):
        calls.append(data["node"])
        if data["node"].startswith("umbrel"):
            from pytest_homeassistant_custom_component.test_util.aiohttp import (
                AiohttpClientMockResponse,
            )

            return AiohttpClientMockResponse(
                method, url, json={"status": "error", "errorMessage": "Zone already exists"}
            )
        from pytest_homeassistant_custom_component.test_util.aiohttp import (
            AiohttpClientMockResponse,
        )

        return AiohttpClientMockResponse(method, url, json={"status": "ok"})

    aioclient_mock.post(f"{URL}/tapi/allowed/add", side_effect=_post)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN, "allow_domain", {"domain": "good.example.com"}, blocking=True
        )
    assert sorted(calls) == ["cyberdyne.dns.home.arpa", "umbrel.dns.home.arpa"]
    assert err.value.translation_key == "node_write_failed"
    msg = str(err.value)
    assert "umbrel: Zone already exists" in msg
    assert "1 of 2" in msg
    assert "cyberdyne" not in msg


async def test_service_transport_and_deny_failures(
    hass: HomeAssistant, mock_addon: AiohttpClientMocker, config_entry
) -> None:
    """403 deny body and a connection error both count as node failures."""
    await _setup(hass, config_entry)
    mock_addon.post(
        f"{URL}/tapi/blocked/delete",
        status=403,
        json={"status": "error", "errorMessage": "param not allowed: x"},
    )
    with pytest.raises(HomeAssistantError, match="cyberdyne: param not allowed: x"):
        await hass.services.async_call(
            DOMAIN, "unblock_domain", {"domain": "x.com", "node": "cyberdyne"}, blocking=True
        )
    mock_addon.post(f"{URL}/tapi/blocked/add", exc=aiohttp.ClientConnectionError("down"))
    with pytest.raises(HomeAssistantError, match="2 of 2"):
        await hass.services.async_call(DOMAIN, "block_domain", {"domain": "x.com"}, blocking=True)


@pytest.mark.parametrize("minutes", [5, "15", 35, 60])
async def test_pause_blocking(
    hass: HomeAssistant, mock_addon: AiohttpClientMocker, config_entry, minutes
) -> None:
    """One POST to the primary (no node): Technitium relays the pause to every node."""
    await _setup(hass, config_entry)
    mock_addon.post(f"{URL}/tapi/settings/temporaryDisableBlocking", json={"status": "ok"})
    await hass.services.async_call(DOMAIN, "pause_blocking", {"minutes": minutes}, blocking=True)
    posts = _posts(mock_addon)
    assert [(p[0], p[1]) for p in posts] == [
        ("/tapi/settings/temporaryDisableBlocking", {"minutes": int(minutes)})
    ]
    assert posts[0][2]["X-Dash"] == "1"


@pytest.mark.parametrize("minutes", [0, 12, 65, 1440, "x"])
async def test_pause_blocking_bad_minutes(
    hass: HomeAssistant, mock_addon: AiohttpClientMocker, config_entry, minutes
) -> None:
    """Only the fixed pause lengths are accepted."""
    await _setup(hass, config_entry)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, "pause_blocking", {"minutes": minutes}, blocking=True)
    assert _posts(mock_addon) == []


async def test_resume_blocking(
    hass: HomeAssistant, mock_addon: AiohttpClientMocker, config_entry
) -> None:
    """Resume only ever sends enableBlocking=true."""
    await _setup(hass, config_entry)
    mock_addon.post(f"{URL}/tapi/settings/set", json={"status": "ok"})
    await hass.services.async_call(DOMAIN, "resume_blocking", {}, blocking=True)
    assert [(p[0], p[1]) for p in _posts(mock_addon)] == [
        ("/tapi/settings/set", {"enableBlocking": True})
    ]


async def test_pause_blocking_denied(
    hass: HomeAssistant, mock_addon: AiohttpClientMocker, config_entry
) -> None:
    """Technitium's permission error reaches the user."""
    await _setup(hass, config_entry)
    mock_addon.post(
        f"{URL}/tapi/settings/temporaryDisableBlocking",
        json={"status": "error", "errorMessage": "Access was denied."},
    )
    with pytest.raises(HomeAssistantError, match="Access was denied") as err:
        await hass.services.async_call(DOMAIN, "pause_blocking", {"minutes": 15}, blocking=True)
    assert err.value.translation_key == "cluster_write_failed"


async def test_blocking_switch_state(
    hass: HomeAssistant, mock_addon: AiohttpClientMocker, summary, config_entry
) -> None:
    """Off while any node is paused; flips back on once the pause has ended."""
    await _setup(hass, config_entry)
    eid = _eid(hass, "switch", "entry1_cluster_blocking")
    state = hass.states.get(eid)
    assert state.state == STATE_OFF  # umbrel is paused in the fixture
    assert state.attributes["paused_until"] == "2026-10-14T01:30:00Z"

    resumed = copy.deepcopy(summary)
    for node in resumed["nodes"]:
        node["blocking"] = {"enabled": True, "pausedUntil": None}
    _replace_summary(mock_addon, resumed)
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get(eid)
    assert state.state == STATE_ON
    assert state.attributes["paused_until"] is None


@pytest.mark.parametrize(("slider", "minutes"), [(None, 15), (45, 45)])
async def test_blocking_switch_off_pauses(
    hass: HomeAssistant, mock_addon: AiohttpClientMocker, config_entry, slider, minutes
) -> None:
    """Turning the switch off is a timed pause (slider length), never a permanent off."""
    await _setup(hass, config_entry)
    if slider is not None:
        await hass.services.async_call(
            "number", "set_value",
            {"entity_id": _eid(hass, "number", "entry1_cluster_pause_length"), "value": slider},
            blocking=True,
        )
    assert hass.states.get(_eid(hass, "number", "entry1_cluster_pause_length")).state == f"{minutes}"
    mock_addon.post(f"{URL}/tapi/settings/temporaryDisableBlocking", json={"status": "ok"})
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": _eid(hass, "switch", "entry1_cluster_blocking")},
        blocking=True,
    )
    assert [(p[0], p[1]) for p in _posts(mock_addon)] == [
        ("/tapi/settings/temporaryDisableBlocking", {"minutes": minutes})
    ]


async def test_blocking_switch_on_resumes(
    hass: HomeAssistant, mock_addon: AiohttpClientMocker, config_entry
) -> None:
    """Turning the switch on ends the pause."""
    await _setup(hass, config_entry)
    mock_addon.post(f"{URL}/tapi/settings/set", json={"status": "ok"})
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": _eid(hass, "switch", "entry1_cluster_blocking")},
        blocking=True,
    )
    assert [(p[0], p[1]) for p in _posts(mock_addon)] == [
        ("/tapi/settings/set", {"enableBlocking": True})
    ]


async def test_service_validation(
    hass: HomeAssistant, mock_addon: AiohttpClientMocker, config_entry
) -> None:
    """Bad domain / unknown node / unknown entry: ServiceValidationError, no POST."""
    await _setup(hass, config_entry)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "block_domain", {"domain": "not a domain"}, blocking=True
        )
    assert err.value.translation_key == "invalid_domain"
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "block_domain", {"domain": "x.com", "node": "skynet"}, blocking=True
        )
    assert err.value.translation_key == "unknown_node"
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "block_domain", {"domain": "x.com", "config_entry_id": "nope"}, blocking=True
        )
    assert err.value.translation_key == "entry_not_found"
    assert _posts(mock_addon) == []


async def test_service_requires_entry_when_several(
    hass: HomeAssistant, mock_addon: AiohttpClientMocker, config_entry
) -> None:
    """With two clusters, config_entry_id picks one."""
    await _setup(hass, config_entry)
    other = MockConfigEntry(domain=DOMAIN, data={"url": URL}, unique_id="other", entry_id="entry2")
    await _setup(hass, other)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "block_domain", {"domain": "x.com"}, blocking=True)
    assert err.value.translation_key == "entry_required"
    mock_addon.post(f"{URL}/tapi/blocked/add", json={"status": "ok"})
    await hass.services.async_call(
        DOMAIN,
        "block_domain",
        {"domain": "x.com", "config_entry_id": "entry2", "node": "cyberdyne"},
        blocking=True,
    )
    assert len(_posts(mock_addon)) == 1


# ---------------- buttons ----------------


async def _press(hass: HomeAssistant, uid: str) -> None:
    await hass.services.async_call(
        "button", "press", {"entity_id": _eid(hass, "button", uid)}, blocking=True
    )


async def test_button_flush_node(hass: HomeAssistant, mock_addon, config_entry) -> None:
    """Per-node flush posts once, for that node only."""
    await _setup(hass, config_entry)
    mock_addon.post(f"{URL}/tapi/cache/flush", json={"status": "ok"})
    await _press(hass, "entry1_umbrel_flush_cache")
    posts = _posts(mock_addon)
    assert [(p[0], p[1]) for p in posts] == [
        ("/tapi/cache/flush", {"node": "umbrel.dns.home.arpa"})
    ]
    assert posts[0][2]["X-Dash"] == "1"


async def test_button_flush_all(hass: HomeAssistant, mock_addon, config_entry) -> None:
    """Cluster flush posts once per node."""
    await _setup(hass, config_entry)
    mock_addon.post(f"{URL}/tapi/cache/flush", json={"status": "ok"})
    await _press(hass, "entry1_cluster_flush_cache")
    assert sorted(p[1]["node"] for p in _posts(mock_addon)) == [
        "cyberdyne.dns.home.arpa",
        "umbrel.dns.home.arpa",
    ]


async def test_button_flush_failure(hass: HomeAssistant, mock_addon, config_entry) -> None:
    """A failed flush raises."""
    await _setup(hass, config_entry)
    mock_addon.post(f"{URL}/tapi/cache/flush", json={"status": "error", "errorMessage": "nope"})
    with pytest.raises(HomeAssistantError, match="cyberdyne: nope"):
        await _press(hass, "entry1_cyberdyne_flush_cache")


async def test_button_recheck_drift(
    hass: HomeAssistant, mock_addon: AiohttpClientMocker, config_entry
) -> None:
    """Recheck = GET api/drift?force=1, then a summary refresh."""
    await _setup(hass, config_entry)
    mock_addon.get(f"{URL}/api/drift", json={"ok": True, "sections": []})
    before = mock_addon.call_count
    await _press(hass, "entry1_cluster_recheck_drift")
    await hass.async_block_till_done()
    new = [(m, str(u.path), dict(u.query)) for m, u, _, _ in mock_addon.mock_calls[before:]]
    assert new[0] == ("GET", "/api/drift", {"force": "1"})
    assert ("GET", "/api/summary", {}) in new[1:]


async def test_button_recheck_drift_failure(
    hass: HomeAssistant, mock_addon: AiohttpClientMocker, config_entry
) -> None:
    """Drift endpoint down -> HomeAssistantError."""
    await _setup(hass, config_entry)
    mock_addon.get(f"{URL}/api/drift", exc=aiohttp.ClientConnectionError())
    with pytest.raises(HomeAssistantError) as err:
        await _press(hass, "entry1_cluster_recheck_drift")
    assert err.value.translation_key == "drift_failed"


async def test_pause_length_slider_bounds(
    hass: HomeAssistant, mock_addon: AiohttpClientMocker, config_entry
) -> None:
    """The slider stays within what the add-on accepts."""
    await _setup(hass, config_entry)
    eid = _eid(hass, "number", "entry1_cluster_pause_length")
    attrs = hass.states.get(eid).attributes
    assert (attrs["min"], attrs["max"], attrs["step"], attrs["mode"]) == (5, 60, 5, "slider")
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call("number", "set_value", {"entity_id": eid, "value": 90}, blocking=True)
