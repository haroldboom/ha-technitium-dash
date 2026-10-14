"""Cross-node drift detection for a 2-node Technitium DNS Server cluster.

Notes for the orchestrator
--------------------------
* Public entry point: ``compute_drift(call, nodes) -> dict`` (shape = CONTRACT.md
  ``/api/drift``). Calls are made sequentially, so ``call`` need not be thread-safe.
* APIDOCS marks forwarders, enableBlocking, blockListUrls, tsigKeys, proxy* etc. as
  "cluster parameter" (i.e. supposed to sync); per the contract we compare them
  anyway -- that is the point of this module.
* ``proxy`` comes back as ``null`` or an object; it is flattened to ``proxy.<field>``.
  A key that is ``null`` on one node and absent on the other counts as equal.
* allowed/list + blocked/list: assumed ``response.zones`` is a list of child zone
  names. Docs don't say whether they are full names or bare labels; both are
  handled (a name not ending in ``.<domain>`` is treated as a label). Objects with a
  ``name`` field are tolerated too. A domain is counted as an entry when its
  ``records`` list is non-empty (Allowed/Blocked entries carry SOA/NS records).
  The walk is depth-first in sorted order, so if both nodes hit the 2000-entry cap
  the truncated sets are comparable prefixes; the section is then marked ok=false
  with a note in ``errors`` (diffs are still reported, but may be partial).
  allowed/export would be cheaper but returns text/plain, which ``call`` can't parse.
* Apps diff keys: ``<App Name>`` (values = version or null when not installed) and
  ``<App Name>:config.<dotted.path>`` (JSON configs) or ``<App Name>:config``
  (non-JSON text). App-config lists are compared order-sensitively (conservative).
  A non-JSON config that differs is redacted wholesale if it looks secret-bearing.
* Any failed call inside a section -> error recorded, section ok=false, diffs=[].
"""

import datetime
import json
import re

REDACTED = "«redacted»"
_MISSING = object()

# Settings fields that are *supposed* to differ per node (top-level keys of
# settings/get ``response``). Each one is skipped entirely, including sub-keys.
IGNORE = {
    "version": "server build; nodes may upgrade at different times",
    "uptimestamp": "process start time",
    "dnsServerDomain": "node's own identity / FQDN",
    "dnsServerLocalEndPoints": "per-host listen IPs",
    "dnsServerIPv4SourceAddresses": "per-host outbound source IPs",
    "dnsServerIPv6SourceAddresses": "per-host outbound source IPs",
    "webServiceLocalAddresses": "per-host web console listen IPs",
    "webServiceHttpPort": "per-host web console port",
    "webServiceTlsPort": "per-host web console TLS port",
    "webServiceHttpUnixSocket": "host filesystem path",
    "webServiceTlsUnixSocket": "host filesystem path",
    "webServiceTlsCertificatePath": "per-host certificate file",
    "webServiceTlsCertificatePassword": "per-host certificate password",
    "webServiceUseSelfSignedTlsCertificate": "per-host cert choice (self-signed cert is host-specific)",
    "dnsTlsCertificatePath": "per-host certificate file",
    "dnsTlsCertificatePassword": "per-host certificate password",
    "dnsOverHttpUnixSocket": "host filesystem path",
    "dnsOverHttpsUnixSocket": "host filesystem path",
    "logFolder": "host filesystem path",
    "blockListNextUpdatedOn": "timestamp; each node schedules its own block-list refresh",
    "temporaryDisableBlockingTill": "pause end time; each node stamps its own (ms apart), shown on the Blocking tab",
}
# Any other top-level key starting with "cluster" (cluster domain, node list,
# node identity...) is ignored, except this one, which should match on both nodes.
IGNORE_PREFIX = "cluster"
IGNORE_PREFIX_KEEP = {"clusterInitialized"}

# Leaf key names whose values are secrets: diffed on raw values, output redacted.
_REDACT_RE = re.compile(
    r"(password|passwd|passphrase|secret|token|credential|apikey|api_key|"
    r"privatekey|private_key|(^|_)key$|[a-z]Key$|^key$)",
    re.IGNORECASE,
)
# The leaf-key test above is case-insensitive, so "[a-z]Key$" also hits e.g.
# "monkey"; acceptable -- over-redaction is the safe failure mode.
_TEXT_SECRET_RE = re.compile(r"password|passwd|secret|token|apikey|api_key|privatekey", re.I)

