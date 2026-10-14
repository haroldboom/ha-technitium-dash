# Technitium Cluster Dashboard

A daily-use dashboard for a [Technitium DNS Server](https://technitium.com/dns/)
cluster. It shows every node side by side and checks whether the nodes have
drifted apart. It sits in the Home Assistant sidebar, and the companion
**Technitium Cluster** integration gets its sensors from it.

Settings still live in the Technitium web console. This add-on covers the
everyday things: stats, the query log, block/allow, flushing the cache, and drift.

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
   pause of 5, 15, 30 or 60 minutes, and "resume" (blocking back on). It refuses
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

## 2. Configure the add-on

| Option | Default | |
|---|---|---|
| `technitium_url` | `http://192.168.1.198:5380` | The **primary** node's web console URL. Secondaries are reached through it. |
| `technitium_token` | | The token from step 1. |
| `api_key` | *(generated)* | The key the integration uses. Leave it empty and one is generated and kept in `/data/api_key`. |
| `cache_seconds` | `30` | How long Technitium answers are cached. All browsers and the integration share one upstream call per window, and the page's refresh picker won't go below this. |

Start the add-on, then open **Technitium** in the sidebar.

## 3. Add the integration

Install **Technitium Cluster** from HACS (same repository) and restart Home Assistant.
The add-on announces itself to Home Assistant, so the integration should appear
under **Settings → Devices & services → Discovered**. If it doesn't, add it by
hand. The URL is `http://<add-on hostname>:8081` (the hostname is shown on the
add-on's Info tab), plus the API key from `/data/api_key` or the option.

## Network access

The sidebar panel goes through Home Assistant ingress, so it needs no port and
no separate login. The `8081/tcp` port is off by default. If you expose it, every
request needs `Authorization: Bearer <api_key>`, so it's only useful for scripts,
not a browser.
