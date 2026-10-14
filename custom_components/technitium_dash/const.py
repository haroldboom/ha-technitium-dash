"""Constants for the Technitium Cluster integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "technitium_dash"

CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_ADDON_SLUG: Final = "addon_slug"

DEFAULT_SCAN_INTERVAL: Final = 300
MIN_SCAN_INTERVAL: Final = 30

MANUFACTURER: Final = "Technitium"
NODE_MODEL: Final = "Technitium DNS Server"
CLUSTER_MODEL: Final = "Technitium DNS Cluster"
CLUSTER_NAME: Final = "Technitium Cluster"

# Suffix of the cluster device identifier: (DOMAIN, f"{entry_id}_{CLUSTER_KEY}").
CLUSTER_KEY: Final = "cluster"

# Service name -> add-on write path (POST tapi/<path>).
SERVICE_BLOCK_DOMAIN: Final = "block_domain"
SERVICE_UNBLOCK_DOMAIN: Final = "unblock_domain"
SERVICE_ALLOW_DOMAIN: Final = "allow_domain"
SERVICE_UNALLOW_DOMAIN: Final = "unallow_domain"

SERVICE_WRITE_PATHS: Final = {
    SERVICE_BLOCK_DOMAIN: "blocked/add",
    SERVICE_UNBLOCK_DOMAIN: "blocked/delete",
    SERVICE_ALLOW_DOMAIN: "allowed/add",
    SERVICE_UNALLOW_DOMAIN: "allowed/delete",
}

ATTR_DOMAIN: Final = "domain"
ATTR_NODE: Final = "node"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
