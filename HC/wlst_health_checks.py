"""
WLST script to output Oracle WebLogic health information as JSON.

This script is designed to be executed via ``wlst.sh`` and prints a
single JSON object to ``stdout``. It supports emitting cluster, JMS,
JDBC datasource, deployment, thread, and SOA composite information.

For local testing without a WebLogic installation you can run the
script with the standard Python interpreter by setting the
``WLST_SAMPLE_OUTPUT`` environment variable to point at a JSON file
that contains sample data.
"""

import os
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# JSON handling with robust fallback (in case json/simplejson are not present)
# ---------------------------------------------------------------------------

def _json_escape_string(s):
    try:
        basestring_type = basestring  # noqa
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
    s = s.replace("\b", "\\b").replace("\f", "\\f")
    s = s.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return "\"" + s + "\""


def _json_dumps_fallback(obj):
    # None
    if obj is None:
        return "null"

    # Bool
    if isinstance(obj, bool):
        return obj and "true" or "false"

    # Int / long
    try:
        integer_types = (int, long)  # noqa
    except NameError:
        integer_types = (int,)
    if isinstance(obj, integer_types):
        return str(obj)

    # Float
    if isinstance(obj, float):
        # Basic repr is fine here
        return str(obj)

    # String
    try:
        basestring_type = basestring  # noqa
    except NameError:
        basestring_type = str
    if isinstance(obj, basestring_type):
        return _json_escape_string(obj)

    # List / tuple
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join([_json_dumps_fallback(x) for x in obj]) + "]"

    # Dict
    if isinstance(obj, dict):
        items = []
        for k, v in obj.items():
            items.append(_json_escape_string(k) + ":" + _json_dumps_fallback(v))
        return "{" + ",".join(items) + "}"

    # Fallback
    return _json_escape_string(obj)


try:
    import json

    def json_dumps(obj):
        return json.dumps(obj)
except ImportError:
    # WLST/Jython environment without json
    def json_dumps(obj):
        return _json_dumps_fallback(obj)


# ---------------------------------------------------------------------------
# Error tracking helpers
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
# Normalization helpers
# ---------------------------------------------------------------------------

def _composite_key_getter(item):
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    if not name:
        return None
    partition = item.get("partition") or item.get("partitionName") or "default"
    return "%s::%s" % (partition, name)


def normalize_collections(value, current_key=None):
    """
    Convert list-based collections into dictionaries keyed by names.
    """
    key_getters = {
        "clusters": lambda item: isinstance(item, dict) and item.get("name") or None,
        "servers": lambda item: isinstance(item, dict) and item.get("name") or None,
        "jmsServers": lambda item: isinstance(item, dict) and item.get("name") or None,
        "destinations": lambda item: isinstance(item, dict) and item.get("name") or None,
        "datasources": lambda item: isinstance(item, dict) and item.get("name") or None,
        "deployments": lambda item: isinstance(item, dict) and item.get("name") or None,
        "composites": _composite_key_getter,
        "threads": lambda item: isinstance(item, dict)
        and (item.get("server") or item.get("name"))
        or None,
    }

    # List -> dict
    if isinstance(value, list):
        if current_key in key_getters:
            getter = key_getters[current_key]
        else:
            def getter(item):
                if isinstance(item, dict):
                    return item.get("name")
                return None

        mapping = {}
        index = 0
        for item in value:
            index += 1
            if isinstance(item, dict):
                key = getter(item)
                if not key:
                    key = "%s_%d" % (current_key or "item", index)
                mapping[key] = normalize_collections(item, current_key)
            else:
                key = "%s_%d" % (current_key or "item", index)
                mapping[key] = item
        return mapping

    # Dict -> recurse into values
    if isinstance(value, dict):
        mapping = {}
        for key, child in value.items():
            mapping[key] = normalize_collections(child, key)
        return mapping

    # Primitive
    return value


# ---------------------------------------------------------------------------
# WLST sample payload loader (for local testing)
# ---------------------------------------------------------------------------

