from __future__ import print_function
"""
WLST script to output Oracle WebLogic health information as JSON.

This script is designed to be executed via `wlst.sh` and prints a
single JSON object to stdout. It can emit information about:
- Servers and clusters
- JDBC datasources
- JMS servers / destinations
- Deployments
- SOA composites (if SOA is installed in the domain)

It is intentionally written to be conservative in its Python usage so
that it works with the Jython version bundled with WebLogic.
"""

import os
import sys
import json
from datetime import datetime


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _ensure_json_module():
    """Ensure we have a JSON implementation."""
    global json
    try:
        json.dumps({})
    except Exception:
        try:
            import simplejson as json  # noqa
        except Exception:
            raise SystemExit("No JSON implementation available (json/simplejson).")


def _safe_call(fn, default=None):
    """Call fn(), returning default if any exception is raised."""
    try:
        return fn()
    except Exception:
        return default


def _to_list(value):
    """Normalize WebLogic arrays / singletons to a Python list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    try:
        # Many WLST arrays are Java arrays; convert via list().
        return list(value)
    except Exception:
        return [value]


# ---------------------------------------------------------------------------
# Connection handling
# ---------------------------------------------------------------------------

def connect_if_available(username, password, admin_url):
    """
    Connect to the admin server if we are actually running under WLST.

    When executed with a normal Python interpreter there is no `connect`
    function, so this becomes a no-op to allow local dry runs.
    """
    if "connect" not in globals():
        # Not running inside WLST – nothing to do.
        return False

    if not admin_url:
        # WLST will rely on boot.properties / defaults.
        try:
            connect()
            return True
        except Exception:
            return False

    try:
        if username and password:
            connect(username, password, admin_url)
        else:
            connect(admin_url)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Gather functions – each returns a Python dict
# ---------------------------------------------------------------------------

def gather_servers_and_clusters():
    result = {"servers": [], "clusters": []}

    if "domainRuntime" not in globals():
        # Not running under WLST – nothing to query.
        return result

    try:
        domainRuntime()
    except Exception:
        return result

    # --- Servers ---
    server_runtimes = _safe_call(lambda: cmo.getServerRuntimes(), [])
    for srv in _to_list(server_runtimes):
        item = {}
        item["name"] = _safe_call(srv.getName)
        item["state"] = _safe_call(srv.getState)

        # Health
        health_state = _safe_call(srv.getHealthState)
        if health_state:
            subsystems_raw = _safe_call(health_state.getSubSystems, [])
            subsystems = []
            for x in _to_list(subsystems_raw):
                subsystems.append(str(x))

            item["health"] = {
                "state": _safe_call(health_state.getState),
                "subsystems": subsystems
            }

        # JVM
        jvm_rt = _safe_call(srv.getJVMRuntime)
        if jvm_rt:
            item["jvm"] = {
                "heapFreeCurrent": _safe_call(jvm_rt.getHeapFreeCurrent),
                "heapSizeCurrent": _safe_call(jvm_rt.getHeapSizeCurrent),
                "heapFreePercent": _safe_call(jvm_rt.getHeapFreePercent)
            }

        # Thread pool
        thread_rt = _safe_call(srv.getThreadPoolRuntime)
        if thread_rt:
            item["threadPool"] = {
                "executeThreadTotalCount": _safe_call(thread_rt.getExecuteThreadTotalCount),
                "executeThreadIdleCount": _safe_call(thread_rt.getExecuteThreadIdleCount),
                "hoggingThreadCount": _safe_call(thread_rt.getHoggingThreadCount),
                "stuckThreadCount": _safe_call(thread_rt.getStuckThreadCount)
            }

        result["servers"].append(item)

    # --- Clusters ---
    # For clusters we need to go to serverConfig()
    try:
        serverConfig()
    except Exception:
        return result

    clusters = _safe_call(lambda: cmo.getClusters(), [])
    for cl in _to_list(clusters):
        cl_item = {"name": _safe_call(cl.getName), "servers": []}
        srv_refs = _to_list(_safe_call(cl.getServers, []))
        for sref in srv_refs:
            name = _safe_call(sref.getName)
            if name:
                cl_item["servers"].append(name)
        result["clusters"].append(cl_item)

    return result


def gather_jdbc():
    result = {"datasources": []}

    if "domainRuntime" not in globals():
        return result

    try:
        domainRuntime()
    except Exception:
        return result

    jdbc_service = _safe_call(lambda: cmo.getJdbcServiceRuntime())
    if not jdbc_service:
        return result

    ds_runtimes = _to_list(_safe_call(jdbc_service.getJDBCDataSourceRuntimeMBeans, []))
    for ds in ds_runtimes:
        item = {
            "name": _safe_call(ds.getName),
            "state": _safe_call(ds.getState),
            "activeConnectionsCurrentCount": _safe_call(ds.getActiveConnectionsCurrentCount),
            "activeConnectionsHighCount": _safe_call(ds.getActiveConnectionsHighCount),
            "activeConnectionsAverageCount": _safe_call(ds.getActiveConnectionsAverageCount),
            "waitingForConnectionCurrentCount": _safe_call(ds.getWaitingForConnectionCurrentCount),
            "waitingForConnectionHighCount": _safe_call(ds.getWaitingForConnectionHighCount),
            "waitingForConnectionTotal": _safe_call(ds.getWaitingForConnectionTotal),
            "failuresToReconnectCount": _safe_call(ds.getFailuresToReconnectCount),
        }
        result["datasources"].append(item)

    return result


def gather_jms():
    result = {"jmsServers": [], "destinations": []}

    if "domainRuntime" not in globals():
        return result

    try:
        domainRuntime()
    except Exception:
        return result

    # JMS servers – NOTE: depending on WLST version these may come
    # from different places; this section is intentionally simple.
    jms_rt = _safe_call(lambda: cmo.getJMSServers(), [])
    for jms in _to_list(jms_rt):
        item = {
            "name": _safe_call(jms.getName),
            "hostedDestinationsCurrentCount": _safe_call(jms.getHostedDestinationsCurrentCount),
            "messagesCurrentCount": _safe_call(jms.getMessagesCurrentCount),
            "messagesPendingCount": _safe_call(jms.getMessagesPendingCount),
        }
        result["jmsServers"].append(item)

    # Destinations (queues, topics) from JMSRuntime if available
    jms_runtime = _safe_call(lambda: cmo.getJMSRuntime())
    if not jms_runtime:
        return result

    jms_servers = _to_list(_safe_call(jms_runtime.getJMSServers, []))
    for jms_server in jms_servers:
        dest_runtimes = _to_list(_safe_call(jms_server.getDestinations, []))
        for dest in dest_runtimes:
            d = {
                "name": _safe_call(dest.getName),
                "server": _safe_call(jms_server.getName),
                "type": _safe_call(dest.getDestinationType),
                "messagesCurrentCount": _safe_call(dest.getMessagesCurrentCount),
                "messagesPendingCount": _safe_call(dest.getMessagesPendingCount),
                "messagesHighCount": _safe_call(dest.getMessagesHighCount),
                "consumersCurrentCount": _safe_call(dest.getConsumersCurrentCount),
            }
            result["destinations"].append(d)

    return result


def gather_deployments():
    result = {"deployments": []}

    if "domainRuntime" not in globals():
        return result

    try:
        domainRuntime()
    except Exception:
        return result

    app_runtimes = _to_list(_safe_call(lambda: cmo.getApplicationRuntimes(), []))
    for app in app_runtimes:
        app_item = {
            "name": _safe_call(app.getName),
            "type": "application",
            "components": [],
        }

        comp_runtimes = _to_list(_safe_call(app.getComponentRuntimes, []))
        for comp in comp_runtimes:
            comp_item = {
                "name": _safe_call(comp.getName),
                "type": _safe_call(comp.getType),
                "targets": [],
            }

            target_states = _to_list(_safe_call(comp.getStatus, []))
            for ts in target_states:
                # Older Jython sometimes struggles with comprehensions that use
                # "if ... else" inline, so we keep this explicit and simple.
                target_name = _safe_call(ts.getServerName)
                target_state = _safe_call(ts.getState)
                if target_name:
                    comp_item["targets"].append(
                        {"server": target_name, "state": target_state}
                    )

            app_item["components"].append(comp_item)

        result["deployments"].append(app_item)

    return result


def gather_soa():
    """
    SOA composite health – this requires SOA to be installed.
    If SOA is not available in the domain, this will simply return an
    empty structure.
    """
    result = {"soaComposites": []}

    if "domainRuntime" not in globals():
        return result

    try:
        domainRuntime()
    except Exception:
        return result

    # SOA MBean path differs slightly between versions, so we probe.
    soa_runtime = None
    try:
        soa_runtime = getMBean(
            "oracle.soa.config:Location=soa_server1,name=soa-infra,"
            "j2eeType=CompositeLifeCycleConfig,Application=soa-infra"
        )
    except Exception:
        try:
            soa_runtime = getMBean(
                "oracle.soa.config:Location=AdminServer,name=soa-infra,"
                "j2eeType=CompositeLifeCycleConfig,Application=soa-infra"
            )
        except Exception:
            soa_runtime = None

    if not soa_runtime:
        return result

    composites = _to_list(_safe_call(soa_runtime.getComposites, []))
    for comp in composites:
        c_item = {
            "name": _safe_call(comp.getCompositeName),
            "state": _safe_call(comp.getCompositeState),
            "revision": _safe_call(comp.getRevision),
            "partition": _safe_call(comp.getPartition),
        }
        result["soaComposites"].append(c_item)

    return result


def gather_all():
    payload = {}
    servers = gather_servers_and_clusters()
    jdbc = gather_jdbc()
    jms = gather_jms()
    deployments = gather_deployments()
    soa = gather_soa()

    payload.update(servers)
    payload.update(jdbc)
    payload.update(jms)
    payload.update(deployments)
    payload.update(soa)

    return payload


def gather(check, username, password, admin_url):
    """
    Main dispatcher that returns a JSON-serialisable dict depending on
    the requested check type.
    """
    # Local test mode – just echo sample JSON if WLST_SAMPLE_OUTPUT is set.
    sample = os.environ.get("WLST_SAMPLE_OUTPUT")
    if sample:
        try:
            fh = open(sample, "r")
            try:
                return json.load(fh)
            finally:
                fh.close()
        except Exception, exc:
            return {"error": "Unable to load sample output: %s" % str(exc)}

    connected = connect_if_available(username, password, admin_url)
    if not connected and "domainRuntime" in globals():
        # If connect failed in WLST, we still attempt "offline" access where
        # possible, but most runtime MBeans will be unavailable.
        pass

    if check is None:
        check = "all"
    check = check.lower()

    if check == "servers":
        return gather_servers_and_clusters()
    if check == "jdbc":
        return gather_jdbc()
    if check == "jms":
        return gather_jms()
    if check == "deployments":
        return gather_deployments()
    if check == "soa":
        return gather_soa()
    # Default: everything
    return gather_all()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    _ensure_json_module()

    # Arguments:
    #   1: check name (servers|jdbc|jms|deployments|soa|all)
    #   2: admin URL (t3://host:port) – optional
    #   3: username – optional
    #   4: password – optional
    check = None
    admin_url = None
    username = None
    password = None

    if len(sys.argv) > 1:
        check = sys.argv[1]
    if len(sys.argv) > 2:
        admin_url = sys.argv[2]
    if len(sys.argv) > 3:
        username = sys.argv[3]
    if len(sys.argv) > 4:
        password = sys.argv[4]

    payload = gather(check, username, password, admin_url) or {}
    if "generatedAt" not in payload:
        payload["generatedAt"] = datetime.utcnow().isoformat() + "Z"
    if "check" not in payload:
        payload["check"] = check or "all"
    if admin_url and "adminUrl" not in payload:
        payload["adminUrl"] = admin_url

    print(json.dumps(payload))


if __name__ == "__main__":
    main()
