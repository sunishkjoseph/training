import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from socket import create_connection

try:
    import psutil
except ImportError:  # pragma: no cover - optional dependency
    psutil = None


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
    """Explain that a WLST-backed implementation is required for a check."""

    print(
        f"[INFO] {message} check is unavailable because no WLST script has been configured. "
        "Provide the --wlst-exec and --wlst-script parameters or update the config file."
    )


def run_wlst(check, args):
    """Invoke the configured WLST script and return the JSON payload it emits."""

    if not args.wlst_script or not args.wlst_exec:
        placeholder(check.title())
        return None

    command = [
        args.wlst_exec,
        args.wlst_script,
        check,
        args.admin_url or '',
        args.username or '',
        args.password or '',
    ]

    env = os.environ.copy()
    if args.wlst_sample_output:
        env['WLST_SAMPLE_OUTPUT'] = args.wlst_sample_output

    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            env=env,
        )
    except FileNotFoundError as exc:
        print(f"[ERROR] WLST executable '{args.wlst_exec}' not found: {exc}")
        return None

    if result.returncode != 0:
        print(f"[ERROR] WLST returned {result.returncode}: {result.stderr.strip() or result.stdout.strip()}")
        return None

    payload = result.stdout.strip() or result.stderr.strip()
    if not payload:
        print("[ERROR] WLST script produced no output")
        return None

    # WLST may emit log lines. Locate the final JSON structure.
    for line in reversed(payload.splitlines()):
        candidate = line.strip()
        if candidate.startswith('{') or candidate.startswith('['):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        print(f"[ERROR] Unable to decode WLST output as JSON: {exc}\nRaw output:\n{payload}")
        return None


def check_cluster(args):
    """Check cluster state via WLST."""

    data = run_wlst('cluster', args)
    if not data:
        return

    clusters = data.get('clusters') or data.get('items', [])
    if not clusters:
        print("No clusters found")
    for cluster in clusters:
        name = cluster.get('name')
        state = cluster.get('state') or cluster.get('status') or cluster.get('stateReturn')
        print(f"Cluster {name}: {state}")
        for server in cluster.get('servers', []):
            server_name = server.get('name')
            server_state = server.get('state') or server.get('status')
            health = server.get('health')
            details = f" (health: {health})" if health else ''
            print(f"  Member {server_name}: {server_state}{details}")


def check_managed_servers(args):
    """Check managed server runtimes via WLST."""

    data = run_wlst('managed_servers', args)
    if not data:
        return

    servers = data.get('servers') or data.get('items', [])
    for server in servers:
        name = server.get('name')
        state = server.get('state') or server.get('status')
        health = server.get('health')
        cluster = server.get('cluster')
        heap_current = server.get('heapCurrent')
        heap_max = server.get('heapMax')
        heap_info = ''
        if heap_current is not None and heap_max not in (None, 0):
            try:
                heap_info = f" | Heap {float(heap_current)/1024/1024:.1f}MB/{float(heap_max)/1024/1024:.1f}MB"
            except (TypeError, ValueError):
                heap_info = f" | Heap {heap_current}/{heap_max}"
        cluster_info = f" | Cluster {cluster}" if cluster else ''
        health_info = f" | Health {health}" if health else ''
        address = server.get('listenAddress')
        port = server.get('listenPort')
        endpoint = f" | {address}:{port}" if address or port else ''
        print(f"Server {name}: {state}{health_info}{cluster_info}{endpoint}{heap_info}")


def check_jms(args):
    """Check JMS runtimes via WLST."""

    data = run_wlst('jms', args)
    if not data:
        return

    for jms in data.get('jmsServers', []) or data.get('items', []):
        name = jms.get('name')
        state = jms.get('state') or jms.get('health') or jms.get('healthState', {}).get('state')
        print(f"JMS Server {name}: {state}")
        for destination in jms.get('destinations', []):
            dest_name = destination.get('name')
            dest_type = destination.get('type')
            pending = destination.get('messagesCurrentCount')
            high = destination.get('messagesHighCount')
            consumers = destination.get('consumersCurrentCount')
            metrics = []
            if pending is not None:
                metrics.append(f"Pending={pending}")
            if high is not None:
                metrics.append(f"High={high}")
            if consumers is not None:
                metrics.append(f"Consumers={consumers}")
            metrics_str = f" ({', '.join(metrics)})" if metrics else ''
            type_str = f"[{dest_type}] " if dest_type else ''
            print(f"  {type_str}{dest_name}{metrics_str}")


def check_threads(args):
    """Check thread pool runtime statistics via WLST."""

    data = run_wlst('threads', args)
    if not data:
        return

    pools = data.get('threads') or data.get('threadPools') or data.get('items', [])
    if not pools:
        print("No thread pool data returned")
        return

    for pool in pools:
        server = pool.get('server') or pool.get('name')
        total = pool.get('executeThreadTotalCount')
        idle = pool.get('executeThreadIdleCount')
        hogging = pool.get('hoggingThreadCount')
        stuck = pool.get('stuckThreadCount')
        pending = pool.get('pendingUserRequestCount')
        queue = pool.get('queueLength')
        throughput = pool.get('throughput')

        metrics = []
        if total is not None:
            metrics.append(f"Total={total}")
        if idle is not None:
            metrics.append(f"Idle={idle}")
        if pending is not None:
            metrics.append(f"Pending={pending}")
        if queue is not None:
            metrics.append(f"Queue={queue}")
        if hogging is not None:
            metrics.append(f"Hogging={hogging}")
        if stuck is not None:
            metrics.append(f"Stuck={stuck}")
        if throughput is not None:
            metrics.append(f"Throughput={throughput}")

        metrics_str = ', '.join(metrics) if metrics else 'No metrics reported'
        print(f"Thread pool {server or 'unknown'}: {metrics_str}")