# Settings lists where order is meaningful (kept as-is); all others are sorted.
ORDERED_LISTS = {
    "forwarders",  # tried in order (unless concurrentForwarding)
    "webServiceReverseProxyAddresses",  # ACL, first match wins
    "recursionNetworkACL",  # ACL, first match wins
    "dnsReverseProxyNetworkACL",  # ACL, first match wins
}

ZONE_CAP = 2000  # max entries collected per node per zone tree
_CALL_CAP = 6000  # safety stop on tree-walk API calls per node


class _CallError(Exception):
    pass


def _api(call, path, params):
    try:
        res = call(path, params)
    except Exception as e:  # network / parse errors
        raise _CallError(f"{path}: {type(e).__name__}: {e}") from None
    if not isinstance(res, dict):
        raise _CallError(f"{path}: unexpected response type {type(res).__name__}")
    if res.get("status") != "ok":
        msg = res.get("errorMessage") or res.get("status") or "unknown error"
        raise _CallError(f"{path}: {res.get('status')}: {msg}")
    resp = res.get("response")
    return resp if isinstance(resp, dict) else {}


def _is_secret(key):
    return bool(_REDACT_RE.search(key))


def _redact_deep(v):
    if isinstance(v, dict):
        return {k: (REDACTED if _is_secret(k) and x is not None else _redact_deep(x))
                for k, x in v.items()}
    if isinstance(v, list):
        return [_redact_deep(x) for x in v]
    return v


def _canon(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), default=str)


def _flatten(obj, prefix=""):
    """Dicts -> dotted keys; lists and scalars are leaves."""
    out = {}
    for k, v in obj.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and v:
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def _norm(key, v, ordered_keys, sort_lists):
    if isinstance(v, list) and sort_lists and key.split(".")[0] not in ordered_keys:
        return sorted(v, key=_canon)
    return v


def _diff_flat(flats, prefix_key="", ordered_keys=frozenset(), sort_lists=True):
    """flats: {short: flat dict}. Returns list of diffs (redacted)."""
    diffs = []
    keys = sorted(set().union(*[f.keys() for f in flats.values()]))
    for k in keys:
        raw = {}
        for short, f in flats.items():
            v = f.get(k, _MISSING)
            raw[short] = None if v is _MISSING else _norm(k, v, ordered_keys, sort_lists)
        if len({_canon(v) for v in raw.values()}) <= 1:
            continue
        leaf = k.rsplit(".", 1)[-1]
        values = {}
        for short, v in raw.items():
            if v is None:
                values[short] = None
            elif _is_secret(leaf):
                values[short] = REDACTED
            else:
                values[short] = _redact_deep(v)
        diffs.append({"key": prefix_key + k, "values": values})
    return diffs


def _settings_ignored(key):
    if key in IGNORE:
        return True
    return key.startswith(IGNORE_PREFIX) and key not in IGNORE_PREFIX_KEEP


# ---------------------------------------------------------------- sections

def _section_settings(call, nodes, errors):
    flats = {}
    for short, full in nodes:
        resp = _api(call, "settings/get", {"node": full})
        top = {k: v for k, v in resp.items() if not _settings_ignored(k)}
        flats[short] = _flatten(top)
    return _diff_flat(flats, ordered_keys=ORDERED_LISTS)


def _child_name(z, domain):
    if isinstance(z, dict):
        z = z.get("name") or z.get("domain") or ""
    z = str(z).strip().rstrip(".").lower()
    if not z:
        return None
    if not domain or (z != domain and z.endswith("." + domain)):
        return z
    return f"{z}.{domain}"


def _walk_zone_tree(call, path, full, cap=ZONE_CAP):
    """Return (set of entry domains, truncated: bool)."""
    entries, seen = set(), set()
    stack = [""]
    calls = 0
    while stack:
        if len(entries) >= cap or calls >= _CALL_CAP:
            return entries, True
        domain = stack.pop()
        if domain in seen:
            continue
        seen.add(domain)
        params = {"node": full}
        if domain:
            params["domain"] = domain
        resp = _api(call, path, params)
        calls += 1
        here = str(resp.get("domain") or domain).rstrip(".").lower()
        seen.add(here)
        if here and resp.get("records"):
            entries.add(here)
        children = []
        for z in resp.get("zones") or []:
            c = _child_name(z, here)
            if c and c not in seen:
                children.append(c)
        # pop() takes from the end -> push reverse-sorted for sorted DFS order
        stack.extend(sorted(set(children), reverse=True))
    return entries, False


