"""WLST script to output Oracle WebLogic health information as JSON.

This script is designed to be executed via ``wlst.sh`` and prints a
single JSON object to ``stdout``. It supports emitting cluster, JMS,
JDBC datasource, deployment, thread pool, and SOA composite information.

It is written to be compatible with the WLST Jython version in
WebLogic 12.2.1.4 (no Python 3 features).
"""

# ---------------------------------------------------------------------------
# Imports and JSON fallback
# ---------------------------------------------------------------------------

import sys
import os
from datetime import datetime

# Robust "open" alias (for CPython 3 / local testing)
try:
    from io import open as io_open
except ImportError:
    io_open = open  # WLST / older Jython


# JSON (with failover if json module is not available in WLST)
try:
    import json  # noqa
except ImportError:
    # Try simplejson if present
    try:
        import simplejson as json  # noqa
    except ImportError:
        # Minimal JSON encoder for WLST environments with no json/simplejson
        def _json_escape_string(s):
            if s is None:
                return '""'
            try:
                # Python 2: basestring
                basestring_type = basestring  # noqa
            except NameError:
                basestring_type = str
            if not isinstance(s, basestring_type):
                s = str(s)
            s = s.replace('\\', '\\\\')
            s = s.replace('"', '\\"')
            s = s.replace('\b', '\\b')
            s = s.replace('\f', '\\f')
            s = s.replace('\n', '\\n')
            s = s.replace('\r', '\\r')
            s = s.replace('\t', '\\t')
            return '"' + s + '"'

        def _json_dumps(obj):
            # None
            if obj is None:
                return 'null'

            # Booleans
            try:
                bool_type = bool
            except NameError:
                bool_type = None
            if bool_type is not None and isinstance(obj, bool_type):
                if obj:
                    return 'true'
                return 'false'

            # Integers
            try:
                integer_types = (int, long)  # noqa
            except NameError:
                integer_types = (int,)
            if isinstance(obj, integer_types):
                return str(obj)

            # Floats
            if isinstance(obj, float):
                return str(obj)

            # Strings
            try:
                basestring_type = basestring  # noqa
            except NameError:
                basestring_type = str
            if isinstance(obj, basestring_type):
                return _json_escape_string(obj)

            # Lists / tuples
            if isinstance(obj, (list, tuple)):
                parts = []
                for item in obj:
                    parts.append(_json_dumps(item))
                return '[' + ','.join(parts) + ']'

            # Dicts
            if isinstance(obj, dict):
                items = []
                for k, v in obj.items():
                    items.append(_json_escape_string(k) + ':' + _json_dumps(v))
                return '{' + ','.join(items) + '}'

            # Fallback
            return _json_escape_string(str(obj))

        class _JsonModule(object):
            def dumps(self, obj):
                return _json_dumps(obj)

        json = _JsonModule()  # noqa


# ---------------------------------------------------------------------------
# Error tracking helpers
# ---------------------------------------------------------------------------

_error_counters = {
    'clusters': 0,
    'managed_servers': 0,
    'jms_servers': 0,
    'threads': 0,
    'datasources': 0,
    'deployments': 0,
    'composites': 0,
}


def _get_error_key(check_type):
    """Generate unique error keys for consistent error tracking."""
    if check_type not in _error_counters:
        _error_counters[check_type] = 0
    _error_counters[check_type] = _error_counters[check_type] + 1
    return "%s_error_%d" % (check_type, _error_counters[check_type])


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _composite_key_getter(item):
    """Key function for composites -> partition::name."""
    if not isinstance(item, dict):
        return None
    name = item.get('name')
    if not name:
        return None
    partition = item.get('partition')
    if not partition:
        partition = item.get('partitionName')
    if not partition:
        partition = 'default'
    return partition + "::" + name


def normalize_collections(value, current_key=None):
    """Convert list-based collections into dictionaries keyed by names."""
    key_getters = {
        'clusters': lambda item: item.get('name'),
        'servers': lambda item: item.get('name'),
        'jmsServers': lambda item: item.get('name'),
        'destinations': lambda item: item.get('name'),
        'datasources': lambda item: item.get('name'),
        'deployments': lambda item: item.get('name'),
        'composites': _composite_key_getter,
        'threads': lambda item: item.get('server') or item.get('name'),
    }

    # List -> map
    if isinstance(value, list):
        mapping = {}
        getter = key_getters.get(current_key, None)
        index = 0
        for item in value:
            key = None
            if getter is not None:
                key = getter(item)
            else:
                if isinstance(item, dict):
                    key = item.get('name')
            if not key:
                if current_key:
                    key = "%s_%d" % (current_key, index)
                else:
                    key = "item_%d" % index
            mapping[key] = normalize_collections(item, current_key)
            index = index + 1
        return mapping

    # Dict -> recursively normalise children
    if isinstance(value, dict):
        mapping = {}
        for key, child in value.items():
            mapping[key] = normalize_collections(child, key)
        return mapping

    # Base case
    return value