def load_sample_payload(check):
    """
    If WLST_SAMPLE_OUTPUT points to a JSON file, load it instead of using WLST.
    Useful for local/offline testing.
    """
    path = os.environ.get("WLST_SAMPLE_OUTPUT")
    if not path or not os.path.exists(path):
        return None

    try:
        # Try to use real json if available, otherwise our fallback
        payload_text = open(path, "r").read()
        try:
            import json as _real_json
            payload = _real_json.loads(payload_text)
        except Exception:
            # Crude load: only support dicts/lists with strings/numbers
            # For simplicity, just return None if we cannot parse.
            return None
    except Exception:
        return None

    payload = normalize_collections(payload)

    if not check or check == "all":
        return payload

    # Simple filtering based on check type
    filtered = {}
    key_map = {
        "cluster": "clusters",
        "managed_servers": "servers",
        "jms": "jmsServers",
        "datasource": "datasources",
        "deployments": "deployments",
        "composites": "composites",
        "threads": "threads",
    }

    target_key = key_map.get(check)
    if target_key and target_key in payload:
        filtered[target_key] = payload[target_key]
        return filtered

    return payload


# ---------------------------------------------------------------------------
# Helper to convert health state objects to a simple string
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
    # Fallback to str(..)
    try:
        return str(health)
    except Exception:
        return "UNKNOWN"


# ---------------------------------------------------------------------------
# WLST connection helpers
# ---------------------------------------------------------------------------

def connect_if_available(username, password, admin_url):
    """
    Tries to call WLST's connect() if username/password/url are provided.
    Returns True on success, False otherwise. Never raises.
    """
    if not (username and password and admin_url):
        return False

    connect_fn = globals().get("connect")  # Provided by WLST at runtime
    if connect_fn is None:
        return False

    try:
        connect_fn(username, password, admin_url)
        return True
    except Exception:
        return False


def ensure_domain_runtime():
    """
    Calls WLST domainRuntime() if available.
    Returns True if we believe we're in domainRuntime tree, False otherwise.
    """
    domain_runtime = globals().get("domainRuntime")
    if domain_runtime is None:
        return False
    try:
        domain_runtime()
    except Exception:
        return False
    return True


# ---------------------------------------------------------------------------
# Fetchers – executed only when WLST is actually available
# ---------------------------------------------------------------------------

def fetch_clusters():
    clusters = {}
    try:
        if not ensure_domain_runtime():
            raise Exception("domainRuntime() not available")

        runtimes = None
        try:
            runtimes = cmo.getClusterRuntimes()
        except Exception:
            runtimes = None

        if runtimes:
            for runtime in runtimes:
                try:
                    name = None
                    try:
                        name = runtime.getName()
                    except Exception:
                        name = None

                    cluster_info = {
                        "name": name,
                        "state": None,
                        "servers": {},
                    }

                    try:
                        cluster_info["state"] = runtime.getState()
                    except Exception:
                        cluster_info["state"] = None

                    # Servers in the cluster
                    server_runtimes = None
                    try:
                        server_runtimes = runtime.getServerRuntimes()
                    except Exception:
                        try:
                            server_runtimes = runtime.getServers()
                        except Exception:
                            server_runtimes = None

                    if server_runtimes:
                        index = 0
                        for server in server_runtimes:
                            index += 1
                            try:
                                server_name = None
                                try:
                                    server_name = server.getName()
                                except Exception:
                                    server_name = None

                                health = None
                                try:
                                    health = server.getHealthState()
                                except Exception:
                                    health = None

                                key = server_name or "server_%d" % index
                                cluster_info["servers"][key] = {
                                    "name": server_name,
                                    "state": getattr(server, "getState", lambda: None)(),
                                    "health": normalize_health_state(health),
                                }
                            except Exception:
                                # ignore a single bad server entry
                                pass

                    key = name or "cluster_%d" % (len(clusters) + 1)
                    clusters[key] = cluster_info
                except Exception:
                    # one bad cluster shouldn't kill the whole call
                    pass
    except Exception:
        exc = sys.exc_info()[1]
        key = _get_error_key("clusters")
        clusters[key] = {"name": "ERROR", "state": str(exc)}
    return clusters


def fetch_managed_servers():
    servers = {}
    try:
        if not ensure_domain_runtime():
            raise Exception("domainRuntime() not available")

        try:
            runtimes = cmo.getServerRuntimes()
        except Exception:
            runtimes = None

        if runtimes:
            for runtime in runtimes:
                try:
                    health = None
                    try:
                        health = runtime.getHealthState()
                    except Exception:
                        health = None

                    heap_runtime = None
                    try:
                        heap_runtime = runtime.getJVMRuntime()
                    except Exception:
                        heap_runtime = None

                    heap_current = None
                    heap_max = None
                    if heap_runtime:
                        try:
                            heap_current = heap_runtime.getHeapSizeCurrent()
                        except Exception:
                            heap_current = None
                        try:
                            heap_max = heap_runtime.getHeapSizeMax()
                        except Exception:
                            heap_max = None

                    name = None
                    try:
                        name = runtime.getName()
                    except Exception:
                        name = None

                    key = name or "server_%d" % (len(servers) + 1)
                    entry = {
                        "name": name,
                        "state": getattr(runtime, "getState", lambda: None)(),
                        "cluster": getattr(runtime, "getClusterName", lambda: None)(),
                        "health": normalize_health_state(health),
                        "listenAddress": getattr(
                            runtime, "getListenAddress", lambda: None
                        )(),
                        "listenPort": getattr(
                            runtime, "getListenPort", lambda: None
                        )(),
                        "heapCurrent": heap_current,
                        "heapMax": heap_max,
                    }
                    servers[key] = entry
                except Exception:
                    # ignore a single bad server
                    pass
    except Exception:
        exc = sys.exc_info()[1]
        key = _get_error_key("managed_servers")
        servers[key] = {"name": "ERROR", "state": str(exc)}
    return servers


def fetch_threads():
    """
    Basic thread pool metrics per server.
    """
    thread_pools = {}
    try:
        if not ensure_domain_runtime():
            raise Exception("domainRuntime() not available")

        try:
            runtimes = cmo.getServerRuntimes()
        except Exception:
            runtimes = None

        if runtimes:
            for runtime in runtimes:
                try:
                    name = None
                    try:
                        name = runtime.getName()
                    except Exception:
                        name = None

                    tpr = None
                    try:
                        tpr = runtime.getThreadPoolRuntime()
                    except Exception:
                        tpr = None

                    entry = {"server": name}

                    if tpr:
                        def safe_get(mbean, attr, default=None):
                            try:
                                func = getattr(mbean, attr)
                            except Exception:
                                return default
                            try:
                                return func()
                            except Exception:
                                return default

                        entry["executeThreadTotalCount"] = safe_get(
                            tpr, "getExecuteThreadTotalCount"
                        )
                        entry["executeThreadIdleCount"] = safe_get(
                            tpr, "getExecuteThreadIdleCount"
                        )
                        entry["hoggingThreadCount"] = safe_get(
                            tpr, "getHoggingThreadCount"
                        )
                        entry["pendingUserRequestCount"] = safe_get(
                            tpr, "getPendingUserRequestCount"
                        )
                        entry["queueLength"] = safe_get(tpr, "getQueueLength")
                        entry["throughput"] = safe_get(tpr, "getThroughput")

                    key = name or "threadPool_%d" % (len(thread_pools) + 1)
                    thread_pools[key] = entry
                except Exception:
                    # ignore a single bad server
                    pass
    except Exception:
        exc = sys.exc_info()[1]
        key = _get_error_key("threads")
        thread_pools[key] = {"server": "ERROR", "state": str(exc)}
    return thread_pools


