# training

This repository contains sample scripts and notes. The `middleware_healthcheck.py` script performs basic health checks for Oracle Fusion Middleware environments.

## Usage

Run a full health check:

```bash
python middleware_healthcheck.py --full --servers AdminServer1,ManagedServer1
```

Run specific checks:

```bash
python middleware_healthcheck.py --checks cpu,memory,deployments --servers AdminServer1
```

The script reports CPU and memory usage, verifies that specified server processes are running, and can query WebLogic REST services for cluster, JMS, datasource, deployments, and SOA composite status when `--admin-url`, `--username`, and `--password` are supplied. LDAP reachability can be tested with `--ldap-host` and `--ldap-port`.

Example using the REST checks:

```bash
python middleware_healthcheck.py --full \
  --servers AdminServer1,ManagedServer1 \
  --admin-url http://localhost:7001 \
  --username weblogic --password welcome1 \
  --ldap-host ldap.example.com
```
