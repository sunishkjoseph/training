# Remote Ops AI Agent (FastMCP + Streamlit)

This package adds a configurable AI agent that can run shell scripts on remote Linux servers through SSH.

## What is included

- `fastmcp_server.py`: FastMCP tool server exposing:
  - `list_servers`
  - `run_remote_script(server_name, script)`
- `streamlit_app.py`: Chat UI with model + server configuration controls.
- `remote_exec.py`: SSH command execution layer.
- `config.py`: YAML config loader with env-var expansion.
- `config.sample.yaml`: Starter configuration.

## Setup

```bash
cd remote-agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.sample.yaml config.yaml
```

Set your API key:

```bash
export OPENAI_API_KEY='your-key'
```

Update `config.yaml` with your real server hosts and SSH key.

## Run FastMCP server

SSE transport (remote tool endpoint):

```bash
python fastmcp_server.py --config config.yaml --transport sse --host 0.0.0.0 --port 9000
```

STDIO transport (for local MCP clients):

```bash
python fastmcp_server.py --config config.yaml --transport stdio
```

## Run Streamlit frontend

```bash
streamlit run streamlit_app.py
```

In the Streamlit sidebar, you can adjust:

- OpenAI model name
- Server inventory JSON
- Config file path

## Security notes

- Use least-privileged SSH users.
- Keep `strict_host_key_checking: true` in production.
- Avoid giving destructive commands unless explicitly requested.
- Consider command allow-lists before exposing this agent beyond trusted teams.