def fetch_jms_servers():
    servers = {}
    try:
        if not ensure_domain_runtime():
            raise Exception("domainRuntime() not available")

        jms_runtime = None
        try:
            jms_runtime = cmo.getJMSRuntime()
        except Exception:
            jms_runtime = None

        server_runtimes = None
        if jms_runtime:
            try:
                server_runtimes = jms_runtime.getJMSServers()
            except Exception:
                server_runtimes = None
        elif hasattr(cmo, "getJMSServers"):
            try:
                server_runtimes = cmo.getJMSServers()
            except Exception:
                server_runtimes = None

        if server_runtimes:
            for runtime in server_runtimes:
                try:
                    health = None
                    try:
                        health = runtime.getHealthState()
                    except Exception:
                        health = None

                    destinations = {}

                    destination_runtimes = None
                    try:
                        destination_runtimes = runtime.getDestinations()
                    except Exception:
                        destination_runtimes = None

                    if destination_runtimes:
                        idx = 0
                        for dest in destination_runtimes:
                            idx += 1
                            try:
                                dest_name = None
                                try:
                                    dest_name = dest.getName()
                                except Exception:
                                    dest_name = None

                                key = dest_name or "destination_%d" % idx
                                destinations[key] = {
                                    "name": dest_name,
                                    "type": getattr(dest, "getType", lambda: None)(),
                                    "messagesCurrentCount": getattr(
                                        dest, "getMessagesCurrentCount", lambda: None
                                    )(),
                                    "messagesHighCount": getattr(
                                        dest, "getMessagesHighCount", lambda: None
                                    )(),
                                    "consumersCurrentCount": getattr(
                                        dest,
                                        "getConsumersCurrentCount",
                                        lambda: None,
                                    )(),
                                }
                            except Exception:
                                pass

                    name = None
                    try:
                        name = runtime.getName()
                    except Exception:
                        name = None

                    key = name or "jmsServer_%d" % (len(servers) + 1)
                    servers[key] = {
                        "name": name,
                        "state": getattr(runtime, "getState", lambda: None)(),
                        "health": normalize_health_state(health),
                        "destinations": destinations,
                    }
                except Exception:
                    pass
    except Exception:
        exc = sys.exc_info()[1]
        key = _get_error_key("jms_servers")
        servers[key] = {"name": "ERROR", "state": str(exc)}
    return servers


def fetch_datasources():
    datasources = {}
    try:
        if not ensure_domain_runtime():
            raise Exception("domainRuntime() not available")

        service = None
        try:
            service = cmo.getJDBCServiceRuntime()
        except Exception:
            service = None

        if service:
            for runtime in service.getJDBCDataSourceRuntimeMBeans():
                try:
                    name = None
                    try:
                        name = runtime.getName()
                    except Exception:
                        name = None

                    key = name or "datasource_%d" % (len(datasources) + 1)
                    datasources[key] = {
                        "name": name,
                        "state": getattr(runtime, "getState", lambda: None)(),
                        "activeConnectionsCurrentCount": getattr(
                            runtime,
                            "getActiveConnectionsCurrentCount",
                            lambda: None,
                        )(),
                    }
                except Exception:
                    pass
    except Exception:
        exc = sys.exc_info()[1]
        key = _get_error_key("datasources")
        datasources[key] = {"name": "ERROR", "state": str(exc)}
    return datasources


def fetch_deployments():
    deployments = {}
    try:
        if not ensure_domain_runtime():
            raise Exception("domainRuntime() not available")

        runtime = None
        try:
            runtime = cmo.lookupAppRuntimeStateRuntime()
        except Exception:
            runtime = None

        if runtime:
            for app in runtime.getAppDeploymentStateRuntimes():
                try:
                    name = None
                    try:
                        name = app.getName()
                    except Exception:
                        name = None

                    key = name or "deployment_%d" % (len(deployments) + 1)
                    deployments[key] = {
                        "name": name,
                        "state": getattr(app, "getState", lambda: None)(),
                    }
                except Exception:
                    pass
    except Exception:
        exc = sys.exc_info()[1]
        key = _get_error_key("deployments")
        deployments[key] = {"name": "ERROR", "state": str(exc)}
    return deployments


