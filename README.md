# Technitium Cluster for Home Assistant

A Home Assistant **add-on** and a matching **integration** for a
[Technitium DNS Server](https://technitium.com/dns/) cluster.

- **Add-on: Technitium Cluster Dashboard.** A sidebar panel that shows every node
  side by side: queries, blocked, cache, top clients and domains, a query log, and
  one-tap block/allow on all nodes. It also checks whether the nodes have
  **drifted apart** (settings, allowed and blocked zones, apps), which the stock
  console can't. It only holds a limited Technitium token, and it caches every
  upstream call, so any number of viewers costs the DNS server the same.
- **Integration: Technitium Cluster.** Sensors, binary sensors, buttons and
  block/allow actions for automations. It talks **only to the add-on**, never to
  Technitium.

Settings stay in the Technitium web console. The one exception is pausing blocking
for a few minutes, which needs an extra permission (see below).

## Install

1. **Add-on:** Settings → Add-ons → Add-on store → ⋮ → Repositories → add
   `https://github.com/haroldboom/ha-technitium-dash`, then install **Technitium
   Cluster Dashboard**.
2. **Integration:** HACS → ⋮ → Custom repositories → the same URL, category
   *Integration*. Install **Technitium Cluster**, restart Home Assistant, and accept
   it under Settings → Devices & services → *Discovered*.

## 1. Create a limited Technitium user and token

Give the add-on its own user. Don't use `admin`. Do this on the **primary** node's
web console.

1. **Administration → Users → Add User**: username `dashboard`, any long password.
2. **Administration → Permissions**: on each section below, add `dashboard` as a
   *user* permission and tick the boxes shown. Leave the existing `Everyone` and
   `Administrators` rows alone, and don't add the user to `Administrators`.

   | Section        | View | Modify | Delete | Used for |
   |----------------|:----:|:------:|:------:|----------|
   | Dashboard      | ✅ |    |    | stats, top clients/domains |
   | Logs           | ✅ |    |    | the query log |
   | Apps           | ✅ |    |    | finding the Query Logs app, app drift |
   | Cache          | ✅ | ✅ | ✅ | flush cache, clear one domain |
   | Allowed        | ✅ | ✅ | ✅ | allow / un-allow a domain |
   | Blocked        | ✅ | ✅ | ✅ | block / un-block a domain |
   | Settings       | ✅ | ✅* |    | blocking status, settings drift; *Modify only to pause blocking |
   | Administration | ✅ |    |    | cluster nodes and their state |
   | Zones, DnsClient, DhcpServer | | | | not needed |

   Settings **Modify** is optional. It is only needed to **pause blocking** from the
   dashboard or the integration. Technitium has no finer permission, so the token could
   then change any setting. The add-on itself only lets two settings calls through: a
   pause of 5 to 60 minutes, and "resume" (blocking back on). It refuses
   everything else, including turning blocking off. Leave Modify off if you don't want
   pausing.
3. **Administration → Sessions → Create Token**: user `dashboard`, any token name.
   Copy the token into this add-on's `technitium_token` option.

### Clusters: get the user onto the secondary nodes

Technitium 15.6.1 has a bug
([TechnitiumSoftware/DnsServer#2224](https://github.com/TechnitiumSoftware/DnsServer/issues/2224)):
the primary only writes new users and permissions to disk when it shuts down, so
secondaries never receive them, even after a resync. The symptom is
`No such user exists: dashboard` for every secondary node.

Workaround, once, after creating the user and permissions:

1. Restart the **primary** cleanly (`docker restart -t 60 <container>`, or a normal
   service restart) so it saves `auth.config`.
2. On each **secondary**: **Administration → Cluster → Resync**.

You can't create the user on a secondary directly; the secondary forwards the request
to the primary, which answers "User already exists". This step won't be needed once
#2224 is fixed.


## Integration entities

| Entity | Cluster | Per node |
|---|:-:|:-:|
| Queries, blocked, blocked %, cached %, NXDOMAIN, server failures, clients (last hour) | ✅ | ✅ |
| Drift differences (attributes: per section) | ✅ | |
| Version (diagnostic) | | ✅ |
| Nodes in sync (`problem`: on = drift) | ✅ | |
| Connectivity, Blocking (attribute `paused_until`) | | ✅ |
| Button: Flush cache | ✅ (all nodes) | ✅ |
| Button: Recheck drift | ✅ | |
| Switch: Blocking (off = pause, back on by itself when the pause ends) | ✅ | |
| Number: Blocking pause length (slider, 5–60 min) | ✅ | |

Actions: `technitium_dash.block_domain`, `unblock_domain`, `allow_domain`,
`unallow_domain` take `domain` and an optional `node`. By default they apply to
every node.

The **Blocking** switch pauses blocking on every node when you turn it off, for as
long as the **Blocking pause length** slider says (5 to 60 minutes, default 15). When
the pause ends, Technitium turns blocking back on and the switch follows. Turning the
switch on ends the pause early. It can't turn blocking off for good.

`technitium_dash.pause_blocking` (`minutes`: 5 to 60, in steps of 5) pauses blocking on
every node, and `resume_blocking` ends a pause early. In a cluster Technitium applies both to
all nodes, so neither takes a `node`. They need Settings **Modify** (see above).

The update interval is under the integration's options (default 5 min, minimum
30 s). The dashboard page has its own refresh picker (Off to 1 h). Both are
floored by the add-on's `cache_seconds`.

## Development

```
python3 -m unittest tests/test_drift.py tests/test_server.py   # add-on server, stdlib only
uv venv -p 3.13 .venv && uv pip install -p .venv -r requirements_test.txt
.venv/bin/pytest tests/integration -q                          # integration
```

The add-on server also runs standalone: `TECHNITIUM_TOKEN=… python3 technitium_dash/app/server.py`
serves on :8081.

## Credits

This is an unofficial, community project and isn't made by Technitium. The icon is the
Technitium logo, redrawn from `DnsServerCore/www/img/logo.png` in
[TechnitiumSoftware/DnsServer](https://github.com/TechnitiumSoftware/DnsServer) (GPL-3.0).
It's used only to show which server this connects to. The MIT license covers this
project's code, not the logo.
