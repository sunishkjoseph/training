from __future__ import print_function

"""WLST script to output Oracle WebLogic health information as JSON.

This script is designed to be executed via ``wlst.sh`` and prints a
single JSON object to ``stdout``. It supports emitting cluster, JMS,
JDBC datasource, deployment, and SOA composite information.

For local testing without a WebLogic installation you can run the
script with the standard Python interpreter by setting the
``WLST_SAMPLE_OUTPUT`` environment variable to point at a JSON file
that contains sample data. ``middleware_healthcheck.py`` wires this up
automatically when the ``--wlst-sample-output`` option is supplied.
"""

import json
import os
import sys
from datetime import datetime

try:
    from io import open as io_open
except ImportError:  # pragma: no cover - Python 2 / Jython fallback
    io_open = open


def normalize_collections(value, current_key=None):
    """Convert list-based collections into dictionaries keyed by names."""

    key_getters = {
        'clusters': lambda item: item.get('name'),
        'servers': lambda item: item.get('name'),
        'jmsServers': lambda item: item.get('name'),
        'destinations': lambda item: item.get('name'),
        'datasources': lambda item: item.get('name'),
        'deployments': lambda item: item.get('name'),
        'composites': lambda item: (
            ("{}::{}".format(
                item.get('partition') or item.get('partitionName') or 'default',
                item.get('name')
            )) if item.get('name') else None
        ),
        'threads': lambda item: item.get('server') or item.get('name'),
    }

    if isinstance(value, list):
        getter = key_getters.get(current_key, lambda item: item.get('name') if isinstance(item, dict) else None)
        mapping = {}
        for index, item in enumerate(value, start=1):
            if isinstance(item, dict):
                key = getter(item) or "{}_{}".format(current_key or 'item', index)
                mapping[key] = normalize_collections(item)
            else:
                mapping["{}_{}".format(current_key or 'item', index)] = item
        return mapping

    if isinstance(value, dict):
        return {key: normalize_collections(child, key) for key, child in value.items()}
    return value


def next_key(prefix, container):
    """Return a fallback key using the container size as the index."""

    count = len(container) + 1 if hasattr(container, '__len__') else 1
    return "{}_{}".format(prefix, count)


def load_sample_payload(check):
    path = os.environ.get('WLST_SAMPLE_OUTPUT')
    if not path or not os.path.exists(path):
        return None

    try:
        with io_open(path, 'r', encoding='utf-8') as handle:
            payload = json.load(handle)
    except TypeError:  # pragma: no cover - ``encoding`` not supported
        with io_open(path, 'r') as handle:
            payload = json.load(handle)

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
    if not (username and password and admin_url):
        return False

    connect_fn = globals().get('connect')  # Provided by WLST at runtime
    if connect_fn is None:
        return False

    try:
        connect_fn(username, password, admin_url)
        return True
    except Exception as exc:  # pragma: no cover - WLST environment only
        print(json.dumps({'error': 'Failed to connect via WLST: {}'.format(exc)}))
        sys.exit(1)


def ensure_domain_runtime():
    domain_runtime = globals().get('domainRuntime')
    if domain_runtime is None:
        return False
    domain_runtime()
    return True


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
                    server_key = server_name or next_key('server', cluster_info['servers'])
                    cluster_info['servers'][server_key] = {
                        'name': server_name,
                        'state': getattr(server, 'getState', lambda: None)(),
                        'health': normalize_health_state(health),
                    }
                key = name or next_key('cluster', clusters)
                clusters[key] = cluster_info
    except Exception as exc:
        error_key = next_key('cluster_error', clusters)
        clusters[error_key] = {'name': 'ERROR', 'state': str(exc)}
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
                    heap_current = heap_runtime.getHeapSizeCurrent() if hasattr(heap_runtime, 'getHeapSizeCurrent') else None
                    heap_max = heap_runtime.getHeapSizeMax() if hasattr(heap_runtime, 'getHeapSizeMax') else None
            name = runtime.getName() if hasattr(runtime, 'getName') else None
            server_key = name or next_key('server', servers)
            servers[server_key] = {
                'name': name,
                'state': runtime.getState() if hasattr(runtime, 'getState') else None,
                'cluster': getattr(runtime, 'getClusterName', lambda: None)(),
                'health': normalize_health_state(health),
                'listenAddress': getattr(runtime, 'getListenAddress', lambda: None)(),
                'listenPort': getattr(runtime, 'getListenPort', lambda: None)(),
                'heapCurrent': heap_current,
                'heapMax': heap_max,
            }
    except Exception as exc:
        error_key = next_key('server_error', servers)
        servers[error_key] = {'name': 'ERROR', 'state': str(exc)}
    return servers


