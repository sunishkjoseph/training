from __future__ import annotations

import shlex
import subprocess
from dataclasses import asdict

from config import ServerConfig


class RemoteExecutor:
    def __init__(self, servers: dict[str, ServerConfig], timeout_seconds: int, max_output_chars: int) -> None:
        self.servers = servers
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars

    def run_remote_script(self, server_name: str, script: str) -> dict:
        if server_name not in self.servers:
            return {
                "ok": False,
                "error": f"Unknown server '{server_name}'. Available: {', '.join(self.servers.keys())}",
            }

        server = self.servers[server_name]
        ssh_target = f"{server.user}@{server.host}"

        ssh_cmd = ["ssh", "-p", str(server.port)]
        if server.key_path:
            ssh_cmd.extend(["-i", server.key_path])
        if not server.strict_host_key_checking:
            ssh_cmd.extend(["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"])
        ssh_cmd.extend([ssh_target, "bash -lc " + shlex.quote(script)])

        try:
            proc = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "server": asdict(server),
                "script": script,
                "error": f"Timed out after {self.timeout_seconds} seconds",
            }

        stdout = (proc.stdout or "")[: self.max_output_chars]
        stderr = (proc.stderr or "")[: self.max_output_chars]
        return {
            "ok": proc.returncode == 0,
            "server": asdict(server),
            "script": script,
            "returncode": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": len(proc.stdout or "") > self.max_output_chars
            or len(proc.stderr or "") > self.max_output_chars,
        }
