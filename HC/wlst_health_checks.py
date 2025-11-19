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

# ---------------------------------------------------------------------------
# JSON import with failover
# ---------------------------------------------------------------------------
try:
    import json
except ImportError:
    try:
        import simplejson as json  # fallback if stdlib json not available
    except ImportError:
        # Extremely small JSON encoder (only `dumps`) as last-resort
        def _json_escape_string(s):
            try:
                basestring_type = basestring
            except NameError:
                basestring_type = str

            if s is None:
                return '""'

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

            # Bool
            if obj is True:
                return 'true'
            if obj is False:
                return 'false'

            # Integers / floats
            try:
                integer_types = (int, long)
            except NameError:
                integer_types = (int,)

            if isinstance(obj, integer_types):
                return str(obj)
            if isinstance(obj, float):
                return str(obj)

            # Strings
            try:
                basestring_type = basestring
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
                parts = []
                for k, v in obj.items():
                    parts.append(_json_escape_string(k) + ':' + _json_dumps(v))
                return '{' + ','.join(parts) + '}'

            # Fallback
            return _json_escape_string(str(obj))

        class _JsonModule(object):
            def dumps(self, obj):
                return _json_dumps(obj)

        json = _JsonModule()

# ---------------------------------------------------------------------------
# Standard imports
# ---------------------------------------------------------------------------
import os
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# Error tracking for consistent exception key generation
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
    count = _error_counters.get(check_type, 0) + 1
    _error_counters[check_type] = count
    return "%s_error_%d" % (check_type, count)


# ---------------------------------------------------------------------------
# Collection normalisation helpers
# ---------------------------------------------------------------------------
def _composite_key_getter(item):
    """Build a stable key for composites: partition::name."""
    if not isinstance(item, dict):
        return None
    name = item.get('name')
    if not name:
        return None
    partition = item.get('partition') or item.get('partitionName') or 'default'
    return "%s::%s" % (partition, name)


def normalize_collections(value, current_key=None):
    """
    Convert list-based collections into dictionaries keyed by names.

    This avoids JSON arrays at top-level collections and gives stable keys.
    """
    key_getters = {
        'clusters': lambda item: isinstance(item, dict) and item.get('name') or None,
        'servers': lambda item: isinstance(item, dict) and item.get('name') or None,
        'jmsServers': lambda item: isinstance(item, dict) and item.get('name') or None,
        'destinations': lambda item: isinstance(item, dict) and item.get('name') or None,
        'datasources': lambda item: isinstance(item, dict) and item.get('name') or None,
        'deployments': lambda item: isinstance(item, dict) and item.get('name') or None,
        'composites': _composite_key_getter,
        'threads': lambda item: isinstance(item, dict) and (item.get('server') or item.get('name')) or None,
    }

    # List → dict keyed by name or generated key
    if isinstance(value, list):
        def _default_getter(item):
            if isinstance(item, dict):
                return item.get('name')
            return None

        getter = key_getters.get(current_key, _default_getter)
        mapping = {}
        index = 0
        for item in value:
            key = getter(item)
            if not key:
                key_base = current_key or 'item'
                key = "%s_%d" % (key_base, index)
            if isinstance(item, dict):
                mapping[key] = normalize_collections(item, current_key)
            else:
                mapping[key] = item
            index += 1
        return mapping

    # Dict → recurse on children
    if isinstance(value, dict):
        mapping = {}
        for key, child in value.items():
            mapping[key] = normalize_collections(child, key)
        return mapping

    # Primitive values
    return value


# ---------------------------------------------------------------------------
# Sample payload support (for local testing)
# ---------------------------------------------------------------------------
def load_sample_payload(check):
    path = os.environ.get('WLST_SAMPLE_OUTPUT')
    if not path or not os.path.exists(path):
        return None

    # If our json module does not support `load`, just skip sample.
    if not hasattr(json, 'load'):
        return None

    try:
        f = open(path, 'r')
        try:
            payload = json.load(f)
        finally:
            f.close()
    except TypeError:
        # Some json implementations don't accept encoding keyword, etc.
        f = open(path, 'r')
        try:
            payload = json.load(f)
        finally:
            f.close()

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
    if target_key and isinstance(payload, dict) and payload.has_key(target_key):
        filtered[target_key] = payload[target_key]
        return filtered

    return payload


# ---------------------------------------------------------------------------
# WLST helpers
# ---------------------------------------------------------------------------
def normalize_health_state(health):
    """Return the raw health state string if available, else str(health)."""
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
    """Try to connect via WLST if connect() is available."""
    if not (username and password and admin_url):
        return False

    connect_fn = globals().get('connect')  # Provided by WLST at runtime
    if connect_fn is None:
        return False

    try:
        connect_fn(username, password, admin_url)
        return True
    except Exception, exc:
        try:
            error_payload = {'error': 'Failed to connect via WLST: %s' % exc}
            print json.dumps(error_payload)
        except Exception:
            # Last resort: plain print
            print 'Failed to connect via WLST: %s' % exc
        sys.exit(1)


def ensure_domain_runtime():
    """Switch WLST to domainRuntime tree if possible."""
    domain_runtime = globals().get('domainRuntime')
    if domain_runtime is None:
        return False
    domain_runtime()
    return True


# ---------------------------------------------------------------------------
# Fetch functions (each wrapped in try/except to avoid blowing up)
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

                # getServerRuntimes vs getServers depending on WLS version
                try:
                    server_runtimes = getattr(runtime, 'getServerRuntimes', lambda: [])()
                except Exception:
                    server_runtimes = getattr(runtime, 'getServers', lambda: [])()

                if server_runtimes is None:
                    server_runtimes = []

                for server in server_runtimes:
                    health = getattr(server, 'getHealthState', lambda: None)()
                    server_name = getattr(server, 'getName', lambda: None)()
                    key = server_name
                    if not key:
                        key = 'server_%d' % (len(cluster_info['servers']) + 1)
                    cluster_info['servers'][key] = {
                        'name': server_name,
                        'state': getattr(server, 'getState', lambda: None)(),
                        'health': normalize_health_state(health),
                    }

                key = name
                if not key:
                    key = 'cluster_%d' % (len(clusters) + 1)
                clusters[key] = cluster_info
    except Exception, exc:
        key = _get_error_key('clusters')
        clusters[key] = {'name': 'ERROR', 'state': str(exc)}
    return clusters


def fetch_managed_servers():
    servers = {}
    try:
        ensure_domain_runtime()
        runtimes = cmo.getServerRuntimes()
        if not runtimes:
            runtimes = []
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
                key = 'server_%d' % (len(servers) + 1)

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
    except Exception, exc:
        key = _get_error_key('managed_servers')
        servers[key] = {'name': 'ERROR', 'state': str(exc)}
    return servers


def fetch_threads():
    thread_pools = {}
    try:
        ensure_domain_runtime()
        runtimes = cmo.getServerRuntimes()
        if not runtimes:
            runtimes = []
        for runtime in runtimes:
            server_name = getattr(runtime, 'getName', lambda: None)()
            pool = getattr(runtime, 'getThreadPoolRuntime', lambda: None)()
            entry = {
                'server': server_name,
            }
            if pool:
                entry['executeThreadTotalCount'] = getattr(pool, 'getExecuteThreadTotalCount', lambda: None)()
                entry['executeThreadIdleCount'] = getattr(pool, 'getExecuteThreadIdleCount', lambda: None)()
                entry['hoggingThreadCount'] = getattr(pool, 'getHoggingThreadCount', lambda: None)()
                entry['pendingUserRequestCount'] = getattr(pool, 'getPendingUserRequestCount', lambda: None)()
                throughput = getattr(pool, 'getThroughput', None)
                if callable(throughput):
                    try:
                        entry['throughput'] = throughput()
                    except Exception:
                        entry['throughput'] = None
            key = server_name
            if not key:
                key = 'threadPool_%d' % (len(thread_pools) + 1)
            thread_pools[key] = entry
    except Exception, exc:
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

        if not server_runtimes:
            server_runtimes = []

        for runtime in server_runtimes:
            health = getattr(runtime, 'getHealthState', lambda: None)()
            destinations = {}

            try:
                destination_runtimes = getattr(runtime, 'getDestinations', lambda: [])()
            except Exception:
                destination_runtimes = []

            if not destination_runtimes:
                destination_runtimes = []

            for dest in destination_runtimes:
                dest_name = getattr(dest, 'getName', lambda: None)()
                key = dest_name
                if not key:
                    key = 'destination_%d' % (len(destinations) + 1)
                destinations[key] = {
                    'name': dest_name,
                    'type': getattr(dest, 'getType', lambda: None)(),
                    'messagesCurrentCount': getattr(dest, 'getMessagesCurrentCount', lambda: None)(),
                    'messagesHighCount': getattr(dest, 'getMessagesHighCount', lambda: None)(),
                    'consumersCurrentCount': getattr(dest, 'getConsumersCurrentCount', lambda: None)(),
                }

            name = getattr(runtime, 'getName', lambda: None)()
            key = name
            if not key:
                key = 'jmsServer_%d' % (len(servers) + 1)
            servers[key] = {
                'name': name,
                'state': getattr(runtime, 'getState', lambda: None)(),
                'health': normalize_health_state(health),
                'destinations': destinations,
            }
    except Exception, exc:
        key = _get_error_key('jms_servers')
        servers[key] = {'name': 'ERROR', 'state': str(exc)}
    return servers


