# ha-technitium-dash: build contract (v2: Home Assistant add-on + integration)

One GitHub repo that is BOTH a Home Assistant add-on repository AND a HACS
integration repository.

```
repository.yaml                         add-on repo metadata            (orchestrator)
technitium_dash/                        the add-on                      (orchestrator)
  config.yaml  build.yaml  Dockerfile  run.sh  DOCS.md  CHANGELOG.md
  app/server.py  app/drift.py                                           (orchestrator)
  app/static/index.html                                                 (frontend agent)
custom_components/technitium_dash/      the integration                 (integration agent)
hacs.json  README.md  .github/workflows/                                (orchestrator)
tests/test_drift.py  tests/test_server.py                               (orchestrator)
tests/integration/                                                      (integration agent)
```

The cluster: Technitium DNS 15.6.1, primary `cyberdyne.dns.home.arpa`
(192.168.1.198:5380), secondary `umbrel.dns.home.arpa` (192.168.1.150). The add-on
holds the Technitium token; **nothing else ever talks to Technitium**. The
integration talks ONLY to the add-on.

## The add-on (server)
- Python stdlib server (`app/server.py`), port **8081** inside the container.
- **Ingress**: the HA sidebar panel. The page is served under a path prefix
  (`/api/hassio_ingress/<token>/`), so **the frontend must use relative URLs**
  (`api/nodes`, `tapi/...`, never `/api/nodes`).
- Options (from `/data/options.json`): `technitium_url`, `technitium_token`,
  `api_key` (optional), `cache_seconds` (default 30).
- **Caching (don't hammer Technitium)**: every GET to Technitium is cached
  server-side for `cache_seconds`, keyed by path and params. So any number of
  browsers plus the integration cost one upstream call per key per window. Drift is
  cached for max(300, cache_seconds); `GET api/drift?force=1` recomputes, but at
  most once every 30 s.
- **Auth**: requests from the ingress proxy (`172.30.32.2`) are trusted. Every
  other request needs `Authorization: Bearer <api_key>` when `api_key` is set.
  `GET api/health` is always open.
- Writes need header `X-Dash: 1`, as before.

## Browser/integration → add-on API (all paths relative to the add-on root)
Everything from v1 still applies: `api/nodes`, `api/blocking`, `api/drift`,
`api/health`, `GET tapi/<read path>`, and `POST tapi/<write path>` (allowed/add|delete,
blocked/add|delete, cache/flush, cache/delete; **no settings writes**).

### NEW `GET api/summary`: one call with everything the integration needs
```json
{
  "generatedAt": "2026-10-14T01:00:00Z",
  "cacheSeconds": 30,
  "range": "LastHour",
  "cluster": {"totalQueries": 1263, "totalBlocked": 26, "totalCached": 1198,
              "totalNxDomain": 763, "totalServerFailure": 0, "totalClients": 14,
              "blockedPct": 2.1, "cachedPct": 94.9,
              "blockListZones": 2020734, "allowedZones": 0, "blockedZones": 0},
  "nodes": [
    {"short": "cyberdyne", "name": "cyberdyne.dns.home.arpa", "role": "Primary",
     "state": "Self", "version": "15.6.1", "reachable": true,
     "blocking": {"enabled": true, "pausedUntil": null},
     "stats": { same keys as cluster } }
  ],
  "drift": {"ok": true, "differences": 0, "checkedAt": "2026-10-14T00:58:00Z",
            "sections": {"settings": 0, "allowed": 0, "blocked": 0, "apps": 0}},
  "errors": []
}
```
- `stats` is `null` and `reachable` is false when a node fails. Percentages are
  0 to 100, with one decimal.
- `drift` comes from the drift cache and never forces a recompute.
- `?range=LastDay` etc. is optional.

### NEW `GET api/config`
`{"cacheSeconds": 30, "version": "<add-on version>", "ingress": true|false}`. The
frontend uses `cacheSeconds` as the floor for its refresh picker.

## Frontend (index.html) changes
1. **Relative URLs everywhere** (ingress).
2. **Refresh-interval picker in the header**: Off (manual), 30 s, 1 min, 5 min,
   15 min, 30 min, 1 h. Default 1 min. Saved in localStorage (try/catch). The
   floor is `cacheSeconds` from `api/config`; hide options below it. Show
   "Updated 2 min ago" plus a manual refresh button. Every periodic poll
   (overview, header drift/blocking badges, the log tab if it auto-refreshes) obeys
   this one interval; nothing else polls on its own timer. Pause while the tab is
   hidden; refresh once on return if the interval has passed. "Check again" on
   drift calls `api/drift?force=1`.
3. Must still work inside an iframe (HA ingress) and on a phone.

## Integration (custom_components/technitium_dash)
- Domain `technitium_dash`, name "Technitium Cluster". Config flow: add-on URL plus
  optional API key. Validate with `api/health`, then `api/summary`. Also implement
  `async_step_hassio` (Supervisor discovery: `HassioServiceInfo.config` has
  `host`, `port`, `api_key`) with a confirm step.
- An options flow for the update interval (default 300 s, minimum 30 s).
- A single `DataUpdateCoordinator` polling `api/summary`.
- Entities (`has_entity_name`). A device per node, plus one "Cluster" device:
  - Sensors (cluster and per node): queries, blocked, blocked %, cached %,
    NXDOMAIN, server failures, clients. Per node also: version (diagnostic). Cluster
    also: drift differences.
  - Binary sensors: per node `connectivity` (reachable), per node "Blocking"
    (on/off; attribute `paused_until`); cluster "Nodes in sync" (device_class
    `problem`, inverted, so on = drift).
  - Buttons: "Flush cache" per node and cluster-wide (every node), and "Recheck
    drift" (`api/drift?force=1`, then refresh).
  - Services: `technitium_dash.block_domain`, `allow_domain`, `unblock_domain`,
    `unallow_domain` (fields `domain`, optional `node`; default all nodes).
- `aiohttp` through HA's shared session, `Authorization: Bearer` when an api_key is
  set, and `X-Dash: 1` on POSTs.
- `manifest.json`: `iot_class: local_polling`, `config_flow: true`, a version,
  `codeowners`, a documentation URL placeholder, and `hassio` in `after_dependencies`.
- Tests: pytest with `pytest-homeassistant-custom-component`, covering the config
  flow, coordinator and entities against a mocked add-on.
