# Technitium Cluster Dashboard

![Supports aarch64][aarch64-shield] ![Supports amd64][amd64-shield]

A daily-use dashboard for a [Technitium DNS Server](https://technitium.com/dns/)
cluster, in the Home Assistant sidebar.

- Every node side by side: queries, blocked, cache, top clients and domains.
- The query log, and block / allow a domain on every node in one tap.
- Pause blocking on every node for a few minutes. Technitium turns it back on by itself.
- A drift check that shows where the nodes' settings, allowed and blocked zones, or
  apps have come apart.
- Upstream calls are cached, so more viewers don't mean more load on the DNS server.

It holds its own limited Technitium token and never changes DNS settings, apart
from pausing blocking if you allow it. The companion **Technitium Cluster**
integration (HACS) gets its sensors, Blocking switch and actions from this add-on.

See the **Documentation** tab for the Technitium user and permissions to set up.

[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg
