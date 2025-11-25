"""
WLST script to output Oracle WebLogic health information as JSON.

This is designed to be executed via wlst.sh and prints a single JSON
object to stdout. It can also be run with CPython for local testing
(by providing WLST_SAMPLE_OUTPUT pointing to a JSON file).
"""

# ---------------------------------------------------------------------------
# Robust JSON import with fallback (json -> simplejson -> minimal encoder)
# ---------------------------------------------------------------------------

HAS_REAL_JSON = False

try:
    import json
    HAS_REAL_JSON = True
except ImportError:
    try:
        import simplejson as json
        HAS_REAL_JSON = True
    except ImportError:
        # Very small JSON "module" implementing only dumps(), enough for WLST.
        def _json_escape_string(s):
            try:
                basestring_type = basestring  # Python 2 / Jython
            except NameError:
                basestring_type = str
            if s is None:
                return "\"\""
            if not isinstance(s, basestring_type):
                try:
                    s = str(s)
                except Exception:
                    s = repr(s)
            s = s.replace("\\", "\\\\").replace("\"", "\\\"")
            s = s.replace("\b", "\\b").replace("\f", "\\f").replace("\n", "\\n")
            s = s.replace("\r", "\\r").replace("\t", "\\t")
            return "\"" + s + "\""

        def _json_dumps(obj):
            if obj is None:
                return "null"
            if isinstance(obj, bool):
                return obj and "true" or "false"
            try:
                integer_types = (int, long)  # noqa
            except NameError:
                integer_types = (int,)
            if isinstance(obj, integer_types):
                return str(obj)
            if isinstance(obj, float):
                return str(obj)

            try:
                string_type = basestring  # noqa
            except NameError:
                string_type = str
            if isinstance(obj, string_type):
                return _json_escape_string(obj)

            if isinstance(obj, (list, tuple)):
                return "[" + ",".join([_json_dumps(x) for x in obj]) + "]"

            if isinstance(obj, dict):
                items = []
                for k, v in obj.items():
                    key_str = _json_escape_string(k)
                    items.append(key_str + ":" + _json_dumps(v))
                return "{" + ",".join(items) + "}"

            return _json_escape_string(str(obj))

        class _JsonModule(object):
            def dumps(self, obj):
                return _json_dumps(obj)

        json = _JsonModule()  # type: ignore

import os
import sys
from datetime import datetime

# For reading sample payloads when running under CPython
try:
    from io import open as io_open
except ImportError:
    io_open = open  # type: ignore

# ---------------------------------------------------------------------------
# Error tracking for consistent exception key generation
# ---------------------------------------------------------------------------

_error_counters = {
    "clusters": 0,
    "managed_servers": 0,
    "jms_servers": 0,
    "threads": 0,
    "datasources": 0,
    "deployments": 0,
    "composites": 0,
}


def _get_error_key(check_type):
    count = _error_counters.get(check_type, 0) + 1
    _error_counters[check_type] = count
    return "%s_error_%d" % (check_type, count)


# ---------------------------------------------------------------------------
# Collection normalisation: turn lists into dicts keyed by names
# (rewritten to avoid lambdas & dict comprehensions that upset WLST/Jython)
# ---------------------------------------------------------------------------

def _composite_key(item):
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    if not name:
        return None
    partition = item.get("partition") or item.get("partitionName") or "default"
    return "%s::%s" % (partition, name)


def normalize_collections(value, current_key=None):
    """
    Convert list-based collections to dicts keyed by 'name' (or composite name).
    This function is intentionally written in a very "old school" style so that
    Jython/WLST does not choke on any newer Python syntax.
    """
    # Lists
    if isinstance(value, list):
        mapping = {}
        index = 0
        for item in value:
            key = None

            if current_key == "composites":
                key = _composite_key(item)
            elif isinstance(item, dict):
                key = item.get("name")

            if not key:
                # Fallback generic key
                base = current_key or "item"
                key = "%s_%d" % (base, index)

            mapping[key] = normalize_collections(item, current_key)
            index += 1
        return mapping

    # Dicts
    if isinstance(value, dict):
        result = {}
        for k, child in value.items():
            result[k] = normalize_collections(child, k)
        return result

    # Everything else unchanged
    return value


# ---------------------------------------------------------------------------
# Sample payload loading (for CPython testing only)
# ---------------------------------------------------------------------------

