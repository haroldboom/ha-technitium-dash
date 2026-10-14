# Changelog

## 0.3.0

- Pauses can be 5 to 60 minutes in 5-minute steps (the dashboard keeps its 5 / 15 / 30 /
  60 buttons).
- Integration 0.3.0: a **Blocking** switch (off pauses blocking on every node, and it
  flips back on when Technitium re-enables blocking) and a **Blocking pause length**
  slider that sets how long. Blocking still can't be turned off for good from HA.

## 0.2.0

- Pause blocking on every node for 5, 15, 30 or 60 minutes from the Blocking tab, and
  resume early. Technitium turns blocking back on by itself when the pause ends. Needs
  Settings **Modify** on the dashboard user. The add-on refuses every other settings
  change, including turning blocking off.
- Integration 0.2.0: `pause_blocking` and `resume_blocking` actions.

## 0.1.4

- Header drops the title and logo (the Home Assistant panel already has a heading). The
  sync status leads the header (its own row on phones), with blocking and the refresh
  controls next to it or below.
- The light/dark toggle moved to the footer.

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
