import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
from socket import create_connection

try:
    import psutil
except ImportError:  # pragma: no cover - optional dependency
    psutil = None

try:
    import requests
except ImportError:  # pragma: no cover - optional dependency
    requests = None


def check_os_cpu():
    """Print the current CPU usage."""
    if psutil:
        usage = psutil.cpu_percent(interval=1)
        cpus = psutil.cpu_count()
    else:
        usage = os.getloadavg()[0] * 100 / (os.cpu_count() or 1)
        cpus = os.cpu_count() or 1
    print(f"CPU usage: {usage:.2f}% ({cpus} cores)")


def check_os_memory():
    """Print the current memory usage."""
    if psutil:
        mem = psutil.virtual_memory()
        total = mem.total / (1024 * 1024)
        usage = mem.percent
    else:
        meminfo = {}
        with open('/proc/meminfo') as f:
            for line in f:
                key, value = line.split(':')
                meminfo[key] = int(value.strip().split()[0])
        total = meminfo['MemTotal'] / 1024
        free = (meminfo.get('MemFree', 0) + meminfo.get('Buffers', 0) + meminfo.get('Cached', 0)) / 1024
        usage = 100 * (1 - free / total)
    print(f"Memory usage: {usage:.2f}% of {total:.0f}MB")


def check_servers(names):
    """Check if server processes are running."""
    for name in names:
        result = subprocess.run(['pgrep', '-fl', name], stdout=subprocess.PIPE, text=True)
        if result.stdout.strip():
            print(f"Server '{name}' is running")
        else:
            print(f"Server '{name}' is NOT running")


def placeholder(message):
    print(f"[INFO] {message} check not implemented. Provide WLST script or custom logic.")


def http_get_json(url, auth=None):
    """Retrieve JSON from a URL using requests or urllib."""
    if requests:
        resp = requests.get(url, auth=auth, timeout=5)
        resp.raise_for_status()
        return resp.json()

    import urllib.request
    req = urllib.request.Request(url)
    if auth:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=5) as resp:  # pragma: no cover - network
        return json.loads(resp.read().decode())


def check_cluster(admin_url, auth):
    """Check cluster state via WebLogic REST API."""
    try:
        data = http_get_json(f"{admin_url}/management/weblogic/latest/domainRuntime/ClusterRuntimes", auth)
    except Exception as exc:
        print(f"[ERROR] Failed to get cluster info: {exc}")
        return

    clusters = data.get("items", [])
    if not clusters:
        print("No clusters found")
    for cluster in clusters:
        name = cluster.get("name")
        state = cluster.get("state") or cluster.get("stateReturn")
        print(f"Cluster {name}: {state}")


def check_jms(admin_url, auth):
    """Check JMS runtimes."""
    try:
        data = http_get_json(f"{admin_url}/management/weblogic/latest/domainRuntime/JMSRuntime/JMSServers", auth)
    except Exception as exc:
        print(f"[ERROR] Failed to get JMS info: {exc}")
        return

    for jms in data.get("items", []):
        name = jms.get("name")
        state = jms.get("healthState", {}).get("state")
        print(f"JMS Server {name}: {state}")


def check_datasource(admin_url, auth):
    """Check JDBC data sources."""
    try:
        data = http_get_json(
            f"{admin_url}/management/weblogic/latest/domainRuntime/JDBCServiceRuntime/JDBCDataSourceRuntimeMBeans",
            auth,
        )
    except Exception as exc:
        print(f"[ERROR] Failed to get datasource info: {exc}")
        return

    for ds in data.get("items", []):
        name = ds.get("name")
        state = ds.get("state") or ds.get("stateReturn")
        failures = ds.get("activeConnectionsCurrentCount", "?")
        print(f"Datasource {name}: {state}, Active={failures}")


def check_deployments(admin_url, auth):
    """Check application deployments."""
    try:
        data = http_get_json(
            f"{admin_url}/management/weblogic/latest/domainRuntime/AppRuntimeStateRuntime/AppDeploymentStateRuntimes",
            auth,
        )
    except Exception as exc:
        print(f"[ERROR] Failed to get deployment info: {exc}")
        return

    for app in data.get("items", []):
        name = app.get("name")
        state = app.get("state") or app.get("stateReturn")
        print(f"Deployment {name}: {state}")


def check_composites(admin_url, auth):
    """Check deployed SOA composites."""
    try:
        data = http_get_json(f"{admin_url}/soa-infra/management/partitions", auth)
    except Exception as exc:
        print(f"[ERROR] Failed to get composite info: {exc}")
        return

    for partition in data.get("items", []):
        pname = partition.get("name")
        composites = partition.get("composites", [])
        for comp in composites:
            cname = comp.get("name")
            state = comp.get("state")
            print(f"Composite {pname}/{cname}: {state}")


def check_ldap(host, port):
    """Check if LDAP host is reachable."""
    try:
        with create_connection((host, port), timeout=5):
            print(f"LDAP service {host}:{port} reachable")
    except Exception as exc:
        print(f"LDAP service {host}:{port} unreachable: {exc}")


def main():
    parser = argparse.ArgumentParser(description='Oracle Fusion Middleware health check tool')
    parser.add_argument('--full', action='store_true', help='Run full health check')
    parser.add_argument('--checks', help='Comma separated list of checks to run')
    parser.add_argument('--servers', help='Comma separated names of server processes to validate')
    parser.add_argument('--admin-url', help='Admin server base URL (e.g. http://host:7001)')
    parser.add_argument('--username', help='Admin username for REST checks')
    parser.add_argument('--password', help='Admin password for REST checks')
    parser.add_argument('--ldap-host', help='LDAP hostname to check')
    parser.add_argument('--ldap-port', type=int, default=389, help='LDAP port (default 389)')
    args = parser.parse_args()

    available = {
        'cpu': check_os_cpu,
        'memory': check_os_memory,
        'servers': lambda: check_servers([s.strip() for s in args.servers.split(',')]) if args.servers else placeholder('Server'),
        'cluster': (
            lambda: check_cluster(args.admin_url, (args.username, args.password))
            if args.admin_url and args.username and args.password
            else placeholder('Cluster')
        ),
        'jms': (
            lambda: check_jms(args.admin_url, (args.username, args.password))
            if args.admin_url and args.username and args.password
            else placeholder('JMS')
        ),
        'datasource': (
            lambda: check_datasource(args.admin_url, (args.username, args.password))
            if args.admin_url and args.username and args.password
            else placeholder('Datasource')
        ),
        'deployments': (
            lambda: check_deployments(args.admin_url, (args.username, args.password))
            if args.admin_url and args.username and args.password
            else placeholder('Deployments')
        ),
        'composites': (
            lambda: check_composites(args.admin_url, (args.username, args.password))
            if args.admin_url and args.username and args.password
            else placeholder('Composites')
        ),
        'ldap': (
            lambda: check_ldap(args.ldap_host, args.ldap_port)
            if args.ldap_host
            else placeholder('LDAP')
        ),
    }

    if args.full:
        checks = list(available.keys())
    elif args.checks:
        checks = [c.strip() for c in args.checks.split(',') if c.strip() in available]
    else:
        parser.print_help()
        sys.exit(1)

    for check in checks:
        print(f"\n--- {check.upper()} ---")
        available[check]()


if __name__ == '__main__':
    main()
