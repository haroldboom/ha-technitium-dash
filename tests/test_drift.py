import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "technitium_dash", "app"))

import drift  # noqa: E402

A = "cyberdyne.dns.home.arpa"
B = "umbrel.dns.home.arpa"
NODES = [("cyberdyne", A), ("umbrel", B)]

SETTINGS = {
    "version": "15.6.1",
    "uptimestamp": "2026-10-13T01:00:00Z",
    "clusterInitialized": True,
    "dnsServerDomain": "cyberdyne.dns.home.arpa",
    "dnsServerLocalEndPoints": ["0.0.0.0:53", "[::]:53"],
    "webServiceLocalAddresses": ["[::]"],
    "webServiceTlsCertificatePath": None,
    "webServiceTlsCertificatePassword": None,
    "zoneTransferAllowedNetworks": ["192.168.1.0/24", "10.0.0.0/8"],
    "qpmPrefixLimitsIPv4": [{"prefix": 32, "udpLimit": 600, "tcpLimit": 600}],
    "tsigKeys": [{"keyName": "home", "sharedSecret": "AAAA", "algorithmName": "hmac-sha256"}],
    "cacheMaximumEntries": 10000,
    "enableBlocking": True,
    "blockListUrls": ["https://big.oisd.nl/", "https://example/hosts"],
    "blockListNextUpdatedOn": "2026-10-14T01:00:00Z",
    "proxy": None,
    "forwarders": ["1.1.1.1", "1.0.0.1"],
    "forwarderProtocol": "Tls",
}


def _zone_resp(domain, zones, records=False):
    recs = []
    if records:
        recs = [{"name": domain, "type": "SOA", "ttl": "14400", "rData": {"serial": 1}}]
    return {"status": "ok", "response": {"domain": domain, "zones": zones, "records": recs}}


def make_tree(domains):
    """Build a fake allowed/blocked tree: {queried domain: response}."""
    nodes = {"": set()}
    for d in domains:
        labels = d.split(".")
        for i in range(len(labels) - 1, -1, -1):
            name = ".".join(labels[i:])
            parent = ".".join(labels[i + 1:])
            nodes.setdefault(name, set())
            nodes.setdefault(parent, set()).add(name)
    return {n: _zone_resp(n, sorted(c), n in domains) for n, c in nodes.items()}


class FakeAPI:
    def __init__(self):
        self.data = {}
        for short, full in NODES:
            s = copy.deepcopy(SETTINGS)
            s["dnsServerDomain"] = full
            self.data[full] = {
                "settings": s,
                "allowed": ["example.com", "mail.google.com"],
                "blocked": ["ads.example.net"],
                "apps": [{"name": "Query Logs (Sqlite)", "version": "8.0"},
                         {"name": "Advanced Blocking", "version": "10.0"}],
                "config": {
                    "Query Logs (Sqlite)": json.dumps({"enableLogging": True, "maxLogDays": 7}),
                    "Advanced Blocking": json.dumps({"enableBlocking": True,
                                                     "networkGroupMap": {"0.0.0.0/0": "everyone"}}),
                },
            }
        self.fail = set()  # (full, path) or (full, "*")
        self.calls = []

    def __call__(self, path, params):
        self.calls.append((path, dict(params)))
        full = params["node"]
        if (full, path) in self.fail or (full, "*") in self.fail:
            raise ConnectionError("node unreachable")
        d = self.data[full]
        if path == "settings/get":
            return {"status": "ok", "response": copy.deepcopy(d["settings"]), "server": full}
        if path in ("allowed/list", "blocked/list"):
            tree = make_tree(d[path.split("/")[0]])
            dom = params.get("domain", "")
            if dom not in tree:
                return _zone_resp(dom, [])
            return tree[dom]
        if path == "apps/list":
            return {"status": "ok", "response": {"apps": copy.deepcopy(d["apps"])}}
        if path == "apps/config/get":
            return {"status": "ok", "response": {"config": d["config"].get(params["name"])}}
        return {"status": "error", "errorMessage": "unknown path " + path}


def section(result, sid):
    return next(s for s in result["sections"] if s["id"] == sid)


class DriftTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()

    def run_drift(self):
        r = drift.compute_drift(self.api, NODES)
        json.dumps(r)  # must be serialisable
        return r

    def test_identical_nodes_ok(self):
        r = self.run_drift()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["nodes"], ["cyberdyne", "umbrel"])
        self.assertEqual([s["id"] for s in r["sections"]], ["settings", "allowed", "blocked", "apps"])
        self.assertTrue(all(s["ok"] and s["diffs"] == [] for s in r["sections"]))
        self.assertEqual(r["errors"], [])
        self.assertTrue(r["generatedAt"].endswith("Z"))
        # every call carries node=
        self.assertTrue(all("node" in p for _, p in self.api.calls))

    def test_forwarders_diff_order_matters(self):
        self.api.data[B]["settings"]["forwarders"] = ["1.0.0.1", "1.1.1.1"]
        r = self.run_drift()
        s = section(r, "settings")
        self.assertFalse(s["ok"])
        self.assertFalse(r["ok"])
        self.assertEqual(s["diffs"], [{"key": "forwarders", "values": {
            "cyberdyne": ["1.1.1.1", "1.0.0.1"], "umbrel": ["1.0.0.1", "1.1.1.1"]}}])

    def test_unordered_list_reorder_not_reported(self):
        self.api.data[B]["settings"]["blockListUrls"].reverse()
        self.api.data[B]["settings"]["zoneTransferAllowedNetworks"].reverse()
        self.assertTrue(section(self.run_drift(), "settings")["ok"])

    def test_ignored_per_node_fields(self):
        s = self.api.data[B]["settings"]
        s["dnsServerLocalEndPoints"] = ["192.168.1.150:53"]
        s["uptimestamp"] = "2026-10-01T00:00:00Z"
        s["version"] = "15.6.0"
        s["webServiceTlsCertificatePath"] = "/etc/umbrel.pfx"
        s["clusterDomain"] = "dns.home.arpa"  # unknown cluster-identity key
        s["blockListNextUpdatedOn"] = "2026-10-15T00:00:00Z"
        s["temporaryDisableBlockingTill"] = "2026-10-14T12:56:20.1763087Z"  # same pause, stamped ms apart
        # dnsServerDomain already differs per node in the fixture
        r = self.run_drift()
        self.assertTrue(section(r, "settings")["ok"], section(r, "settings"))

    def test_redacted_secret(self):
        self.api.data[B]["settings"]["tsigKeys"][0]["sharedSecret"] = "BBBB"
        self.api.data[A]["settings"]["proxy"] = {"type": "Socks5", "address": "10.0.0.1",
                                                 "port": 1080, "username": "u", "password": "hunter2"}
        r = self.run_drift()
        diffs = {d["key"]: d["values"] for d in section(r, "settings")["diffs"]}
        self.assertNotIn("BBBB", json.dumps(r))
        self.assertNotIn("AAAA", json.dumps(r))
        self.assertNotIn("hunter2", json.dumps(r))
        self.assertEqual(diffs["tsigKeys"]["umbrel"][0]["sharedSecret"], drift.REDACTED)
        self.assertEqual(diffs["tsigKeys"]["umbrel"][0]["keyName"], "home")
        self.assertEqual(diffs["proxy.password"], {"cyberdyne": drift.REDACTED, "umbrel": None})
        self.assertEqual(diffs["proxy.address"], {"cyberdyne": "10.0.0.1", "umbrel": None})

    def test_equal_secret_not_reported(self):
        for full in (A, B):
            self.api.data[full]["settings"]["proxy"] = {"type": "Http", "password": "same"}
        self.assertTrue(section(self.run_drift(), "settings")["ok"])

    def test_allowed_zone_set_difference(self):
        self.api.data[B]["allowed"] = ["example.com", "only.umbrel.org"]
        r = self.run_drift()
        s = section(r, "allowed")
        self.assertFalse(s["ok"])
        self.assertEqual(s["diffs"], [
            {"key": "mail.google.com", "values": {"cyberdyne": "mail.google.com", "umbrel": None}},
            {"key": "only.umbrel.org", "values": {"cyberdyne": None, "umbrel": "only.umbrel.org"}},
        ])
        self.assertTrue(section(r, "blocked")["ok"])

    def test_nested_entries_found(self):
        # parent and child both entries; child only on one node
        self.api.data[A]["blocked"] = ["example.net", "ads.example.net"]
        self.api.data[B]["blocked"] = ["example.net"]
        s = section(self.run_drift(), "blocked")
        self.assertEqual([d["key"] for d in s["diffs"]], ["ads.example.net"])

    def test_label_style_zone_names(self):
        # zones returned as bare labels instead of full names
        def call(path, params):
            dom = params.get("domain", "")
            tree = {"": _zone_resp("", ["com"]), "com": _zone_resp("com", ["example"]),
                    "example.com": _zone_resp("example.com", [], True)}
            if path in ("allowed/list", "blocked/list"):
                return tree.get(dom, _zone_resp(dom, []))
            return self.api(path, params)
        entries, trunc = drift._walk_zone_tree(call, "allowed/list", A)
        self.assertEqual(entries, {"example.com"})
        self.assertFalse(trunc)

    def test_zone_cap(self):
        self.api.data[A]["blocked"] = [f"d{i}.example.org" for i in range(30)]
        old = drift.ZONE_CAP
        try:
            entries, trunc = drift._walk_zone_tree(self.api, "blocked/list", A, cap=10)
        finally:
            drift.ZONE_CAP = old
        self.assertTrue(trunc)
        self.assertEqual(len(entries), 10)

    def test_app_version_and_config_diff(self):
        self.api.data[B]["apps"][1]["version"] = "9.1"
        self.api.data[B]["apps"].append({"name": "Split Horizon", "version": "6.0"})
        self.api.data[B]["config"]["Query Logs (Sqlite)"] = json.dumps(
            {"enableLogging": True, "maxLogDays": 30, "apiToken": "xyz"})
        r = self.run_drift()
        s = section(r, "apps")
        self.assertFalse(s["ok"])
        diffs = {d["key"]: d["values"] for d in s["diffs"]}
        self.assertEqual(diffs["Advanced Blocking"], {"cyberdyne": "10.0", "umbrel": "9.1"})
        self.assertEqual(diffs["Split Horizon"], {"cyberdyne": None, "umbrel": "6.0"})
        self.assertEqual(diffs["Query Logs (Sqlite):config.maxLogDays"], {"cyberdyne": 7, "umbrel": 30})
        self.assertEqual(diffs["Query Logs (Sqlite):config.apiToken"],
                         {"cyberdyne": None, "umbrel": drift.REDACTED})
        self.assertNotIn("xyz", json.dumps(r))
        # config not fetched for an app installed on one node only
        self.assertNotIn("Split Horizon", [p.get("name") for _, p in self.api.calls])

    def test_app_config_non_json(self):
        self.api.data[A]["config"]["Query Logs (Sqlite)"] = "# plain text\nfoo=1"
        self.api.data[B]["config"]["Query Logs (Sqlite)"] = "# plain text\nfoo=2"
        s = section(self.run_drift(), "apps")
        self.assertEqual(s["diffs"], [{"key": "Query Logs (Sqlite):config",
                                       "values": {"cyberdyne": "# plain text\nfoo=1",
                                                  "umbrel": "# plain text\nfoo=2"}}])

    def test_one_node_erroring(self):
        self.api.fail.add((B, "*"))
        self.api.data[B]["settings"]["forwarders"] = ["9.9.9.9"]  # must not be reported
        r = self.run_drift()
        self.assertFalse(r["ok"])
        for s in r["sections"]:
            self.assertFalse(s["ok"])
            self.assertEqual(s["diffs"], [])
        self.assertEqual(len(r["errors"]), 4)
        self.assertEqual({e["node"] for e in r["errors"]}, {"umbrel"})
        self.assertEqual([e["section"] for e in r["errors"]], ["settings", "allowed", "blocked", "apps"])

    def test_error_status_response(self):
        orig = self.api

        def call(path, params):
            if path == "apps/list" and params["node"] == A:
                return {"status": "error", "errorMessage": "boom"}
            return orig(path, params)
        r = drift.compute_drift(call, NODES)
        self.assertFalse(section(r, "apps")["ok"])
        self.assertTrue(section(r, "settings")["ok"])
        self.assertEqual(r["errors"][0]["node"], "cyberdyne")
        self.assertIn("boom", r["errors"][0]["error"])
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main()