def fetch_threads():  # pragma: no cover - WLST environment only
    thread_pools = {}
    try:
        ensure_domain_runtime()
        runtimes = cmo.getServerRuntimes()
        for runtime in runtimes or []:
            name = getattr(runtime, 'getName', lambda: None)()
            thread_runtime = getattr(runtime, 'getThreadPoolRuntime', lambda: None)()
            if not thread_runtime:
                continue
            entry = {
                'server': name,
                'executeThreadTotalCount': getattr(
                    thread_runtime, 'getExecuteThreadTotalCount', lambda: None
                )(),
                'executeThreadIdleCount': getattr(
                    thread_runtime, 'getExecuteThreadIdleCount', lambda: None
                )(),
                'pendingUserRequestCount': getattr(
                    thread_runtime, 'getPendingUserRequestCount', lambda: None
                )(),
                'hoggingThreadCount': getattr(
                    thread_runtime, 'getHoggingThreadCount', lambda: None
                )(),
                'stuckThreadCount': getattr(
                    thread_runtime, 'getStuckThreadCount', lambda: None
                )(),
                'queueLength': getattr(thread_runtime, 'getQueueLength', lambda: None)(),
            }
            throughput = getattr(thread_runtime, 'getThroughput', None)
            if callable(throughput):
                try:
                    entry['throughput'] = throughput()
                except Exception:
                    entry['throughput'] = None
            pool_key = name or next_key('threadPool', thread_pools)
            thread_pools[pool_key] = entry
    except Exception as exc:
        error_key = next_key('thread_error', thread_pools)
        thread_pools[error_key] = {'server': 'ERROR', 'state': str(exc)}
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
                dest_key = dest_name or next_key('destination', destinations)
                destinations[dest_key] = {
                    'name': dest_name,
                    'type': getattr(dest, 'getType', lambda: None)(),
                    'messagesCurrentCount': getattr(dest, 'getMessagesCurrentCount', lambda: None)(),
                    'messagesHighCount': getattr(dest, 'getMessagesHighCount', lambda: None)(),
                    'consumersCurrentCount': getattr(dest, 'getConsumersCurrentCount', lambda: None)(),
                }
            name = getattr(runtime, 'getName', lambda: None)()
            server_key = name or next_key('jmsServer', servers)
            servers[server_key] = {
                'name': name,
                'state': getattr(runtime, 'getState', lambda: None)(),
                'health': normalize_health_state(health),
                'destinations': destinations,
            }
    except Exception as exc:
        error_key = next_key('jms_error', servers)
        servers[error_key] = {'name': 'ERROR', 'state': str(exc)}
    return servers


def fetch_datasources():  # pragma: no cover - WLST environment only
    datasources = {}
    try:
        ensure_domain_runtime()
        service = cmo.getJDBCServiceRuntime()
        if service:
            for runtime in service.getJDBCDataSourceRuntimeMBeans():
                name = runtime.getName()
                datasource_key = name or next_key('datasource', datasources)
                datasources[datasource_key] = {
                    'name': name,
                    'state': runtime.getState(),
                    'activeConnectionsCurrentCount': runtime.getActiveConnectionsCurrentCount(),
                }
    except Exception as exc:
        error_key = next_key('datasource_error', datasources)
        datasources[error_key] = {'name': 'ERROR', 'state': str(exc)}
    return datasources


def fetch_deployments():  # pragma: no cover - WLST environment only
    deployments = {}
    try:
        ensure_domain_runtime()
        runtime = cmo.lookupAppRuntimeStateRuntime()
        if runtime:
            for app in runtime.getAppDeploymentStateRuntimes():
                name = app.getName()
                deployment_key = name or next_key('deployment', deployments)
                deployments[deployment_key] = {
                    'name': name,
                    'state': app.getState(),
                }
    except Exception as exc:
        error_key = next_key('deployment_error', deployments)
        deployments[error_key] = {'name': 'ERROR', 'state': str(exc)}
    return deployments


def fetch_composites():  # pragma: no cover - WLST environment only
    composites = {}
    try:
        soa = globals().get('soa_cluster_state')
        if soa:
            composites_list = soa()  # Custom hook provided by customer WLST scripts
        else:
            composites_list = []
        for composite in composites_list:
            if 'version' not in composite and hasattr(composite, 'getRevision'):  # pragma: no cover
                try:
                    composite['version'] = composite.getRevision()
                except Exception:
                    composite['version'] = None
            name = composite.get('name')
            partition = composite.get('partition') or composite.get('partitionName')
            if name:
                key = "{}::{}".format(partition or 'default', name)
            else:
                key = next_key('composite', composites)
            composites[key] = composite
    except Exception as exc:
        error_key = next_key('composite_error', composites)
        composites[error_key] = {'name': 'ERROR', 'state': str(exc)}
    return composites


def gather(check, username, password, admin_url):
    sample_payload = load_sample_payload(check)
    if sample_payload is not None and not sample_payload:
        return normalize_collections(sample_payload)
    if sample_payload:
        sample_payload['generatedAt'] = datetime.utcnow().isoformat() + 'Z'
        sample_payload['source'] = 'sample'
        return normalize_collections(sample_payload)

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
    check = sys.argv[1] if len(sys.argv) > 1 else 'all'
    admin_url = sys.argv[2] if len(sys.argv) > 2 else None
    username = sys.argv[3] if len(sys.argv) > 3 else None
    password = sys.argv[4] if len(sys.argv) > 4 else None

    payload = gather(check, username, password, admin_url) or {}
    if 'generatedAt' not in payload:
        payload['generatedAt'] = datetime.utcnow().isoformat() + 'Z'
    payload.setdefault('check', check)

    print(json.dumps(payload))


if __name__ == '__main__':
    main()
