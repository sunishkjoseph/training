from __future__ import annotations

import argparse

from fastmcp import FastMCP

from config import load_config
from remote_exec import RemoteExecutor


def build_mcp(config_path: str) -> FastMCP:
    runtime = load_config(config_path)
    executor = RemoteExecutor(
        runtime.servers,
        timeout_seconds=runtime.timeout_seconds,
        max_output_chars=runtime.max_output_chars,
    )

    mcp = FastMCP("remote-ops-mcp")

    @mcp.tool(description="Run a shell script on one configured remote server over SSH")
    def run_remote_script(server_name: str, script: str) -> dict:
        return executor.run_remote_script(server_name=server_name, script=script)

    @mcp.tool(description="List configured remote servers")
    def list_servers() -> dict:
        return {
            "servers": {
                name: {
                    "host": s.host,
                    "user": s.user,
                    "port": s.port,
                }
                for name, s in runtime.servers.items()
            }
        }

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the remote-ops FastMCP server")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config")
    parser.add_argument("--transport", default="sse", choices=["sse", "stdio"])  # streamable transports
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()

    mcp = build_mcp(args.config)
    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="sse", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
