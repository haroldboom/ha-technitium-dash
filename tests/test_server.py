"""Tests for the add-on server: caching, auth, write allowlist, summary shape."""
import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

os.environ["DASH_OPTIONS_FILE"] = "/nonexistent/options.json"
os.environ["TECHNITIUM_TOKEN"] = "test-token"
os.environ["DASH_API_KEY"] = "secret-key"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "technitium_dash", "app"))

import server  # noqa: E402

NODES = {"status": "ok", "response": {"clusterInitialized": True, "clusterNodes": [
    {"name": "cyberdyne.dns.home.arpa", "type": "Primary", "state": "Self", "version": "15.6.1"},
    {"name": "umbrel.dns.home.arpa", "type": "Secondary", "state": "Connected", "version": "15.6.1"},
]}}


def stats(q, b, c):
    return {"status": "ok", "response": {"stats": {
        "totalQueries": q, "totalBlocked": b, "totalCached": c, "totalNxDomain": 1,
        "totalServerFailure": 0, "totalClients": 3, "blockListZones": 10,
        "allowedZones": 0, "blockedZones": 0}}}


class FakeTechnitium:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()
        self.down = set()

    def __call__(self, path, params=None, timeout=15):
        params = dict(params or {})
        with self.lock:
            self.calls.append((path, params))
        node = params.get("node", "")
        if node.split(".")[0] in self.down:
            return {"status": "error", "errorMessage": f"{node} unreachable"}
        if path == "admin/cluster/state":
            return NODES
        if path == "dashboard/stats/get":
            return {"cluster": stats(300, 30, 200), "cyberdyne.dns.home.arpa": stats(200, 20, 150),
                    "umbrel.dns.home.arpa": stats(100, 10, 50)}[node]
        if path == "settings/get":
            return {"status": "ok", "response": {"enableBlocking": True, "temporaryDisableBlockingTill": None,
                                                 "blockingType": "NxDomain", "blockListUrls": ["a"]}}
        return {"status": "ok", "response": {}}

    def count(self, path):
        return sum(1 for p, _ in self.calls if p == path)


