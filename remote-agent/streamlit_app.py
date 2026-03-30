from __future__ import annotations

import json
from pathlib import Path

import streamlit as st
from openai import OpenAI

from config import RuntimeConfig, ServerConfig, load_config
from remote_exec import RemoteExecutor


def config_editor(default_path: str = "config.yaml") -> RuntimeConfig:
    st.sidebar.header("Configuration")
    config_path = st.sidebar.text_input("Config path", value=default_path)

    if not Path(config_path).exists():
        st.sidebar.warning(f"Config file {config_path} not found")
        st.stop()

    runtime = load_config(config_path)

    runtime.model = st.sidebar.text_input("Model", value=runtime.model)

    server_json = st.sidebar.text_area(
        "Servers JSON (optional override)",
        value=json.dumps(
            {
                name: {
                    "host": s.host,
                    "user": s.user,
                    "port": s.port,
                    "key_path": s.key_path,
                    "strict_host_key_checking": s.strict_host_key_checking,
                }
                for name, s in runtime.servers.items()
            },
            indent=2,
        ),
        height=280,
    )

    try:
        parsed_servers = json.loads(server_json)
        runtime.servers = {
            name: ServerConfig(
                host=cfg["host"],
                user=cfg["user"],
                port=int(cfg.get("port", 22)),
                key_path=cfg.get("key_path"),
                strict_host_key_checking=bool(cfg.get("strict_host_key_checking", True)),
            )
            for name, cfg in parsed_servers.items()
        }
    except Exception as exc:  # noqa: BLE001
        st.sidebar.error(f"Invalid server JSON: {exc}")

    return runtime


def run_agent(runtime: RuntimeConfig, user_prompt: str, chat_history: list[dict]) -> tuple[str, list[dict]]:
    client = OpenAI(api_key=runtime.openai_api_key)
    executor = RemoteExecutor(runtime.servers, runtime.timeout_seconds, runtime.max_output_chars)

    tools = [
        {
            "type": "function",
            "function": {
                "name": "run_remote_script",
                "description": "Run a command/script on one configured remote server",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "server_name": {"type": "string"},
                        "script": {"type": "string"},
                    },
                    "required": ["server_name", "script"],
                },
            },
        }
    ]

    messages = [{"role": "system", "content": runtime.system_prompt}] + chat_history + [
        {"role": "user", "content": user_prompt}
    ]

    for _ in range(4):
        response = client.chat.completions.create(
            model=runtime.model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0.1,
        )
        choice = response.choices[0].message

        if not choice.tool_calls:
            content = choice.content or "No response generated."
            messages.append({"role": "assistant", "content": content})
            return content, messages[1:]  # omit system message in session

        messages.append(choice.model_dump())
        for tool_call in choice.tool_calls:
            args = json.loads(tool_call.function.arguments)
            tool_result = executor.run_remote_script(
                server_name=args["server_name"],
                script=args["script"],
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": "run_remote_script",
                    "content": json.dumps(tool_result),
                }
            )

    return "Agent stopped after max tool-iteration limit.", messages[1:]


def main() -> None:
    st.set_page_config(page_title="Remote Ops Agent", layout="wide")
    st.title("Remote Ops AI Agent")
    st.caption("OpenAI-powered agent that can execute remote shell scripts over SSH.")

    runtime = config_editor()

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    for message in st.session_state.chat_history:
        if message["role"] in {"assistant", "user"}:
            with st.chat_message(message["role"]):
                st.write(message["content"])

    prompt = st.chat_input("Ask for diagnostics or command execution...")
    if prompt:
        with st.chat_message("user"):
            st.write(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                answer, updated_history = run_agent(runtime, prompt, st.session_state.chat_history)
                st.write(answer)
                st.session_state.chat_history = updated_history


if __name__ == "__main__":
    main()