def fetch_composites():
    """
    Uses custom hook 'soa_cluster_state' if present in WLST globals.
    That hook is expected to return a list of dict-like composite entries.
    """
    composites = {}
    try:
        soa = globals().get("soa_cluster_state")
        if soa:
            try:
                composites_list = soa()
            except Exception:
                composites_list = []
        else:
            composites_list = []

        idx = 0
        for composite in composites_list:
            idx += 1
            try:
                # Expecting dict-like object
                if not isinstance(composite, dict):
                    continue

                # Normalize version if missing but method exists
                if "version" not in composite and hasattr(composite, "getRevision"):
                    try:
                        composite["version"] = composite.getRevision()
                    except Exception:
                        composite["version"] = None

                name = composite.get("name")
                partition = composite.get("partition") or composite.get(
                    "partitionName"
                )

                if name:
                    partition_value = partition or "default"
                    key = "%s::%s" % (partition_value, name)
                else:
                    key = "composite_%d" % idx

                composites[key] = composite
            except Exception:
                pass
    except Exception:
        exc = sys.exc_info()[1]
        key = _get_error_key("composites")
        composites[key] = {"name": "ERROR", "state": str(exc)}
    return composites


# ---------------------------------------------------------------------------
# Gather
# ---------------------------------------------------------------------------

def gather(check, username, password, admin_url):
    """
    Main entry for data collection.
    Returns a dict ready to be JSON-encoded.
    """

    # 1. Sample payload mode (local testing)
    sample_payload = load_sample_payload(check)
    if sample_payload is not None:
        return normalize_collections(sample_payload)

    # 2. Online WLST mode
    if not connect_if_available(username, password, admin_url):
        return {
            "error": "WLST runtime not available or connect() failed and no sample payload supplied"
        }

    # Ensure domainRuntime is available
    if not ensure_domain_runtime():
        return {"error": "domainRuntime() not available after connect()"}

    # Single checks
    if check == "cluster":
        return normalize_collections({"clusters": fetch_clusters()})
    if check == "managed_servers":
        return normalize_collections({"servers": fetch_managed_servers()})
    if check == "jms":
        return normalize_collections({"jmsServers": fetch_jms_servers()})
    if check == "threads":
        return normalize_collections({"threads": fetch_threads()})
    if check == "datasource":
        return normalize_collections({"datasources": fetch_datasources()})
    if check == "deployments":
        return normalize_collections({"deployments": fetch_deployments()})
    if check == "composites":
        return normalize_collections({"composites": fetch_composites()})

    # "all" or unknown -> return everything we know
    payload = {
        "clusters": fetch_clusters(),
        "servers": fetch_managed_servers(),
        "jmsServers": fetch_jms_servers(),
        "threads": fetch_threads(),
        "datasources": fetch_datasources(),
        "deployments": fetch_deployments(),
        "composites": fetch_composites(),
    }
    return normalize_collections(payload)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Default arguments as in your wrapper
    check = "all"
    admin_url = None
    username = None
    password = None

    # Parse command line
    argv = sys.argv[1:]
    if len(argv) >= 1:
        check = argv[0]
    if len(argv) >= 2:
        admin_url = argv[1]
    if len(argv) >= 3:
        username = argv[2]
    if len(argv) >= 4:
        password = argv[3]

    # Always wrap in try/except so we ALWAYS emit JSON
    try:
        payload = gather(check, username, password, admin_url) or {}
    except Exception:
        exc = sys.exc_info()[1]
        payload = {
            "error": "Unhandled exception during WLST healthcheck: %s" % str(exc)
        }

    if "generatedAt" not in payload:
        payload["generatedAt"] = datetime.utcnow().isoformat() + "Z"
    if "check" not in payload:
        payload["check"] = check

    # IMPORTANT: exactly one JSON object to stdout, no extra prints
    sys.stdout.write(json_dumps(payload))
    sys.stdout.write("\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
