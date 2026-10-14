"""Minimal async client for the Technitium Dash add-on HTTP API.

The integration never talks to Technitium directly; everything goes through
the add-on, which holds the Technitium token and caches upstream calls.
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp

REQUEST_TIMEOUT = 30


class TechnitiumDashError(Exception):
    """Base error for the add-on client."""


class TechnitiumDashConnectionError(TechnitiumDashError):
    """The add-on could not be reached or answered garbage."""


class TechnitiumDashAuthError(TechnitiumDashError):
    """The add-on rejected the API key (HTTP 401)."""


def normalize_url(url: str) -> str:
    """Normalise an add-on URL: default scheme, lower-case host, no trailing slash."""
    url = url.strip()
    if "://" not in url:
        url = f"http://{url}"
    parts = urlsplit(url)
    if not parts.hostname:
        raise ValueError("missing host")
    netloc = parts.hostname.lower()
    if ":" in netloc:  # IPv6 literal
        netloc = f"[{netloc}]"
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), netloc, path, "", ""))


class TechnitiumDashClient:
    """Talks to the add-on (api/summary, api/health, api/drift, tapi/<write>)."""

    def __init__(
        self, session: aiohttp.ClientSession, url: str, api_key: str | None = None
    ) -> None:
        """Initialise the client."""
        self._session = session
        self.url = normalize_url(url)
        self._api_key = api_key or None

    def _headers(self, write: bool = False) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        if write:
            headers["X-Dash"] = "1"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        write = method == "POST"
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.request(
                    method,
                    f"{self.url}/{path}",
                    params=params,
                    json=json,
                    headers=self._headers(write),
                ) as resp:
                    if resp.status == 401:
                        raise TechnitiumDashAuthError("add-on rejected the API key")
                    try:
                        body = await resp.json(content_type=None)
                    except (aiohttp.ContentTypeError, ValueError) as err:
                        raise TechnitiumDashConnectionError(
                            f"{path}: HTTP {resp.status}, not JSON"
                        ) from err
                    # Writes: the add-on answers Technitium-style JSON even on a
                    # 4xx deny ({"status": "error", "errorMessage": ...}), so hand
                    # it back for the caller to report per node.
                    if resp.status >= 400 and not (write and isinstance(body, dict)):
                        raise TechnitiumDashConnectionError(f"{path}: HTTP {resp.status}")
                    return body
        except TimeoutError as err:
            raise TechnitiumDashConnectionError(f"{path}: timed out") from err
        except aiohttp.ClientError as err:
            raise TechnitiumDashConnectionError(f"{path}: {err}") from err

    async def health(self) -> dict[str, Any]:
        """GET api/health (always open, no auth)."""
        return await self._request("GET", "api/health")

    async def summary(self) -> dict[str, Any]:
        """GET api/summary."""
        data = await self._request("GET", "api/summary")
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
            raise TechnitiumDashConnectionError("api/summary: unexpected payload")
        return data

    async def force_drift(self) -> dict[str, Any]:
        """GET api/drift?force=1 (the add-on rate-limits this to once per 30 s)."""
        return await self._request("GET", "api/drift", params={"force": "1"})

    async def write(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST tapi/<path>; returns Technitium's JSON ({"status": "ok"|"error"})."""
        body = await self._request("POST", f"tapi/{path}", json=payload)
        if not isinstance(body, dict):
            return {"status": "error", "errorMessage": "unexpected response"}
        return body
