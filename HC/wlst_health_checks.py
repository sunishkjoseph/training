from __future__ import print_function

"""WLST script to output Oracle WebLogic health information as JSON.

This script is designed to be executed via ``wlst.sh`` and prints a
single JSON object to ``stdout``. It supports emitting cluster, JMS,
JDBC datasource, deployment, and SOA composite information.

For local testing without a WebLogic installation you can run the
script with the standard Python interpreter by setting the
``WLST_SAMPLE_OUTPUT`` environment variable to point at a JSON file
that contains sample data.
"""

import json
import os
import sys
from datetime import datetime

try:
    # Python 2 / Jython friendly "open" that can accept encoding on newer runtimes
    from io import open as io_open
except ImportError:  # pragma: no cover
    io_open = open

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
    _error_counters[check_type] = _error_counters.get(check_type, 0) + 1
    return "{0}_error_{1}".format(check_type, _error_counters[check_type])


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _composite_key_getter(item):
    """Generate key for composites as partition::name."""
    if not isinstance(item, dict):
        return None
    name = item.get('name')
    if not name:
        return None
    partition = item.get('partition') or item.get('partitionName') or 'default'
    return "{0}::{1}".format(partition, name)


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

    if isinstance(value, list):
        def _default_getter(item):
            if isinstance(item, dict):
                return item.get('name')
            return None

        getter = key_getters.get(current_key, _default_getter)
        mapping = {}
        for index, item in enumerate(value):
            if isinstance(item, dict):
                key = getter(item)
                if not key:
                    key = "{0}_{1}".format(current_key or 'item', index)
                mapping[key] = normalize_collections(item, current_key)
            else:
                mapping["{0}_{1}".format(current_key or 'item', index)] = item
        return mapping

    if isinstance(value, dict):
        mapping = {}
        for key, child in value.items():
            mapping[key] = normalize_collections(child, key)
        return mapping

    return value


# ---------------------------------------------------------------------------
# Sample payload loader
# ---------------------------------------------------------------------------

def load_sample_payload(check):
    """Load sample payload from WLST_SAMPLE_OUTPUT, optionally filter by check."""
    path = os.environ.get('WLST_SAMPLE_OUTPUT')
    if not path or not os.path.exists(path):
        return None

    # Avoid "with" so we work on very old Jython as well
    try:
        try:
            handle = io_open(path, 'r', encoding='utf-8')
        except TypeError:  # encoding not supported
            handle = io_open(path, 'r')
        try:
            payload = json.load(handle)
        finally:
            handle.close()
    except Exception:
        # If sample cannot be loaded, behave as if it doesn't exist
        return None

    payload = normalize_collections(payload)

    if check == 'all' or check is None:
        return payload

    # Filter specific section from sample JSON
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
# Health / WLST helpers
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
    """Try to connect via WLST; return False if no WLST / connect() available."""
    if not (username and password and admin_url):
        return False

    connect_fn = globals().get('connect')  # Provided by WLST at runtime
    if connect_fn is None:
        return False

    try:
        connect_fn(username, password, admin_url)
        return True
    except Exception:
        exc = sys.exc_info()[1]
        print(json.dumps({'error': 'Failed to connect via WLST: {0}'.format(exc)}))
        sys.exit(1)


def ensure_domain_runtime():
    """Call domainRuntime() if available; return True if runtime is ready."""
    domain_runtime = globals().get('domainRuntime')
    if domain_runtime is None:
        return False
    domain_runtime()
    return True


# ---------------------------------------------------------------------------
# Fetchers (WLST only)
# ---------------------------------------------------------------------------

def fetch_clusters():  # pragma: no cover - WLST environment only
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
                try:
                    server_runtimes = getattr(runtime, 'getServerRuntimes', lambda: [])()
                except Exception:  # Some WLST versions expose getServers instead
                    server_runtimes = getattr(runtime, 'getServers', lambda: [])()

                for server in server_runtimes or []:
                    health = getattr(server, 'getHealthState', lambda: None)()
                    server_name = getattr(server, 'getName', lambda: None)()
                    key = server_name or 'server_{0}'.format(len(cluster_info['servers']) + 1)
                    cluster_info['servers'][key] = {
                        'name': server_name,
                        'state': getattr(server, 'getState', lambda: None)(),
                        'health': normalize_health_state(health),
                    }

                key = name or 'cluster_{0}'.format(len(clusters) + 1)
                clusters[key] = cluster_info
    except Exception:
        exc = sys.exc_info()[1]
        key = _get_error_key('clusters')
        clusters[key] = {'name': 'ERROR', 'state': str(exc)}
    return clusters


def fetch_managed_servers():  # pragma: no cover - WLST environment only
    servers = {}
    try:
        ensure_domain_runtime()
        runtimes = cmo.getServerRuntimes()
        for runtime in runtimes or []:
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

            name = None
            if hasattr(runtime, 'getName'):
                name = runtime.getName()

            key = name or 'server_{0}'.format(len(servers) + 1)
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


