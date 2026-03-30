from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ServerConfig:
    host: str
    user: str
    port: int = 22
    key_path: str | None = None
    strict_host_key_checking: bool = True


@dataclass
class RuntimeConfig:
    openai_api_key: str
    model: str
    system_prompt: str
    timeout_seconds: int
    max_output_chars: int
    servers: dict[str, ServerConfig]


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load_config(path: str | os.PathLike[str]) -> RuntimeConfig:
    cfg_path = Path(path)
    raw = yaml.safe_load(cfg_path.read_text())
    raw = _expand_env(raw)

    openai_cfg = raw.get("openai", {})
    agent_cfg = raw.get("agent", {})
    exec_cfg = raw.get("execution", {})

    servers: dict[str, ServerConfig] = {}
    for name, server in raw.get("servers", {}).items():
        servers[name] = ServerConfig(
            host=server["host"],
            user=server["user"],
            port=int(server.get("port", 22)),
            key_path=server.get("key_path"),
            strict_host_key_checking=bool(server.get("strict_host_key_checking", True)),
        )

    api_key = openai_cfg.get("api_key") or os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError("OpenAI API key missing. Set openai.api_key or OPENAI_API_KEY.")

    return RuntimeConfig(
        openai_api_key=api_key,
        model=openai_cfg.get("model", "gpt-4.1-mini"),
        system_prompt=agent_cfg.get(
            "system_prompt",
            "You are a remote operations assistant. Use tools for shell tasks.",
        ),
        timeout_seconds=int(exec_cfg.get("timeout_seconds", 120)),
        max_output_chars=int(exec_cfg.get("max_output_chars", 12000)),
        servers=servers,
    )