def check_datasource(args):
    """Check JDBC data sources via WLST."""

    data = run_wlst('datasource', args)
    if not data:
        return

    for ds in data.get('datasources', []) or data.get('items', []):
        name = ds.get('name')
        state = ds.get('state') or ds.get('status') or ds.get('stateReturn')
        active = ds.get('activeConnectionsCurrentCount')
        additional = f", Active={active}" if active is not None else ''
        print(f"Datasource {name}: {state}{additional}")


def check_deployments(args):
    """Check application deployments via WLST."""

    data = run_wlst('deployments', args)
    if not data:
        return

    for app in data.get('deployments', []) or data.get('items', []):
        name = app.get('name')
        state = app.get('state') or app.get('status') or app.get('stateReturn')
        print(f"Deployment {name}: {state}")


def check_composites(args):
    """Check deployed SOA composites via WLST."""

    data = run_wlst('composites', args)
    if not data:
        return

    composites = data.get('composites') or data.get('items', [])
    for composite in composites:
        partition = composite.get('partition') or composite.get('partitionName')
        name = composite.get('name')
        state = composite.get('state')
        version = composite.get('version') or composite.get('revision') or composite.get('compositeVersion')
        prefix = f"{partition}/" if partition else ''
        version_info = f" (version {version})" if version else ''
        print(f"Composite {prefix}{name}: {state}{version_info}")


def check_ldap(host, port):
    """Check if LDAP host is reachable."""
    try:
        with create_connection((host, port), timeout=5):
            print(f"LDAP service {host}:{port} reachable")
    except Exception as exc:
        print(f"LDAP service {host}:{port} unreachable: {exc}")


def load_config(path):
    """Load configuration from a JSON or YAML file."""
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file '{path}' not found")

    text = config_path.read_text()
    suffix = config_path.suffix.lower()
    if suffix in {'.yaml', '.yml'}:
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("PyYAML is required to load YAML configuration files") from exc

        data = yaml.safe_load(text)
    else:
        data = json.loads(text)

    if not isinstance(data, dict):
        raise ValueError("Configuration file must contain a JSON/YAML object")
    return data


def apply_config(args, config):
    """Merge configuration dictionary into argparse Namespace."""

    def ensure_comma_separated(value):
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, (list, tuple, set)):
            return ','.join(str(item) for item in value)
        return str(value)

    if 'full' in config and not args.full and not args.checks:
        args.full = bool(config['full'])
    if 'checks' in config and args.checks is None:
        args.checks = ensure_comma_separated(config['checks'])
    if 'servers' in config and args.servers is None:
        args.servers = ensure_comma_separated(config['servers'])
    if 'admin_url' in config and args.admin_url is None:
        args.admin_url = config['admin_url']
    if 'username' in config and args.username is None:
        args.username = config['username']
    if 'password' in config and args.password is None:
        args.password = config['password']
    if 'ldap_host' in config and args.ldap_host is None:
        args.ldap_host = config['ldap_host']
    if 'ldap_port' in config and (args.ldap_port == 389 or args.ldap_port is None):
        try:
            args.ldap_port = int(config['ldap_port'])
        except (TypeError, ValueError):
            raise ValueError("ldap_port must be an integer")
    if 'wlst_exec' in config and args.wlst_exec is None:
        args.wlst_exec = config['wlst_exec']
    if 'wlst_script' in config and args.wlst_script is None:
        args.wlst_script = config['wlst_script']
    if 'wlst_sample_output' in config and args.wlst_sample_output is None:
        args.wlst_sample_output = config['wlst_sample_output']


def main():
    parser = argparse.ArgumentParser(description='Oracle Fusion Middleware health check tool')
    parser.add_argument('--config', help='Path to JSON/YAML file containing parameters')
    parser.add_argument('--full', action='store_true', help='Run full health check')
    parser.add_argument('--checks', help='Comma separated list of checks to run')
    parser.add_argument('--servers', help='Comma separated names of server processes to validate')
    parser.add_argument('--admin-url', help='Admin server base URL (e.g. http://host:7001)')
    parser.add_argument('--username', help='Admin username for WLST checks')
    parser.add_argument('--password', help='Admin password for WLST checks')
    parser.add_argument('--ldap-host', help='LDAP hostname to check')
    parser.add_argument('--ldap-port', type=int, default=389, help='LDAP port (default 389)')
    parser.add_argument('--wlst-exec', help='Path to wlst.sh (or python3 for local testing)')
    parser.add_argument('--wlst-script', help='Path to the WLST script that emits JSON status')
    parser.add_argument('--wlst-sample-output', help='Path to a JSON file used to simulate WLST output')
    args = parser.parse_args()

    if args.config:
        try:
            config = load_config(args.config)
        except Exception as exc:
            print(f"[ERROR] {exc}")
            sys.exit(1)
        apply_config(args, config)

    available = {
        'cpu': check_os_cpu,
        'memory': check_os_memory,
        'servers': (
            lambda: check_servers([s.strip() for s in args.servers.split(',')])
            if args.servers
            else placeholder('Server processes')
        ),
        'managed_servers': lambda: check_managed_servers(args),
        'cluster': lambda: check_cluster(args),
        'jms': lambda: check_jms(args),
        'threads': lambda: check_threads(args),
        'datasource': lambda: check_datasource(args),
        'deployments': lambda: check_deployments(args),
        'composites': lambda: check_composites(args),
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
