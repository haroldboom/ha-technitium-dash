"""DataUpdateCoordinator for the Technitium Cluster integration."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    TechnitiumDashAuthError,
    TechnitiumDashClient,
    TechnitiumDashConnectionError,
    TechnitiumDashError,
)
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

type TechnitiumDashConfigEntry = ConfigEntry[TechnitiumDashCoordinator]


def node_short(node: dict[str, Any]) -> str:
    """Return the short (first-label) node name used in identifiers."""
    short = node.get("short") or str(node.get("name") or "").split(".")[0]
    return str(short).lower()


class TechnitiumDashCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the add-on's api/summary."""

    config_entry: TechnitiumDashConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: TechnitiumDashConfigEntry,
        client: TechnitiumDashClient,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.client = client

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.client.summary()
        except TechnitiumDashAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except TechnitiumDashConnectionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

    # ----- helpers over the latest summary -----

    @property
    def nodes(self) -> dict[str, dict[str, Any]]:
        """Nodes of the latest summary, keyed by short name."""
        if not self.data:
            return {}
        return {
            node_short(n): n
            for n in self.data.get("nodes") or []
            if isinstance(n, dict) and (n.get("short") or n.get("name"))
        }

    def resolve_nodes(self, node: str | None) -> list[dict[str, Any]]:
        """Nodes to act on: all of them, or the one matching a short/full name.

        Returns [] when `node` is given but unknown.
        """
        nodes = self.nodes
        if not node:
            return list(nodes.values())
        wanted = node.strip().lower().rstrip(".")
        return [
            n for short, n in nodes.items() if wanted in (short, str(n.get("name") or "").lower())
        ]

    async def async_write_nodes(
        self, path: str, nodes: list[dict[str, Any]], payload: dict[str, Any]
    ) -> None:
        """POST tapi/<path> once per node; raise listing every node that failed."""

        async def _one(node: dict[str, Any]) -> str | None:
            name = node.get("name") or node_short(node)
            try:
                resp = await self.client.write(path, {**payload, "node": name})
            except TechnitiumDashAuthError:
                return f"{node_short(node)}: add-on rejected the API key"
            except TechnitiumDashError as err:
                return f"{node_short(node)}: {err}"
            if resp.get("status") == "ok":
                return None
            msg = resp.get("errorMessage") or resp.get("status") or "unknown error"
            return f"{node_short(node)}: {msg}"

        results = await asyncio.gather(*(_one(n) for n in nodes))
        failures = [r for r in results if r]
        if failures:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="node_write_failed",
                translation_placeholders={
                    "action": path,
                    "failed": str(len(failures)),
                    "total": str(len(nodes)),
                    "errors": "; ".join(failures),
                },
            )