def fetch_datasources():
    datasources = {}
    try:
        ensure_domain_runtime()
        service = cmo.getJDBCServiceRuntime()
        if service:
            runtimes = service.getJDBCDataSourceRuntimeMBeans()
            if not runtimes:
                runtimes = []
            for runtime in runtimes:
                name = runtime.getName()
                key = name
                if not key:
                    key = 'datasource_%d' % (len(datasources) + 1)
                datasources[key] = {
                    'name': name,
                    'state': runtime.getState(),
                    'activeConnectionsCurrentCount': runtime.getActiveConnectionsCurrentCount(),
                }
    except Exception, exc:
        key = _get_error_key('datasources')
        datasources[key] = {'name': 'ERROR', 'state': str(exc)}
    return datasources


def fetch_deployments():
    deployments = {}
    try:
        ensure_domain_runtime()
        runtime = cmo.lookupAppRuntimeStateRuntime()
        if runtime:
            apps = runtime.getAppDeploymentStateRuntimes()
            if not apps:
                apps = []
            for app in apps:
                name = app.getName()
                key = name
                if not key:
                    key = 'deployment_%d' % (len(deployments) + 1)
                deployments[key] = {
                    'name': name,
                    'state': app.getState(),
                }
    except Exception, exc:
        key = _get_error_key('deployments')
        deployments[key] = {'name': 'ERROR', 'state': str(exc)}
    return deployments


def fetch_composites():
    """
    Fetch SOA composites using a custom WLST helper `soa_cluster_state` if present.

    Customer is expected to define `soa_cluster_state()` in WLST environment
    that returns a list of dictionaries describing composites.
    """
    composites = {}
    try:
        soa = globals().get('soa_cluster_state')
        if soa:
            composites_list = soa()
        else:
            composites_list = []

        if not composites_list:
            composites_list = []

        for composite in composites_list:
            # Ensure dict-like
            if not isinstance(composite, dict):
                continue

            # Ensure we have a 'version' field if possible
            if (not composite.has_key('version')) and hasattr(composite, 'getRevision'):
                try:
                    composite['version'] = composite.getRevision()
                except Exception:
                    composite['version'] = None

            name = composite.get('name')
            partition = composite.get('partition') or composite.get('partitionName')
            if partition is None:
                partition = 'default'

            if name:
                key = "%s::%s" % (partition, name)
            else:
                key = "composite_%d" % (len(composites) + 1)

            composites[key] = composite
    except Exception, exc:
        key = _get_error_key('composites')
        composites[key] = {'name': 'ERROR', 'state': str(exc)}
    return composites


# ---------------------------------------------------------------------------
# Gather / main
# ---------------------------------------------------------------------------
def gather(check, username, password, admin_url):
    # 1) Try sample payload if configured
    sample_payload = load_sample_payload(check)
    if sample_payload is not None and sample_payload != {}:
        return normalize_collections(sample_payload)

    # 2) Try live WLST connection if possible
    if not connect_if_available(username, password, admin_url):
        return normalize_collections(
            {'error': 'WLST runtime not available and no sample payload supplied'}
        )

    # 3) Execute requested check
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

    # Default: everything
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


def main():
    # Command line:
    #   wlst.sh wlst_health_checks.py [check] [admin_url] [user] [password]
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
    if not payload.has_key('generatedAt'):
        payload['generatedAt'] = datetime.utcnow().isoformat() + 'Z'
    if not payload.has_key('check'):
        payload['check'] = check

    # Use plain print statement for Python 2 / Jython compatibility
    print json.dumps(payload)


if __name__ == '__main__':
    main()