def load_sample_payload(check):
    """
    If WLST_SAMPLE_OUTPUT is set and we have a real JSON parser,
    load a sample payload (for CPython/local testing).
    In WLST (where JSON may be missing), this will return None.
    """
    if not HAS_REAL_JSON:
        return None

    path = os.environ.get("WLST_SAMPLE_OUTPUT")
    if not path or not os.path.exists(path):
        return None

    try:
        with io_open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)  # type: ignore[attr-defined]
    except TypeError:
        # Jython / legacy: encoding not supported
        with io_open(path, "r") as handle:
            payload = json.load(handle)  # type: ignore[attr-defined]

    payload = normalize_collections(payload)

    if check in (None, "", "all"):
        return payload

    # Filter only the requested section
    filtered = {}
    key_map = {
        "cluster": "clusters",
        "jms": "jmsServers",
        "datasource": "datasources",
        "deployments": "deployments",
        "composites": "composites",
        "managed_servers": "servers",
        "threads": "threads",
    }
    target_key = key_map.get(check)
    if target_key and target_key in payload:
        filtered[target_key] = payload[target_key]
    return filtered or payload


# ---------------------------------------------------------------------------
# WLST helpers
# ---------------------------------------------------------------------------

def normalize_health_state(health):
    if health is None:
        return None
    getter = getattr(health, "getState", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            pass
    return str(health)


def connect_if_available(username, password, admin_url):
    """
    Returns True if we are in WLST and connect() succeeds, else False.
    On connection failure, prints a JSON error and exits.
    """
    if not (username and password and admin_url):
        return False

    connect_fn = globals().get("connect")  # Provided by WLST
    if connect_fn is None:
        return False

    try:
        connect_fn(username, password, admin_url)
        return True
    except Exception:
        exc = sys.exc_info()[1]
        # This will be the only JSON line in WLST output (apart from banner).
        print(json.dumps({"error": "Failed to connect via WLST: %s" % exc}))
        sys.exit(1)


def ensure_domain_runtime():
    """
    Switch WLST into domainRuntime() tree, if available.
    """
    domain_runtime = globals().get("domainRuntime")
    if domain_runtime is None:
        return False
    domain_runtime()
    return True


# ---------------------------------------------------------------------------
# Fetchers (clusters, servers, JMS, threads, datasources, deployments, SOA)
# ---------------------------------------------------------------------------

def fetch_clusters():  # pragma: no cover - WLST environment only
    clusters = {}
    try:
        ensure_domain_runtime()
        runtimes = getattr(globals().get("cmo", None), "getClusterRuntimes", lambda: [])()
        if runtimes:
            for runtime in runtimes:
                name = getattr(runtime, "getName", lambda: None)()
                cluster_info = {
                    "name": name,
                    "state": getattr(runtime, "getState", lambda: None)(),
                    "servers": {},
                }

                # Collect servers belonging to the cluster (if exposed)
                try:
                    server_runtimes = getattr(runtime, "getServerRuntimes", lambda: [])()
                except Exception:
                    server_runtimes = getattr(runtime, "getServers", lambda: [])()

                index = 0
                for server in server_runtimes or []:
                    server_name = getattr(server, "getName", lambda: None)()
                    health = getattr(server, "getHealthState", lambda: None)()
                    key = server_name
                    if not key:
                        key = "server_%d" % index
                    cluster_info["servers"][key] = {
                        "name": server_name,
                        "state": getattr(server, "getState", lambda: None)(),
                        "health": normalize_health_state(health),
                    }
                    index += 1

                key = name or "cluster_%d" % (len(clusters) + 1)
                clusters[key] = cluster_info
    except Exception:
        exc = sys.exc_info()[1]
        key = _get_error_key("clusters")
        clusters[key] = {"name": "ERROR", "state": str(exc)}
    return clusters


def fetch_managed_servers():  # pragma: no cover - WLST environment only
    servers = {}
    try:
        ensure_domain_runtime()
        runtimes = getattr(globals().get("cmo", None), "getServerRuntimes", lambda: [])()
        for runtime in runtimes or []:
            health = getattr(runtime, "getHealthState", lambda: None)()
            heap_runtime = getattr(runtime, "getJVMRuntime", lambda: None)()
            heap_current = None
            heap_max = None
            if heap_runtime:
                try:
                    heap_current = heap_runtime.getHeapSizeCurrent()
                    heap_max = heap_runtime.getHeapSizeMax()
                except Exception:
                    heap_current = None
                    heap_max = None

            name = getattr(runtime, "getName", lambda: None)()
            key = name or "server_%d" % (len(servers) + 1)
            servers[key] = {
                "name": name,
                "state": getattr(runtime, "getState", lambda: None)(),
                "cluster": getattr(runtime, "getClusterName", lambda: None)(),
                "health": normalize_health_state(health),
                "listenAddress": getattr(runtime, "getListenAddress", lambda: None)(),
                "listenPort": getattr(runtime, "getListenPort", lambda: None)(),
                "heapCurrent": heap_current,
                "heapMax": heap_max,
            }
    except Exception:
        exc = sys.exc_info()[1]
        key = _get_error_key("managed_servers")
        servers[key] = {"name": "ERROR", "state": str(exc)}
    return servers


def fetch_threads():  # pragma: no cover - WLST environment only
    thread_pools = {}
    try:
        ensure_domain_runtime()
        runtimes = getattr(globals().get("cmo", None), "getServerRuntimes", lambda: [])()
        for runtime in runtimes or []:
            name = getattr(runtime, "getName", lambda: None)()
            entry = {"server": name}
            try:
                tp = getattr(runtime, "getThreadPoolRuntime", lambda: None)()
            except Exception:
                tp = None
            if tp:
                for attr in [
                    "getExecuteThreadTotalCount",
                    "getExecuteThreadIdleCount",
                    "getStandbyThreadCount",
                    "getHoggingThreadCount",
                    "getQueueLength",
                ]:
                    try:
                        val = getattr(tp, attr, lambda: None)()
                    except Exception:
                        val = None
                    entry[attr[3:]] = val  # strip 'get' prefix for key

                throughput = getattr(tp, "getThroughput", None)
                if callable(throughput):
                    try:
                        entry["throughput"] = throughput()
                    except Exception:
                        entry["throughput"] = None

            key = name or "threadPool_%d" % (len(thread_pools) + 1)
            thread_pools[key] = entry
    except Exception:
        exc = sys.exc_info()[1]
        key = _get_error_key("threads")
        thread_pools[key] = {"server": "ERROR", "state": str(exc)}
    return thread_pools


def fetch_jms_servers():  # pragma: no cover - WLST environment only
    servers = {}
    try:
        ensure_domain_runtime()
        cmo_obj = globals().get("cmo", None)
        jms_runtime = getattr(cmo_obj, "getJMSRuntime", lambda: None)()
        if jms_runtime:
            server_runtimes = getattr(jms_runtime, "getJMSServers", lambda: [])()
        else:
            if hasattr(cmo_obj, "getJMSServers"):
                server_runtimes = cmo_obj.getJMSServers()
            else:
                server_runtimes = []

        for runtime in server_runtimes or []:
            health = getattr(runtime, "getHealthState", lambda: None)()
            destinations = {}
            try:
                destination_runtimes = getattr(runtime, "getDestinations", lambda: [])()
            except Exception:
                destination_runtimes = []

            index = 0
            for dest in destination_runtimes or []:
                dest_name = getattr(dest, "getName", lambda: None)()
                key = dest_name or "destination_%d" % index
                destinations[key] = {
                    "name": dest_name,
                    "type": getattr(dest, "getType", lambda: None)(),
                    "messagesCurrentCount": getattr(dest, "getMessagesCurrentCount", lambda: None)(),
                    "messagesHighCount": getattr(dest, "getMessagesHighCount", lambda: None)(),
                    "consumersCurrentCount": getattr(dest, "getConsumersCurrentCount", lambda: None)(),
                }
                index += 1

            name = getattr(runtime, "getName", lambda: None)()
            key = name or "jmsServer_%d" % (len(servers) + 1)
            servers[key] = {
                "name": name,
                "state": getattr(runtime, "getState", lambda: None)(),
                "health": normalize_health_state(health),
                "destinations": destinations,
            }
    except Exception:
        exc = sys.exc_info()[1]
        key = _get_error_key("jms_servers")
        servers[key] = {"name": "ERROR", "state": str(exc)}
    return servers


def fetch_datasources():  # pragma: no cover - WLST environment only
    datasources = {}
    try:
        ensure_domain_runtime()
        cmo_obj = globals().get("cmo", None)
        service = getattr(cmo_obj, "getJDBCServiceRuntime", lambda: None)()
        if service:
            for runtime in service.getJDBCDataSourceRuntimeMBeans():
                name = runtime.getName()
                key = name or "datasource_%d" % (len(datasources) + 1)
                datasources[key] = {
                    "name": name,
                    "state": runtime.getState(),
                    "activeConnectionsCurrentCount": runtime.getActiveConnectionsCurrentCount(),
                }
    except Exception:
        exc = sys.exc_info()[1]
        key = _get_error_key("datasources")
        datasources[key] = {"name": "ERROR", "state": str(exc)}
    return datasources


def fetch_deployments():  # pragma: no cover - WLST environment only
    deployments = {}
    try:
        ensure_domain_runtime()
        cmo_obj = globals().get("cmo", None)
        runtime = getattr(cmo_obj, "lookupAppRuntimeStateRuntime", lambda: None)()
        if runtime:
            for app in runtime.getAppDeploymentStateRuntimes():
                name = app.getName()
                key = name or "deployment_%d" % (len(deployments) + 1)
                deployments[key] = {
                    "name": name,
                    "state": app.getState(),
                }
    except Exception:
        exc = sys.exc_info()[1]
        key = _get_error_key("deployments")
        deployments[key] = {"name": "ERROR", "state": str(exc)}
    return deployments


def fetch_composites():  # pragma: no cover - WLST environment only
    """
    This assumes there is a helper available in WLST globals called
    'soa_cluster_state' that returns a list of composite dicts.
    """
    composites = {}
    try:
        soa = globals().get("soa_cluster_state")
        if soa:
            composites_list = soa()
        else:
            composites_list = []

        index = 0
        for composite in composites_list:
            if isinstance(composite, dict):
                if "version" not in composite and hasattr(composite, "getRevision"):
                    try:
                        composite["version"] = composite.getRevision()
                    except Exception:
                        composite["version"] = None
                name = composite.get("name")
                partition = composite.get("partition") or composite.get("partitionName")
                key = _composite_key(
                    {"name": name, "partition": partition}
                ) or "composite_%d" % index
            else:
                # Unexpected type; just wrap
                name = None
                key = "composite_%d" % index
            composites[key] = composite
            index += 1
    except Exception:
        exc = sys.exc_info()[1]
        key = _get_error_key("composites")
        composites[key] = {"name": "ERROR", "state": str(exc)}
    return composites


# ---------------------------------------------------------------------------
# Top-level gather + main()
# ---------------------------------------------------------------------------

def gather(check, username, password, admin_url):
    # Prefer sample payload in CPython test mode
    sample_payload = load_sample_payload(check)
    if sample_payload is not None:
        return sample_payload

    if not connect_if_available(username, password, admin_url):
        # WLST not available or connection not attempted; return clear error
        return {"error": "WLST runtime not available and no sample payload supplied"}

    # From here on, we are connected in WLST.
    if check == "cluster":
        payload = {"clusters": fetch_clusters()}
    elif check == "managed_servers":
        payload = {"servers": fetch_managed_servers()}
    elif check == "jms":
        payload = {"jmsServers": fetch_jms_servers()}
    elif check == "threads":
        payload = {"threads": fetch_threads()}
    elif check == "datasource":
        payload = {"datasources": fetch_datasources()}
    elif check == "deployments":
        payload = {"deployments": fetch_deployments()}
    elif check == "composites":
        payload = {"composites": fetch_composites()}
    else:
        # Default: everything
        payload = {
            "clusters": fetch_clusters(),
            "servers": fetch_managed_servers(),
            "jmsServers": fetch_jms_servers(),
            "threads": fetch_threads(),
            "datasources": fetch_datasources(),
            "deployments": fetch_deployments(),
            "composites": fetch_composites(),
        }

    # Extra safety: normalise any remaining list-based structures
    return normalize_collections(payload)


def main():
    # Arguments: check admin_url username password
    if len(sys.argv) > 1:
        check = sys.argv[1]
    else:
        check = "all"

    if len(sys.argv) > 2:
        admin_url = sys.argv[2]
    else:
        admin_url = None

    if len(sys.argv) > 3:
        username = sys.argv[3]
    else:
        username = None

    if len(sys.argv) > 4:
        password = sys.argv[4]
    else:
        password = None

    payload = gather(check, username, password, admin_url) or {}
    if "generatedAt" not in payload:
        payload["generatedAt"] = datetime.utcnow().isoformat() + "Z"
    if "check" not in payload:
        payload["check"] = check

    # Important: print ONLY JSON (no extra text). WLST banner is outside our control.
    print(json.dumps(payload))


if __name__ == "__main__":
    main()