def fetch_threads():  # pragma: no cover - WLST environment only
    thread_pools = {}
    try:
        ensure_domain_runtime()
        runtimes = cmo.getServerRuntimes()
        for runtime in runtimes or []:
            name = getattr(runtime, 'getName', lambda: None)()
            # Newer WLST: getThreadPoolRuntime(), older: getExecuteQueueRuntimes()
            pool_runtimes = []
            if hasattr(runtime, 'getThreadPoolRuntime'):
                tp = runtime.getThreadPoolRuntime()
                if tp:
                    pool_runtimes = [tp]
            elif hasattr(runtime, 'getExecuteQueueRuntimes'):
                pool_runtimes = runtime.getExecuteQueueRuntimes()

            for thread_runtime in pool_runtimes or []:
                entry = {
                    'server': name,
                    'name': getattr(thread_runtime, 'getName', lambda: None)(),
                    'pendingRequests': getattr(thread_runtime, 'getPendingUserRequestCount', lambda: None)(),
                    'completedRequests': getattr(thread_runtime, 'getCompletedRequestCount', lambda: None)(),
                    'threadCount': getattr(thread_runtime, 'getExecuteThreadTotalCount', lambda: None)(),
                    'hoggingThreadCount': getattr(thread_runtime, 'getHoggingThreadCount', lambda: None)(),
                    'stuckThreadCount': getattr(thread_runtime, 'getStuckThreadCount', lambda: None)(),
                }
                throughput = getattr(thread_runtime, 'getThroughput', None)
                if callable(throughput):
                    try:
                        entry['throughput'] = throughput()
                    except Exception:
                        entry['throughput'] = None
                key = name or 'threadPool_{0}'.format(len(thread_pools) + 1)
                thread_pools[key] = entry
    except Exception:
        exc = sys.exc_info()[1]
        key = _get_error_key('threads')
        thread_pools[key] = {'server': 'ERROR', 'state': str(exc)}
    return thread_pools


def fetch_jms_servers():  # pragma: no cover - WLST environment only
    servers = {}
    try:
        ensure_domain_runtime()
        jms_runtime = getattr(cmo, 'getJMSRuntime', lambda: None)()
        server_runtimes = []
        if jms_runtime:
            server_runtimes = getattr(jms_runtime, 'getJMSServers', lambda: [])()
        elif hasattr(cmo, 'getJMSServers'):
            server_runtimes = cmo.getJMSServers()

        for runtime in server_runtimes or []:
            health = getattr(runtime, 'getHealthState', lambda: None)()
            destinations = {}
            try:
                destination_runtimes = getattr(runtime, 'getDestinations', lambda: [])()
            except Exception:
                destination_runtimes = []

            for dest in destination_runtimes or []:
                dest_name = getattr(dest, 'getName', lambda: None)()
                key = dest_name or 'destination_{0}'.format(len(destinations) + 1)
                destinations[key] = {
                    'name': dest_name,
                    'type': getattr(dest, 'getType', lambda: None)(),
                    'messagesCurrentCount': getattr(dest, 'getMessagesCurrentCount', lambda: None)(),
                    'messagesHighCount': getattr(dest, 'getMessagesHighCount', lambda: None)(),
                    'consumersCurrentCount': getattr(dest, 'getConsumersCurrentCount', lambda: None)(),
                }

            name = getattr(runtime, 'getName', lambda: None)()
            key = name or 'jmsServer_{0}'.format(len(servers) + 1)
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


def fetch_datasources():  # pragma: no cover - WLST environment only
    datasources = {}
    try:
        ensure_domain_runtime()
        service = cmo.getJDBCServiceRuntime()
        if service:
            for runtime in service.getJDBCDataSourceRuntimeMBeans():
                name = runtime.getName()
                key = name or 'datasource_{0}'.format(len(datasources) + 1)
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


def fetch_deployments():  # pragma: no cover - WLST environment only
    deployments = {}
    try:
        ensure_domain_runtime()
        runtime = cmo.lookupAppRuntimeStateRuntime()
        if runtime:
            for app in runtime.getAppDeploymentStateRuntimes():
                name = app.getName()
                key = name or 'deployment_{0}'.format(len(deployments) + 1)
                deployments[key] = {
                    'name': name,
                    'state': app.getState(),
                }
    except Exception:
        exc = sys.exc_info()[1]
        key = _get_error_key('deployments')
        deployments[key] = {'name': 'ERROR', 'state': str(exc)}
    return deployments


def fetch_composites():  # pragma: no cover - WLST environment only
    """Fetch SOA composite state via a custom soa_cluster_state() hook."""
    composites = {}
    try:
        soa = globals().get('soa_cluster_state')
        if soa:
            composites_list = soa()  # custom hook provided by customer scripts
        else:
            composites_list = []

        for composite in composites_list:
            # Try to fill version if not present
            if 'version' not in composite and hasattr(composite, 'getRevision'):
                try:
                    composite['version'] = composite.getRevision()
                except Exception:
                    composite['version'] = None

            name = composite.get('name')
            partition = composite.get('partition') or composite.get('partitionName')
            if name:
                key = "{0}::{1}".format(partition or 'default', name)
            else:
                key = "composite_{0}".format(len(composites) + 1)
            composites[key] = composite
    except Exception:
        exc = sys.exc_info()[1]
        key = _get_error_key('composites')
        composites[key] = {'name': 'ERROR', 'state': str(exc)}
    return composites


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def gather(check, username, password, admin_url):
    """Gather requested health information."""
    # 1) Try sample payload if provided
    sample_payload = load_sample_payload(check)
    if sample_payload is not None and sample_payload:
        return normalize_collections(sample_payload)

    # 2) Try live WLST connection
    if not connect_if_available(username, password, admin_url):
        return normalize_collections(
            {'error': 'WLST runtime not available and no sample payload supplied'}
        )

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

    # "all" / default case
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
    # Arg layout (WLST): wlst.sh wlst_health_checks.py [check] [admin_url] [username] [password]
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
        payload['generatedAt'] = datetime.utcnow().isoformat() + 'Z'
    payload.setdefault('check', check)

    print(json.dumps(payload))


if __name__ == '__main__':
    main()