class ServerTest(unittest.TestCase):
    def setUp(self):
        server.CACHE = server.TTLCache()
        self.fake = FakeTechnitium()
        self.p = mock.patch.object(server, "tcall", self.fake)
        self.p.start()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.p.stop()

    def req(self, path, method="GET", body=None, key="secret-key", headers=None):
        h = dict(headers or {})
        if key:
            h["Authorization"] = f"Bearer {key}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    # --- auth
    def test_health_is_open(self):
        code, body = self.req("/api/health", key=None)
        self.assertEqual(code, 200)
        self.assertTrue(body["tokenConfigured"])

    def test_api_key_required(self):
        self.assertEqual(self.req("/api/summary", key=None)[0], 401)
        self.assertEqual(self.req("/api/summary", key="wrong")[0], 401)
        self.assertEqual(self.req("/api/summary")[0], 200)

    def test_ingress_proxy_trusted(self):
        with mock.patch.object(server, "INGRESS_PROXY", "127.0.0.1"):
            self.assertEqual(self.req("/api/nodes", key=None)[0], 200)

    # --- caching
    def test_reads_are_cached(self):
        for _ in range(5):
            self.req("/tapi/dashboard/stats/get?node=cluster&type=LastHour")
        self.assertEqual(self.fake.count("dashboard/stats/get"), 1)

    def test_cache_is_single_flight(self):
        def slow(path, params=None, timeout=15):
            time.sleep(0.2)
            return self.fake(path, params)
        with mock.patch.object(server, "tcall", slow):
            ts = [threading.Thread(target=self.req, args=("/tapi/apps/list?node=x",)) for _ in range(6)]
            [t.start() for t in ts]
            [t.join() for t in ts]
        self.assertEqual(self.fake.count("apps/list"), 1)

    def test_errors_cached_briefly(self):
        self.fake.down.add("umbrel")
        server.cached_tcall("dashboard/stats/get", {"node": "umbrel.dns.home.arpa"})
        hit = server.CACHE.peek(("t", "dashboard/stats/get", (("node", "umbrel.dns.home.arpa"),)))
        self.assertEqual(hit[2], server.ERROR_TTL)

    def test_write_invalidates_section_cache(self):
        self.req("/tapi/allowed/list?node=a")
        self.req("/tapi/blocked/list?node=a")
        code, _ = self.req("/tapi/allowed/add", "POST", {"domain": "x.com", "node": "a"}, headers={"X-Dash": "1"})
        self.assertEqual(code, 200)
        self.req("/tapi/allowed/list?node=a")
        self.req("/tapi/blocked/list?node=a")
        self.assertEqual(self.fake.count("allowed/list"), 2)
        self.assertEqual(self.fake.count("blocked/list"), 1)

    # --- write allowlist
    def test_write_needs_x_dash(self):
        self.assertEqual(self.req("/tapi/cache/flush", "POST", {"node": "a"})[0], 403)

    def test_settings_writes_refused(self):
        h = {"X-Dash": "1"}
        # turning blocking off, any other setting, or a node target are all refused
        self.assertEqual(self.req("/tapi/settings/set", "POST", {"enableBlocking": False}, headers=h)[0], 400)
        self.assertEqual(self.req("/tapi/settings/set", "POST", {"enableBlocking": "false"}, headers=h)[0], 400)
        self.assertEqual(self.req("/tapi/settings/set", "POST", {}, headers=h)[0], 400)
        self.assertEqual(self.req("/tapi/settings/set", "POST", {"forwarders": "1.2.3.4"}, headers=h)[0], 403)
        self.assertEqual(self.req("/tapi/settings/set", "POST", {"enableBlocking": True, "node": "umbrel"}, headers=h)[0], 403)
        self.assertEqual(self.fake.count("settings/set"), 0)

    def test_pause_values_limited(self):
        h = {"X-Dash": "1"}
        for bad in ({"minutes": 0}, {"minutes": 1440}, {"minutes": "15; x"}, {}):
            self.assertEqual(self.req("/tapi/settings/temporaryDisableBlocking", "POST", bad, headers=h)[0], 400)
        self.assertEqual(self.req("/tapi/settings/temporaryDisableBlocking", "POST",
                                  {"minutes": 15, "node": "umbrel"}, headers=h)[0], 403)
        self.assertEqual(self.fake.count("settings/temporaryDisableBlocking"), 0)

    def test_pause_and_resume_go_to_primary_and_refresh_status(self):
        h = {"X-Dash": "1"}
        self.req("/api/blocking")
        before = self.fake.count("settings/get")
        code, _ = self.req("/tapi/settings/temporaryDisableBlocking", "POST", {"minutes": 15}, headers=h)
        self.assertEqual(code, 200)
        self.assertIn(("settings/temporaryDisableBlocking", {"minutes": "15"}), self.fake.calls)
        code, _ = self.req("/tapi/settings/set", "POST", {"enableBlocking": True}, headers=h)
        self.assertEqual(code, 200)
        self.assertIn(("settings/set", {"enableBlocking": "true"}), self.fake.calls)
        self.req("/api/blocking")  # the write dropped the cached settings, so this reads again
        self.assertGreater(self.fake.count("settings/get"), before)

    def test_extra_params_refused(self):
        code, body = self.req("/tapi/blocked/add", "POST", {"domain": "x.com", "forwarders": "1.2.3.4"},
                              headers={"X-Dash": "1"})
        self.assertEqual(code, 403)
        self.assertIn("forwarders", body["errorMessage"])

    def test_unknown_read_path_refused(self):
        self.assertEqual(self.req("/tapi/settings/get")[0], 403)
        self.assertEqual(self.req("/tapi/admin/users/list")[0], 403)

    # --- summary
    def test_summary_shape(self):
        code, s = self.req("/api/summary")
        self.assertEqual(code, 200)
        self.assertEqual(s["cluster"]["totalQueries"], 300)
        self.assertEqual(s["cluster"]["blockedPct"], 10.0)
        self.assertEqual([n["short"] for n in s["nodes"]], ["cyberdyne", "umbrel"])
        self.assertTrue(all(n["reachable"] for n in s["nodes"]))
        self.assertEqual(s["nodes"][1]["stats"]["cachedPct"], 50.0)
        self.assertEqual(s["nodes"][0]["blocking"], {"enabled": True, "pausedUntil": None})
        self.assertEqual(s["errors"], [])

    def test_summary_node_down(self):
        self.fake.down.add("umbrel")
        _, s = self.req("/api/summary")
        umbrel = s["nodes"][1]
        self.assertFalse(umbrel["reachable"])
        self.assertIsNone(umbrel["stats"])
        self.assertIsNone(umbrel["blocking"])
        self.assertEqual([e["node"] for e in s["errors"]], ["umbrel"])

    def test_summary_bad_range(self):
        self.assertEqual(self.req("/api/summary?range=Forever")[0], 400)

    # --- drift
    def test_drift_force_rate_limited(self):
        with mock.patch.object(server.drift, "compute_drift", return_value={"ok": True, "sections": []}) as cd:
            self.req("/api/drift")
            self.req("/api/drift")
            self.req("/api/drift?force=1")  # within DRIFT_FORCE_MIN: served from cache
            self.assertEqual(cd.call_count, 1)
            with server.CACHE._lock:
                t, v, ttl = server.CACHE._d["drift"]
                server.CACHE._d["drift"] = (t - server.DRIFT_FORCE_MIN - 1, v, ttl)
            self.req("/api/drift?force=1")
            self.assertEqual(cd.call_count, 2)


if __name__ == "__main__":
    unittest.main()