# ---------------------------------------------------------------------------
# Sample payload (for local CPython testing)
# ---------------------------------------------------------------------------

def load_sample_payload(check):
    """Load sample payload from WLST_SAMPLE_OUTPUT (for local testing).

    In WLST (Jython) if json.load is not available or parsing fails,
    this simply returns None.
    """
    path = os.environ.get('WLST_SAMPLE_OUTPUT')
    if not path or not os.path.exists(path):
        return None

    payload = None
    try:
        # Prefer encoding-aware open if supported
        try:
            handle = io_open(path, 'r', encoding='utf-8')
        except TypeError:
            handle = io_open(path, 'r')
        try:
            if hasattr(json, 'load'):
                payload = json.load(handle)  # type: ignore
            else:
                payload = None
        finally:
            handle.close()
    except Exception:
        payload = None

    if payload is None:
        return None

    payload = normalize_collections(payload)

    if check == 'all' or check is None:
        return payload

    filtered = {}
    key_map = {
        'cluster': 'clusters',
        'jms': 'jmsServers',
        'datasource': 'datasources',
        'deployments': 'deployments',
        'composites': 'composites',
        'managed_servers': 'servers',
        'threads': 'threads',
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
    getter = getattr(health, 'getState', None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            pass
    return str(health)


def connect_if_available(username, password, admin_url):
    """Connect to admin server via WLST if possible."""
    if not (username and password and admin_url):
        return False

    connect_fn = globals().get('connect')  # Provided by WLST at runtime
    if connect_fn is None:
        return False

    try:
        connect_fn(username, password, admin_url)
        return True
    except Exception:
        # Must still emit JSON so wrapper does not fail on decoding
        exc = sys.exc_info()[1]
        error_payload = {
            'error': 'Failed to connect via WLST: %s' % str(exc),
        }
        # Use fallback json.dumps (module or custom)
        try:
            out = json.dumps(error_payload)
        except Exception:
            out = '{"error":"Failed to connect via WLST (and json.dumps failed)"}'
        print(out)
        sys.exit(1)


def ensure_domain_runtime():
    """Ensure domainRuntime() has been called (WLST online)."""
    domain_runtime = globals().get('domainRuntime')
    if domain_runtime is None:
        return False
    domain_runtime()
    return True


# ---------------------------------------------------------------------------
# Fetch functions (WLST online only)
# ---------------------------------------------------------------------------

def fetch_clusters():
    clusters = {}
    try:
        ensure_domain_runtime()
        runtimes = cmo.getClusterRuntimes()
        if runtimes:
            for runtime in runtimes:
                name = getattr(runtime, 'getName', lambda: None)()
                cluster_info = {
                    'name': name,
                    'state': getattr(runtime, 'getState', lambda: None)(),
                    'servers': {},
                }
                # Some WLST versions provide getServerRuntimes; others use getServers
                try:
                    server_runtimes = getattr(runtime, 'getServerRuntimes', lambda: [])()
                except Exception:
                    server_runtimes = getattr(runtime, 'getServers', lambda: [])()
                if server_runtimes:
                    for server in server_runtimes:
                        health = getattr(server, 'getHealthState', lambda: None)()
                        server_name = getattr(server, 'getName', lambda: None)()
                        key = server_name
                        if not key:
                            key = "server_%d" % (len(cluster_info['servers']) + 1)
                        cluster_info['servers'][key] = {
                            'name': server_name,
                            'state': getattr(server, 'getState', lambda: None)(),
                            'health': normalize_health_state(health),
                        }
                key = name
                if not key:
                    key = "cluster_%d" % (len(clusters) + 1)
                clusters[key] = cluster_info
    except Exception:
        exc = sys.exc_info()[1]
        key = _get_error_key('clusters')
        clusters[key] = {'name': 'ERROR', 'state': str(exc)}
    return clusters


def fetch_managed_servers():
    servers = {}
    try:
        ensure_domain_runtime()
        runtimes = cmo.getServerRuntimes()
        if runtimes:
            for runtime in runtimes:
                health = getattr(runtime, 'getHealthState', lambda: None)()
                heap_runtime = getattr(runtime, 'getJVMRuntime', lambda: None)()
                heap_current = None
                heap_max = None
                if heap_runtime:
                    try:
                        heap_current = heap_runtime.getHeapSizeCurrent()
                        heap_max = heap_runtime.getHeapSizeMax()
                    except Exception:
                        heap_current = None
                        heap_max = None

                name = getattr(runtime, 'getName', lambda: None)()
                key = name
                if not key:
                    key = "server_%d" % (len(servers) + 1)

                servers[key] = {
                    'name': name,
                    'state': getattr(runtime, 'getState', lambda: None)(),
                    'cluster': getattr(runtime, 'getClusterName', lambda: None)(),
                    'health': normalize_health_state(health),
                    'listenAddress': getattr(runtime, 'getListenAddress', lambda: None)(),
                    'listenPort': getattr(runtime, 'getListenPort', lambda: None)(),
                    'heapCurrent': heap_current,
                    'heapMax': heap_max,
                }
    except Exception:
        exc = sys.exc_info()[1]
        key = _get_error_key('managed_servers')
        servers[key] = {'name': 'ERROR', 'state': str(exc)}
    return servers


def fetch_threads():
    thread_pools = {}
    try:
        ensure_domain_runtime()
        runtimes = cmo.getServerRuntimes()
        if runtimes:
            for runtime in runtimes:
                name = getattr(runtime, 'getName', lambda: None)()
                entry = {
                    'server': name,
                    'state': getattr(runtime, 'getState', lambda: None)(),
                }
                # Main thread pool
                pool = getattr(runtime, 'getThreadPoolRuntime', lambda: None)()
                if pool:
                    for attr in [
                        'getExecuteThreadTotalCount',
                        'getExecuteThreadIdleCount',
                        'getPendingUserRequestCount',
                        'getQueueLength',
                    ]:
                        getter = getattr(pool, attr, None)
                        if callable(getter):
                            try:
                                value = getter()
                            except Exception:
                                value = None
                            entry[attr.replace('get', '', 1)] = value
                # Throughput if available
                throughput = getattr(runtime, 'getThroughput', None)
                if callable(throughput):
                    try:
                        entry['throughput'] = throughput()
                    except Exception:
                        entry['throughput'] = None

                key = name
                if not key:
                    key = "threadPool_%d" % (len(thread_pools) + 1)
                thread_pools[key] = entry
    except Exception:
        exc = sys.exc_info()[1]
        key = _get_error_key('threads')
        thread_pools[key] = {'server': 'ERROR', 'state': str(exc)}
    return thread_pools


def fetch_jms_servers():
    servers = {}
    try:
        ensure_domain_runtime()
        jms_runtime = getattr(cmo, 'getJMSRuntime', lambda: None)()
        server_runtimes = []
        if jms_runtime:
            server_runtimes = getattr(jms_runtime, 'getJMSServers', lambda: [])()
        elif hasattr(cmo, 'getJMSServers'):
            server_runtimes = cmo.getJMSServers()

        if server_runtimes:
            for runtime in server_runtimes:
                health = getattr(runtime, 'getHealthState', lambda: None)()
                destinations = {}
                # Destinations
                try:
                    destination_runtimes = getattr(runtime, 'getDestinations', lambda: [])()
                except Exception:
                    destination_runtimes = []
                if destination_runtimes:
                    index = 0
                    for dest in destination_runtimes:
                        dest_name = getattr(dest, 'getName', lambda: None)()
                        key = dest_name
                        if not key:
                            key = "destination_%d" % (len(destinations) + 1)
                        destinations[key] = {
                            'name': dest_name,
                            'type': getattr(dest, 'getType', lambda: None)(),
                            'messagesCurrentCount': getattr(dest, 'getMessagesCurrentCount', lambda: None)(),
                            'messagesHighCount': getattr(dest, 'getMessagesHighCount', lambda: None)(),
                            'consumersCurrentCount': getattr(dest, 'getConsumersCurrentCount', lambda: None)(),
                        }
                        index = index + 1

                name = getattr(runtime, 'getName', lambda: None)()
                key = name
                if not key:
                    key = "jmsServer_%d" % (len(servers) + 1)

                servers[key] = {
                    'name': name,
                    'state': getattr(runtime, 'getState', lambda: None)(),
                    'health': normalize_health_state(health),
                    'destinations': destinations,
                }
    except Exception:
        exc = sys.exc_info()[1]
        key = _get_error_key('jms_servers')
        servers[key] = {'name': 'ERROR', 'state': str(exc)}
    return servers


def fetch_datasources():
    datasources = {}
    try:
        ensure_domain_runtime()
        service = cmo.getJDBCServiceRuntime()
        if service:
            for runtime in service.getJDBCDataSourceRuntimeMBeans():
                name = runtime.getName()
                key = name
                if not key:
                    key = "datasource_%d" % (len(datasources) + 1)
                datasources[key] = {
                    'name': name,
                    'state': runtime.getState(),
                    'activeConnectionsCurrentCount': runtime.getActiveConnectionsCurrentCount(),
                }
    except Exception:
        exc = sys.exc_info()[1]
        key = _get_error_key('datasources')
        datasources[key] = {'name': 'ERROR', 'state': str(exc)}
    return datasources


def fetch_deployments():
    deployments = {}
    try:
        ensure_domain_runtime()
        runtime = cmo.lookupAppRuntimeStateRuntime()
        if runtime:
            for app in runtime.getAppDeploymentStateRuntimes():
                name = app.getName()
                key = name
                if not key:
                    key = "deployment_%d" % (len(deployments) + 1)
                deployments[key] = {
                    'name': name,
                    'state': app.getState(),
                }
    except Exception:
        exc = sys.exc_info()[1]
        key = _get_error_key('deployments')
        deployments[key] = {'name': 'ERROR', 'state': str(exc)}
    return deployments


def fetch_composites():
    """Fetch SOA composites.

    This expects a helper callable 'soa_cluster_state' to be present in
    globals() when running in a SOA-enabled domain. If not present, an
    empty dict is returned.
    """
    composites = {}
    try:
        soa = globals().get('soa_cluster_state')
        if soa:
            composites_list = soa()  # Custom hook provided by user WLST scripts
        else:
            composites_list = []

        if composites_list:
            for composite in composites_list:
                # composite is expected to be a dict-like structure
                if isinstance(composite, dict):
                    if 'version' not in composite and hasattr(composite, 'getRevision'):
                        try:
                            composite['version'] = composite.getRevision()
                        except Exception:
                            composite['version'] = None
                    name = composite.get('name')
                    partition = composite.get('partition')
                    if not partition:
                        partition = composite.get('partitionName')
                    if not partition:
                        partition = 'default'
                    if name:
                        key = partition + "::" + name
                    else:
                        key = "composite_%d" % (len(composites) + 1)
                    composites[key] = composite
    except Exception:
        exc = sys.exc_info()[1]
        key = _get_error_key('composites')
        composites[key] = {'name': 'ERROR', 'state': str(exc)}
    return composites


# ---------------------------------------------------------------------------
# Top-level gather
# ---------------------------------------------------------------------------

def gather(check, username, password, admin_url):
    # 1) Sample payload (local CPython testing)
    sample_payload = load_sample_payload(check)
    if sample_payload is not None:
        return normalize_collections(sample_payload)

    # 2) WLST connection
    if not connect_if_available(username, password, admin_url):
        return normalize_collections(
            {'error': 'WLST runtime not available and no sample payload supplied'}
        )

    # 3) Dispatch based on "check"
    if check == 'cluster':
        return normalize_collections({'clusters': fetch_clusters()})
    if check == 'managed_servers':
        return normalize_collections({'servers': fetch_managed_servers()})
    if check == 'jms':
        return normalize_collections({'jmsServers': fetch_jms_servers()})
    if check == 'threads':
        return normalize_collections({'threads': fetch_threads()})
    if check == 'datasource':
        return normalize_collections({'datasources': fetch_datasources()})
    if check == 'deployments':
        return normalize_collections({'deployments': fetch_deployments()})
    if check == 'composites':
        return normalize_collections({'composites': fetch_composites()})

    # 4) Default: everything
    return normalize_collections(
        {
            'clusters': fetch_clusters(),
            'servers': fetch_managed_servers(),
            'jmsServers': fetch_jms_servers(),
            'threads': fetch_threads(),
            'datasources': fetch_datasources(),
            'deployments': fetch_deployments(),
            'composites': fetch_composites(),
        }
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    # Arguments: check, admin_url, username, password
    check = 'all'
    if len(sys.argv) > 1:
        check = sys.argv[1]

    admin_url = None
    if len(sys.argv) > 2:
        admin_url = sys.argv[2]

    username = None
    if len(sys.argv) > 3:
        username = sys.argv[3]

    password = None
    if len(sys.argv) > 4:
        password = sys.argv[4]

    payload = gather(check, username, password, admin_url) or {}
    if 'generatedAt' not in payload:
        try:
            payload['generatedAt'] = datetime.utcnow().isoformat() + 'Z'
        except Exception:
            payload['generatedAt'] = ''

    if 'check' not in payload:
        payload['check'] = check

    try:
        out = json.dumps(payload)
    except Exception:
        # Last-resort fallback
        out = '{"error":"Failed to encode payload as JSON"}'
    print(out)


if __name__ == '__main__':
    main()
