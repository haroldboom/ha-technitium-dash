#!/usr/bin/env python3
"""Technitium cluster dashboard: static page + allowlisted, cached API proxy.

The Technitium token stays here; browsers and the Home Assistant integration only
ever talk to this server. See CONTRACT.md for the API.

Runs either as a Home Assistant add-on (options in /data/options.json) or
standalone (config.env next to this file, or TECHNITIUM_*/DASH_* env vars).
"""
import hmac
import json
import os
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
sys.path.insert(0, str(HERE))
import drift  # noqa: E402

OPTIONS_FILE = Path(os.environ.get("DASH_OPTIONS_FILE", "/data/options.json"))
INGRESS_PROXY = "172.30.32.2"  # Supervisor's ingress proxy address
VERSION = os.environ.get("BUILD_VERSION", "dev")


def load_config():
    """Normalised config: add-on options win, then config.env, then env vars."""
    cfg = {}
    env_file = HERE / "config.env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip("'\"")
    cfg.update({k: v for k, v in os.environ.items() if k.startswith(("TECHNITIUM_", "DASH_"))})
    out = {
        "url": cfg.get("TECHNITIUM_URL", "http://192.168.1.198:5380"),
        "token": cfg.get("TECHNITIUM_TOKEN", ""),
        "api_key": cfg.get("DASH_API_KEY", ""),
        "cache_seconds": cfg.get("DASH_CACHE_SECONDS", "30"),
        "port": cfg.get("DASH_PORT", "8081"),
        "bind": cfg.get("DASH_BIND", "0.0.0.0"),
        "addon": False,
    }
    if OPTIONS_FILE.exists():
        opts = json.loads(OPTIONS_FILE.read_text())
        out.update({
            "url": opts.get("technitium_url") or out["url"],
            "token": opts.get("technitium_token") or "",
            "api_key": opts.get("api_key") or "",
            "cache_seconds": opts.get("cache_seconds", 30),
            "addon": True,
        })
    if out["addon"] and not out["api_key"]:
        # no key configured: generate one, keep it across restarts, hand it to the
        # integration via Supervisor discovery
        key_file = OPTIONS_FILE.parent / "api_key"
        if not key_file.exists():
            key_file.write_text(secrets.token_urlsafe(32))
            key_file.chmod(0o600)
        out["api_key"] = key_file.read_text().strip()
    out["url"] = str(out["url"]).rstrip("/")
    out["cache_seconds"] = max(5, int(out["cache_seconds"]))
    out["port"] = int(out["port"])
    return out


CFG = load_config()
TOKEN, BASE, API_KEY = CFG["token"], CFG["url"], CFG["api_key"]
CACHE_S = CFG["cache_seconds"]
DRIFT_TTL = max(300, CACHE_S)
DRIFT_FORCE_MIN = 30
ERROR_TTL = 5  # failed upstream calls are cached briefly so a dead node isn't hammered

