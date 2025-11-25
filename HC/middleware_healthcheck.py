#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Middleware health-check wrapper for WLST-based health collector.

- Reads configuration from a YAML file (sample_config.yaml style)
- Invokes WLST (or python for local testing) with wlst_health_checks.py
- Captures stdout (including WLST banner) into a temp file
- Extracts only the JSON payload from that text and parses it
- Prints per-check banners like:
      --- THREADS ---
  and can be extended to write combined reports.
"""

from __future__ import print_function

import argparse
import datetime
import json
import os
import sys
import tempfile
import subprocess

try:
    import yaml
except ImportError:
    yaml = None  # You can replace this with your own minimal YAML loader if needed


# ---------------------------------------------------------------------------
# Helper: safe printing
# ---------------------------------------------------------------------------

def eprint(*args, **kwargs):
    """Print to stderr."""
    kwargs.setdefault("file", sys.stderr)
    print(*args, **kwargs)


# ---------------------------------------------------------------------------
# JSON extraction from WLST banner output
# ---------------------------------------------------------------------------

def extract_json_from_wlst_output(raw_output):
    """
    Given the *full* WLST stdout (including banner lines), return the JSON substring.

    Strategy:
    - Find the first '{' or '[' in the entire string -> JSON start.
    - Find the last '}' or ']' in the entire string -> JSON end.
    - Take that slice and strip whitespace.
    """

    if not raw_output:
        raise ValueError("WLST output is empty")

    start_idx = None
    for i, ch in enumerate(raw_output):
        if ch in ('{', '['):
            start_idx = i
            break

    if start_idx is None:
        raise ValueError("No JSON start character ('{' or '[') found in WLST output")

    last_curly = raw_output.rfind('}')
    last_square = raw_output.rfind(']')
    end_idx = max(last_curly, last_square)

    if end_idx == -1 or end_idx < start_idx:
        raise ValueError("No valid JSON end character ('}' or ']') found in WLST output")

    json_chunk = raw_output[start_idx:end_idx + 1].strip()
    if not json_chunk:
        raise ValueError("Extracted JSON chunk is empty after trimming")

    return json_chunk


# ---------------------------------------------------------------------------
# Config / YAML helpers
# ---------------------------------------------------------------------------

def load_config(path):
    """Load YAML configuration."""
    if not os.path.exists(path):
        raise IOError("Config file not found: {0}".format(path))

    if yaml is None:
        raise RuntimeError("PyYAML is not installed but is required to load {0}".format(path))

    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}
    return data


# ---------------------------------------------------------------------------
# WLST invocation and JSON parsing
# ---------------------------------------------------------------------------

def run_wlst_check(check, cfg):
    """
    Run one WLST check (cluster, managed_servers, jms, threads, datasource, deployments, composites, all)
    using the settings from the YAML config:
        - admin_url
        - username
        - password
        - wlst_path (e.g. wlst.sh or python3 for local testing)
        - wlst_script (e.g. wlst_health_checks.py)
        - wlst_sample_output (optional for local testing)
    """

    admin_url = cfg.get("admin_url")
    username = cfg.get("username")
    password = cfg.get("password")

    if not (admin_url and username and password):
        raise RuntimeError("admin_url, username, and password must be set in the config")

    wlst_path = cfg.get("wlst_path", "wlst.sh")
    wlst_script = cfg.get("wlst_script", "wlst_health_checks.py")

    env = os.environ.copy()
    # For local dry-run / testing: wlst_path can be "python3", and WLST_SAMPLE_OUTPUT points
    # to a JSON file. The wlst_health_checks.py script knows how to use it.
    sample_output = cfg.get("wlst_sample_output")
    if sample_output:
        env["WLST_SAMPLE_OUTPUT"] = sample_output

    cmd = [
        wlst_path,
        wlst_script,
        check,
        admin_url,
        username,
        password,
    ]

    eprint("[INFO] Executing:", " ".join(cmd))

    # Capture stdout & stderr
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        universal_newlines=True  # text mode
    )
    stdout, stderr = proc.communicate()

    # Always write raw output to a temp file for debugging, as you suggested
    tmp_fd, tmp_path = tempfile.mkstemp(prefix="wlst_raw_{0}_".format(check), suffix=".log")
    os.close(tmp_fd)
    try:
        with open(tmp_path, "w") as tmp_f:
            tmp_f.write(stdout)
            if stderr:
                tmp_f.write("\n--- STDERR ---\n")
                tmp_f.write(stderr)
    except Exception:
        # If writing fails, we ignore, because it's only for debugging
        pass

    if proc.returncode != 0:
        # WLST itself failed
        msg = "[ERROR] WLST returned {code}: {err}".format(
            code=proc.returncode,
            err=stderr.strip() or stdout.strip()
        )
        eprint(msg)
        eprint("[ERROR] Raw WLST output written to:", tmp_path)
        raise RuntimeError(msg)

    # Now parse JSON part
    try:
        json_str = extract_json_from_wlst_output(stdout)
        data = json.loads(json_str)
    except Exception as exc:
        eprint(
            "[ERROR] Unable to decode WLST output as JSON: {0}".format(exc)
        )
        eprint("Raw output:")
        eprint(stdout)
        eprint("[ERROR] Raw WLST output saved in:", tmp_path)
        raise

    return data


# ---------------------------------------------------------------------------
# Result handling / reporting
# ---------------------------------------------------------------------------

def print_section_header(title):
    print("")
    print("--- {0} ---".format(title.upper()))
    print("")


def run_all_checks(cfg, checks):
    """
    Run all requested checks and collect the results into a dict.
    checks is a list of (check_name, title_string).
    """
    all_results = {}

    for check_name, title in checks:
        print_section_header(title)
        try:
            result = run_wlst_check(check_name, cfg)
            # Merge / store by check name
            all_results[check_name] = result
            # For now, just pretty-print JSON per section.
            print(json.dumps(result, indent=2))
        except Exception:
            # Error already logged; continue to the next check.
            continue

    return all_results


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Middleware health-check wrapper for WLST health script."
    )
    parser.add_argument(
        "config",
        nargs="?",
        default="sample_config.yaml",
        help="Path to YAML config file (default: sample_config.yaml)",
    )
    parser.add_argument(
        "--check",
        dest="check",
        default=None,
        help=(
            "Single check to run. One of: cluster, managed_servers, jms, "
            "threads, datasource, deployments, composites, all. "
            "If omitted, uses config['full'] to decide whether to run all."
        ),
    )
    return parser


def main():
    parser = build_arg_parser()
    args = parser.parse_args()

    try:
        cfg = load_config(args.config)
    except Exception as exc:
        eprint("[ERROR] Failed to load config {0}: {1}".format(args.config, exc))
        sys.exit(1)

    # Default set of checks (used when full: true or check=all)
    full_checks = [
        ("cluster", "clusters"),
        ("managed_servers", "managed servers"),
        ("jms", "jms servers"),
        ("threads", "threads"),
        ("datasource", "datasources"),
        ("deployments", "deployments"),
        ("composites", "soa composites"),
    ]

    # Decide what to run
    check = args.check
    if not check:
        # If no explicit --check, look at config.full
        if bool(cfg.get("full", True)):
            check = "all"
        else:
            # As a reasonable default, run only threads when full=false
            check = "threads"

    if check == "all":
        # Run everything
        results = run_all_checks(cfg, full_checks)
    else:
        # Run a single check
        title = check
        for name, t in full_checks:
            if name == check:
                title = t
                break
        print_section_header(title)
        try:
            result = run_wlst_check(check, cfg)
            print(json.dumps(result, indent=2))
            results = {check: result}
        except Exception:
            results = {}

    # At this point you can pass `results` into your report_wrapper.py if needed,
    # or write consolidated JSON/CSV files.
    # Example: write a combined JSON with timestamp:
    try:
        ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        out_name = "middleware_health_report_{0}.json".format(ts)
        with open(out_name, "w") as f:
            json.dump(results, f, indent=2)
        print("")
        print("[INFO] Combined report written to:", out_name)
    except Exception as exc:
        eprint("[WARN] Failed to write combined report JSON:", exc)


if __name__ == "__main__":
    main()
