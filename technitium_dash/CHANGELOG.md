# Changelog

## 0.1.3

- Fix: the add-on never announced itself to Supervisor, so the integration didn't show up
  under Discovered. The base image's init started the server with an empty environment
  (no `SUPERVISOR_TOKEN`; it also made the version show as "dev"). The add-on now logs a
  line either way.

## 0.1.2

- Header shows the Technitium logo (just the logo on phones) instead of "Home DNS". The
  add-on fetches the logo from your primary node and caches it for a day; if it can't,
  the header falls back to the name.

## 0.1.1

- Tidier header on phones: two fixed rows (title, refresh and theme; then sync status and
  blocking) instead of buttons wrapping onto a third line.

## 0.1.0

- First release: overview, query log, block/allow, read-only blocking status and
  node drift check for a Technitium DNS cluster, with HA ingress, a refresh-interval
  picker, server-side caching, and Supervisor discovery for the integration.