READ_PATHS = {
    "dashboard/stats/get", "dashboard/stats/getTop", "logs/query",
    "allowed/list", "blocked/list", "cache/list", "apps/list",
}
# path -> params a client may set
WRITE_PATHS = {
    "allowed/add": {"domain", "node"},
    "allowed/delete": {"domain", "node"},
    "blocked/add": {"domain", "node"},
    "blocked/delete": {"domain", "node"},
    "cache/flush": {"node"},
    "cache/delete": {"domain", "node"},
    # Blocking can only be paused (Technitium turns it back on itself) or resumed early.
    # Both go to the primary with no node: in a cluster Technitium relays them to every node.
    "settings/temporaryDisableBlocking": {"minutes"},
    "settings/set": {"enableBlocking"},
}
# Values the add-on lets through, where any value would be too much.
WRITE_VALUES = {
    ("settings/temporaryDisableBlocking", "minutes"): {str(m) for m in range(5, 61, 5)},  # 5-60, steps of 5
    ("settings/set", "enableBlocking"): {"true"},  # never off: no way to leave blocking disabled
}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tcall(path, params=None, timeout=15):
    """Uncached call to Technitium on the primary; returns parsed JSON or an error dict."""
    if not TOKEN:
        return {"status": "invalid-token", "errorMessage": "Technitium token not configured"}
    data = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None}).encode()
    req = urllib.request.Request(
        f"{BASE}/api/{path}", data=data, method="POST",
        headers={"Authorization": f"Bearer {TOKEN}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return {"status": "error", "errorMessage": f"HTTP {e.code} from Technitium"}
    except Exception as e:
        return {"status": "error", "errorMessage": f"{type(e).__name__}: {e}"}


class TTLCache:
    """Single-flight TTL cache: concurrent misses on one key make one upstream call."""

    def __init__(self):
        self._d, self._lock, self._key_locks = {}, threading.Lock(), {}

    def peek(self, key):
        with self._lock:
            return self._d.get(key)

    def get(self, key, ttl, fn, err_ttl=None):
        hit = self.peek(key)
        if hit and time.time() - hit[0] < hit[2]:
            return hit[1]
        with self._lock:
            klock = self._key_locks.setdefault(key, threading.Lock())
        with klock:
            hit = self.peek(key)
            if hit and time.time() - hit[0] < hit[2]:
                return hit[1]
            val = fn()
            bad = isinstance(val, dict) and val.get("status") not in (None, "ok")
            with self._lock:
                self._d[key] = (time.time(), val, err_ttl if bad and err_ttl else ttl)
            return val

    def drop(self, pred):
        with self._lock:
            for k in [k for k in self._d if pred(k)]:
                del self._d[k]


CACHE = TTLCache()


def cached_tcall(path, params=None):
    params = dict(params or {})
    key = ("t", path, tuple(sorted(params.items())))
    return CACHE.get(key, CACHE_S, lambda: tcall(path, params), err_ttl=ERROR_TTL)


def get_nodes():
    def build():
        r = tcall("admin/cluster/state")
        if r.get("status") != "ok":
            return {"status": "error", "nodes": [], "cluster": False,
                    "error": r.get("errorMessage") or r.get("status")}
        resp = r["response"]
        nodes = [{
            "name": n["name"], "short": n["name"].split(".")[0], "ip": n.get("ipAddress"),
            "role": n.get("type"), "state": n.get("state"), "version": n.get("version"),
            "lastSeen": n.get("lastSeen"),
        } for n in resp.get("clusterNodes", [])]
        nodes.sort(key=lambda n: n["role"] != "Primary")
        return {"nodes": nodes, "cluster": bool(resp.get("clusterInitialized"))}
    r = CACHE.get("nodes", CACHE_S, build, err_ttl=ERROR_TTL)
    return {k: v for k, v in r.items() if k != "status"}


def node_pairs():
    return [(n["short"], n["name"]) for n in get_nodes()["nodes"]]


def query_logger(node):
    """(name, classPath) of the Query Logs app on a node."""
    def find():
        r = tcall("apps/list", {"node": node})
        for app in (r.get("response") or {}).get("apps", []):
            for d in app.get("dnsApps", []):
                if d.get("isQueryLogger") and "query logs" in app["name"].lower():
                    return app["name"], d["classPath"]
        return None
    return CACHE.get(("ql", node), 600, find)


def blocking_for(short, name):
    r = cached_tcall("settings/get", {"node": name})
    if r.get("status") != "ok":
        return {"short": short, "name": name, "ok": False,
                "error": r.get("errorMessage") or r.get("status")}
    s = r["response"]
    return {"short": short, "name": name, "ok": True, "error": None,
            "enableBlocking": s.get("enableBlocking"),
            "temporaryDisableBlockingTill": s.get("temporaryDisableBlockingTill"),
            "blockingType": s.get("blockingType"),
            "blockListUrlCount": len(s.get("blockListUrls") or [])}


def get_blocking():
    with ThreadPoolExecutor(4) as ex:
        return {"nodes": list(ex.map(lambda p: blocking_for(*p), node_pairs()))}


# ---- drift -------------------------------------------------------------------

_drift_lock = threading.Lock()


def _compute_drift():
    pairs = node_pairs()
    if len(pairs) < 2:
        return {"ok": False, "nodes": [], "sections": [], "generatedAt": now_iso(),
                "errors": [{"node": None, "section": None,
                            "error": get_nodes().get("error") or "fewer than 2 cluster nodes"}]}
    return drift.compute_drift(tcall, pairs)


def get_drift(force=False):
    hit = CACHE.peek("drift")
    age = time.time() - hit[0] if hit else None
    if hit and not force and age < DRIFT_TTL:
        return hit[1]
    if hit and force and age < DRIFT_FORCE_MIN:
        return hit[1]
    with _drift_lock:
        hit = CACHE.peek("drift")
        if hit and time.time() - hit[0] < (DRIFT_FORCE_MIN if force else DRIFT_TTL):
            return hit[1]
        val = _compute_drift()
        with CACHE._lock:
            CACHE._d["drift"] = (time.time(), val, DRIFT_TTL)
        return val


def drift_summary():
    """Drift from cache only; kicks off a background compute if there's none."""
    hit = CACHE.peek("drift")
    if not hit or time.time() - hit[0] >= DRIFT_TTL:
        if not _drift_lock.locked():
            threading.Thread(target=get_drift, daemon=True).start()
    if not hit:
        return None
    d = hit[1]
    secs = {s["id"]: len(s.get("diffs") or []) for s in d.get("sections", [])}
    return {"ok": bool(d.get("ok")), "differences": sum(secs.values()) + len(d.get("errors") or []),
            "checkedAt": d.get("generatedAt"), "sections": secs}


# ---- summary (for the HA integration) ----------------------------------------

STAT_KEYS = ("totalQueries", "totalBlocked", "totalCached", "totalNxDomain",
             "totalServerFailure", "totalClients", "blockListZones", "allowedZones", "blockedZones")
RANGES = {"LastHour", "LastDay", "LastWeek", "LastMonth", "LastYear"}


def _stats(node, rng):
    r = cached_tcall("dashboard/stats/get", {"node": node, "type": rng, "utc": "true"})
    if r.get("status") != "ok":
        return None, r.get("errorMessage") or r.get("status")
    s = r["response"]["stats"]
    out = {k: s.get(k) for k in STAT_KEYS}
    q = s.get("totalQueries") or 0
    out["blockedPct"] = round(100 * (s.get("totalBlocked") or 0) / q, 1) if q else 0.0
    out["cachedPct"] = round(100 * (s.get("totalCached") or 0) / q, 1) if q else 0.0
    return out, None


def get_summary(rng="LastHour"):
    nodes_info = get_nodes()
    errors = [{"node": None, "error": nodes_info["error"]}] if nodes_info.get("error") else []
    nodes = nodes_info["nodes"]

    def per_node(n):
        stats, err = _stats(n["name"], rng)
        b = blocking_for(n["short"], n["name"])
        return n, stats, err, b

    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(per_node, nodes))
    out_nodes = []
    for n, stats, err, b in results:
        if err:
            errors.append({"node": n["short"], "error": err})
        out_nodes.append({
            "short": n["short"], "name": n["name"], "role": n["role"], "state": n["state"],
            "version": n["version"],
            "reachable": stats is not None and n["state"] in ("Self", "Connected"),
            "blocking": ({"enabled": b["enableBlocking"], "pausedUntil": b["temporaryDisableBlockingTill"]}
                         if b["ok"] else None),
            "stats": stats,
        })
    cluster = None
    if nodes_info.get("cluster"):
        cluster, err = _stats("cluster", rng)
        if err:
            errors.append({"node": "cluster", "error": err})
    elif len(out_nodes) == 1:
        cluster = out_nodes[0]["stats"]
    return {"generatedAt": now_iso(), "cacheSeconds": CACHE_S, "range": rng,
            "cluster": cluster, "nodes": out_nodes, "drift": drift_summary(), "errors": errors}


# ---- HTTP --------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "technitium-dash"

    def log_message(self, fmt, *args):
        if os.environ.get("DASH_ACCESS_LOG"):
            super().log_message(fmt, *args)

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def deny(self, msg="not allowed", code=403):
        self.send_json({"status": "error", "errorMessage": msg}, code)

    def authorized(self, path):
        """Ingress (HA already authenticated the user) is trusted; others need the api key."""
        if not API_KEY or path == "/api/health" or self.client_address[0] == INGRESS_PROXY:
            return True
        auth = self.headers.get("Authorization") or ""
        return auth.startswith("Bearer ") and hmac.compare_digest(auth[7:].encode(), API_KEY.encode())

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        path = url.path
        params = dict(urllib.parse.parse_qsl(url.query, keep_blank_values=True))
        if not self.authorized(path):
            return self.deny("unauthorized", 401)
        if path in ("/", "/index.html"):
            return self.send_static("index.html")
        if path == "/api/health":
            return self.send_json({"ok": True, "tokenConfigured": bool(TOKEN), "version": VERSION})
        if path == "/api/config":
            return self.send_json({"cacheSeconds": CACHE_S, "version": VERSION,
                                   "ingress": self.client_address[0] == INGRESS_PROXY})
        if path == "/api/nodes":
            return self.send_json(get_nodes())
        if path == "/api/blocking":
            return self.send_json(get_blocking())
        if path == "/api/drift":
            return self.send_json(get_drift(force=params.get("force") == "1"))
        if path == "/api/summary":
            rng = params.get("range", "LastHour")
            if rng not in RANGES:
                return self.deny("bad range", 400)
            return self.send_json(get_summary(rng))
        if path.startswith("/tapi/"):
            tpath = path[len("/tapi/"):]
            if tpath not in READ_PATHS:
                return self.deny()
            if tpath == "logs/query" and not params.get("classPath"):
                ql = query_logger(params.get("node", ""))
                if not ql:
                    return self.send_json({"status": "error", "errorMessage": "Query Logs app not found on node"})
                params["name"], params["classPath"] = ql
            return self.send_json(cached_tcall(tpath, params))
        return self.deny("not found", 404)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if not self.authorized(path):
            return self.deny("unauthorized", 401)
        if self.headers.get("X-Dash") != "1":
            return self.deny("missing X-Dash header")
        if not path.startswith("/tapi/") or path[len("/tapi/"):] not in WRITE_PATHS:
            return self.deny()
        tpath = path[len("/tapi/"):]
        n = int(self.headers.get("Content-Length") or 0)
        if n > 65536:
            return self.deny("body too large", 413)
        raw = self.rfile.read(n).decode() if n else ""
        try:
            if "json" in (self.headers.get("Content-Type") or ""):
                params = json.loads(raw or "{}")
                if not isinstance(params, dict):
                    raise ValueError
            else:
                params = dict(urllib.parse.parse_qsl(raw))
        except ValueError:
            return self.deny("bad body", 400)
        extra = set(params) - WRITE_PATHS[tpath]
        if extra:
            return self.deny(f"param not allowed: {', '.join(sorted(extra))}")
        params = {k: ("true" if v is True else "false" if v is False else str(v)) for k, v in params.items()}
        for (wpath, key), allowed in WRITE_VALUES.items():
            if wpath == tpath and params.get(key) not in allowed:
                return self.deny(f"{key} must be one of: {', '.join(sorted(allowed, key=lambda v: (len(v), v)))}", 400)
        r = tcall(tpath, params)
        # a write changes lists, cache contents and possibly drift: drop those caches
        section = tpath.split("/")[0]
        CACHE.drop(lambda k: k == "drift" or (isinstance(k, tuple) and k[0] == "t" and k[1].startswith(section + "/")))
        self.send_json(r)

    def send_static(self, name):
        f = STATIC / name
        if not f.is_file():
            return self.deny("not found", 404)
        body = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        # HA ingress shows the page in a same-origin iframe
        self.send_header("Content-Security-Policy", "frame-ancestors 'self'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def announce_discovery():
    """Tell Supervisor about this add-on so the HA integration can be set up in one click."""
    sup = os.environ.get("SUPERVISOR_TOKEN")
    if not sup:
        if CFG["addon"]:
            print("discovery skipped: no SUPERVISOR_TOKEN in the environment", flush=True)
        return
    body = json.dumps({"service": "technitium_dash", "config": {
        "host": socket.gethostname(), "port": CFG["port"], "api_key": API_KEY}}).encode()
    req = urllib.request.Request("http://supervisor/discovery", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {sup}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"discovery announced: {r.status}", flush=True)
    except Exception as e:
        print(f"discovery announce failed (set the integration up by URL instead): {e}", flush=True)


def main():
    srv = ThreadingHTTPServer((CFG["bind"], CFG["port"]), Handler)
    print(f"technitium-dash {VERSION} on {CFG['bind']}:{CFG['port']} -> {BASE} "
          f"(token {'set' if TOKEN else 'MISSING'}, api key {'set' if API_KEY else 'off'}, "
          f"cache {CACHE_S}s)", flush=True)
    threading.Thread(target=announce_discovery, daemon=True).start()
    threading.Thread(target=get_drift, daemon=True).start()  # warm the drift cache
    srv.serve_forever()


if __name__ == "__main__":
    main()
