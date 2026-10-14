"""Fixtures for the technitium_dash integration tests."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.technitium_dash.const import DOMAIN

URL = "http://addon.local:8081"
API_KEY = "sekrit"

STATS_CLUSTER = {
    "totalQueries": 1263,
    "totalBlocked": 26,
    "totalCached": 1198,
    "totalNxDomain": 763,
    "totalServerFailure": 0,
    "totalClients": 14,
    "blockedPct": 2.1,
    "cachedPct": 94.9,
    "blockListZones": 2020734,
    "allowedZones": 0,
    "blockedZones": 0,
}
STATS_CYBERDYNE = {
    **STATS_CLUSTER,
    "totalQueries": 900,
    "totalBlocked": 20,
    "blockedPct": 2.2,
    "totalClients": 10,
}
STATS_UMBREL = {
    **STATS_CLUSTER,
    "totalQueries": 363,
    "totalBlocked": 6,
    "blockedPct": 1.7,
    "totalClients": 4,
}

SUMMARY: dict[str, Any] = {
    "generatedAt": "2026-10-14T01:00:00Z",
    "cacheSeconds": 30,
    "range": "LastHour",
    "cluster": STATS_CLUSTER,
    "nodes": [
        {
            "short": "cyberdyne",
            "name": "cyberdyne.dns.home.arpa",
            "role": "Primary",
            "state": "Self",
            "version": "15.6.1",
            "reachable": True,
            "blocking": {"enabled": True, "pausedUntil": None},
            "stats": STATS_CYBERDYNE,
        },
        {
            "short": "umbrel",
            "name": "umbrel.dns.home.arpa",
            "role": "Secondary",
            "state": "Connected",
            "version": "15.6.1",
            "reachable": True,
            "blocking": {"enabled": False, "pausedUntil": "2026-10-14T01:30:00Z"},
            "stats": STATS_UMBREL,
        },
    ],
    "drift": {
        "ok": True,
        "differences": 0,
        "checkedAt": "2026-10-14T00:58:00Z",
        "sections": {"settings": 0, "allowed": 0, "blocked": 0, "apps": 0},
    },
    "errors": [],
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ in every test."""
    return


@pytest.fixture
def summary() -> dict[str, Any]:
    """A fresh, mutable copy of the fixture summary."""
    return copy.deepcopy(SUMMARY)


@pytest.fixture
def mock_addon(aioclient_mock: AiohttpClientMocker, summary) -> AiohttpClientMocker:
    """An add-on answering health + summary."""
    aioclient_mock.get(f"{URL}/api/health", json={"ok": True, "tokenConfigured": True})
    aioclient_mock.get(f"{URL}/api/summary", json=summary)
    return aioclient_mock


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A configured entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Technitium Cluster",
        data={"url": URL, "api_key": API_KEY},
        unique_id=URL,
        entry_id="entry1",
    )
