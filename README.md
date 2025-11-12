# training

This repository contains sample scripts and notes. The `middleware_healthcheck.py` script performs basic health checks for Oracle Fusion Middleware environments.

## Download the OCI Generative AI chat demo

To create a distributable archive of the Oracle JET Generative AI chat application, run the packaging script and share the resulting zip file:

```bash
./scripts/package-archive.sh
```

The script produces `ojet-oci-genai-chat.zip` in the repository root while omitting transient directories such as `node_modules/`. Pass a custom filename if you prefer a different archive name:

```bash
./scripts/package-archive.sh my-custom-chat.zip
```


## Requirements

- Python 3.8 or newer
- Oracle WebLogic installation that provides `wlst.sh` (or `wlst.cmd` on Windows) when you want to execute WebLogic-specific health checks
- Optional packages:
  - [`psutil`](https://pypi.org/project/psutil/) for detailed CPU and memory metrics (otherwise `/proc` fallbacks are used on Linux).
  - [`PyYAML`](https://pypi.org/project/PyYAML/) if you want to read configuration from YAML files.

The reporting wrapper writes JSON, HTML, PDF, and `.doc` (RTF) reports using only the Python standard library—no extra dependencies are required for those formats.

## Usage

Run a full health check:

```bash
python middleware_healthcheck.py --full --servers AdminServer1,ManagedServer1
```

Run specific checks:

```bash
python middleware_healthcheck.py --checks cpu,memory,managed_servers,jms --servers AdminServer1
```

Provide arguments from a JSON or YAML configuration file. Example files are available in `sample_config.json` and `sample_config.yaml`.

```bash
python middleware_healthcheck.py --config config.yaml
```

Example `config.yaml`:

```yaml
full: true
servers:
  - AdminServer1
  - ManagedServer1
admin_url: http://localhost:7001
username: weblogic
password: welcome1
ldap_host: ldap.example.com
ldap_port: 1389
wlst_exec: /path/to/weblogic/oracle_common/common/bin/wlst.sh
wlst_script: /path/to/wlst_health_checks.py
```

> **Note:** YAML configuration requires the optional [PyYAML](https://pyyaml.org/) package. JSON configuration works with the Python standard library only.

To use the provided sample JSON configuration:

```bash
python middleware_healthcheck.py --config sample_config.json
```

The script reports CPU and memory usage, verifies that specified server processes are running, and invokes the configured WLST script (`wlst_health_checks.py`) for WebLogic-specific status. The WLST integration currently surfaces:

- Cluster state plus the health of each member server
- Managed server runtime metrics, including health, listen address, and JVM heap usage
- JMS server health along with queue/topic statistics (pending messages, peak counts, consumers)
- Thread pool metrics per managed server (execute threads, pending requests, hogging/stuck counts)
- JDBC datasource state and active connection counts
- Deployment lifecycle state
- SOA composite state and revision information

Supply `--admin-url`, `--username`, and `--password` so the WLST script can connect to the admin server. LDAP reachability can be tested with `--ldap-host` and `--ldap-port`.

Example using the WLST checks:

```bash
python middleware_healthcheck.py --full \
  --servers AdminServer1,ManagedServer1 \
  --admin-url t3://wls-admin.example.com:7001 \
  --username weblogic --password welcome1 \
  --wlst-exec /oracle/middleware/oracle_common/common/bin/wlst.sh \
  --wlst-script /opt/tools/wlst_health_checks.py \
  --ldap-host ldap.example.com
```

If you do not have access to WLST locally, you can simulate its output by supplying `--wlst-exec python3`, `--wlst-script wlst_health_checks.py`, and `--wlst-sample-output sample_wlst_output.json`. This is how the bundled sample configuration files are wired for quick demos.

### WLST integration details

- `wlst_health_checks.py` lives in the repository and is intended to be copied to a location accessible by your WebLogic installation.
- The script prints a single JSON document describing the requested check. When the health-check CLI invokes it, the final JSON line is parsed and used to display human-friendly messages.
- The helper function `placeholder()` in `middleware_healthcheck.py` prints the message `"[INFO] <check> check is unavailable because no WLST script has been configured..."` whenever a WLST-backed check (cluster, JMS, datasource, deployments, composites) is requested but the WLST executable or script path has not been provided. Configure the WLST options to silence this message.

For reference, `sample_wlst_output.json` shows the expected JSON structure that the WLST script emits, including the `threads` section produced from each server's thread pool runtime:

```json
{
  "generatedAt": "2024-01-01T00:00:00Z",
  "check": "all",
  "clusters": [
    {
      "name": "ProdCluster",
      "state": "RUNNING",
      "servers": [
        {"name": "AdminServer", "state": "RUNNING", "health": "HEALTH_OK"},
        {"name": "soa_server1", "state": "RUNNING", "health": "HEALTH_WARN"}
      ]
    }
  ],
  "servers": [
    {"name": "AdminServer", "state": "RUNNING", "health": "HEALTH_OK", "listenAddress": "admin.example.com", "listenPort": 7001, "heapCurrent": 536870912, "heapMax": 1073741824}
  ],
  "jmsServers": [
    {
      "name": "JMSServer1",
      "state": "RUNNING",
      "health": "HEALTH_OK",
      "destinations": [
        {"name": "RequestQueue", "type": "Queue", "messagesCurrentCount": 10, "messagesHighCount": 50, "consumersCurrentCount": 2}
      ]
    }
  ],
  "threads": [
    {
      "server": "AdminServer",
      "executeThreadTotalCount": 32,
      "executeThreadIdleCount": 20,
      "pendingUserRequestCount": 1,
      "hoggingThreadCount": 0,
      "stuckThreadCount": 0,
      "queueLength": 0,
      "throughput": 45.5
    },
    {
      "server": "soa_server1",
      "executeThreadTotalCount": 48,
      "executeThreadIdleCount": 30,
      "pendingUserRequestCount": 2,
      "hoggingThreadCount": 1,
      "stuckThreadCount": 0,
      "queueLength": 3,
      "throughput": 38.2
    }
  ],
  "datasources": [{"name": "SOADataSource", "state": "Running", "activeConnectionsCurrentCount": 12}],
  "deployments": [{"name": "soa-infra", "state": "ACTIVE"}, {"name": "SampleApp", "state": "ACTIVE"}],
  "composites": [{"partition": "default", "name": "OrderProcessor", "state": "active", "version": "1.0"}]
}
```

The repository also includes `sample_healthcheck_output.txt`, which captures a full CLI run using the bundled sample data so you can preview the formatted output.

### Generating reports

Use `report_wrapper.py` to run the health check and save the output in your preferred format. Pass all `middleware_healthcheck.py` arguments after `--`:

```bash
python report_wrapper.py --format json --output report.json -- --full --servers AdminServer1
```

Produce other formats the same way:

```bash
python report_wrapper.py --format html --output report.html -- --full --servers AdminServer1
python report_wrapper.py --format pdf --output report.pdf -- --full --servers AdminServer1
python report_wrapper.py --format doc --output report.doc -- --full --servers AdminServer1
```