def _section_zone_list(path, sid):
    def run(call, nodes, errors):
        sets, truncated = {}, False
        for short, full in nodes:
            entries, trunc = _walk_zone_tree(call, path, full)
            sets[short] = entries
            if trunc:
                truncated = True
                errors.append({"node": short, "section": sid,
                               "error": f"stopped after {len(entries)} entries "
                                        f"(cap {ZONE_CAP}); comparison is partial"})
        diffs = []
        for d in sorted(set().union(*sets.values())):
            if all(d in s for s in sets.values()):
                continue
            diffs.append({"key": d, "values": {sh: (d if d in s else None)
                                                for sh, s in sets.items()}})
        return diffs, (not truncated)
    return run


def _section_apps(call, nodes, errors):
    apps = {}
    for short, full in nodes:
        resp = _api(call, "apps/list", {"node": full})
        apps[short] = {a.get("name"): a.get("version") for a in resp.get("apps") or []
                       if isinstance(a, dict) and a.get("name")}
    diffs = []
    names = sorted(set().union(*[a.keys() for a in apps.values()]))
    common = [n for n in names if all(n in a for a in apps.values())]
    for n in names:
        vals = {sh: a.get(n) if n in a else None for sh, a in apps.items()}
        if n not in common or len({_canon(v) for v in vals.values()}) > 1:
            diffs.append({"key": n, "values": vals})
    for n in common:
        configs = {}
        for short, full in nodes:
            resp = _api(call, "apps/config/get", {"node": full, "name": n})
            configs[short] = resp.get("config")
        diffs.extend(_diff_app_config(n, configs))
    return diffs


def _parse_config(text):
    if text is None:
        return None, True
    if not isinstance(text, str):
        return text, True
    if not text.strip():
        return None, True
    try:
        return json.loads(text), True
    except ValueError:
        return text, False


def _diff_app_config(name, configs):
    parsed = {sh: _parse_config(c) for sh, c in configs.items()}
    if all(ok and isinstance(v, (dict, type(None))) for v, ok in parsed.values()):
        flats = {sh: _flatten(v) if isinstance(v, dict) else {} for sh, (v, _) in parsed.items()}
        return _diff_flat(flats, prefix_key=f"{name}:config.", sort_lists=False)
    # Not all dict-shaped JSON: compare canonical forms as a whole.
    canon = {sh: (None if v is None else (_canon(v) if ok else v.strip()))
             for sh, (v, ok) in parsed.items()}
    if len(set(canon.values())) <= 1:
        return []
    values = {}
    for sh, (v, ok) in parsed.items():
        if v is None:
            values[sh] = None
        elif ok:
            values[sh] = _redact_deep(v)
        else:
            values[sh] = REDACTED if _TEXT_SECRET_RE.search(v) else v
    return [{"key": f"{name}:config", "values": values}]


def _wrap(fn):
    def run(call, nodes, errors):
        return fn(call, nodes, errors), True
    return run


SECTIONS = [
    ("settings", "DNS settings", _wrap(_section_settings)),
    ("allowed", "Allowed zones", _section_zone_list("allowed/list", "allowed")),
    ("blocked", "Blocked zones", _section_zone_list("blocked/list", "blocked")),
    ("apps", "Apps", _wrap(_section_apps)),
]


def compute_drift(call, nodes):
    """Compare all nodes; returns the CONTRACT.md /api/drift JSON shape."""
    nodes = list(nodes)
    errors, sections = [], []
    for sid, title, fn in SECTIONS:
        # Wrap call so failures are attributed to the node that was asked.
        current = {"node": None}

        def tracked(path, params, _c=current):
            full = params.get("node")
            _c["node"] = next((s for s, f in nodes if f == full), full)
            return call(path, params)

        try:
            diffs, complete = fn(tracked, nodes, errors)
            ok = complete and not diffs
        except _CallError as e:
            errors.append({"node": current["node"], "section": sid, "error": str(e)})
            diffs, ok = [], False
        except Exception as e:  # never crash the endpoint
            errors.append({"node": current["node"], "section": sid,
                           "error": f"internal: {type(e).__name__}: {e}"})
            diffs, ok = [], False
        sections.append({"id": sid, "title": title, "ok": ok, "diffs": diffs})
    return {
        "generatedAt": datetime.datetime.now(datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "nodes": [s for s, _ in nodes],
        "ok": all(s["ok"] for s in sections) and not errors,
        "sections": sections,
        "errors": errors,
    }
