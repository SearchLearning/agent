This repo uses the `strands` framework to build an OpenSearch semantic search solution architect agent. The agent collects user requirements and gives recommendations for index types.

There are two ways to use the agent: as a **standalone interactive CLI** or via an **MCP server** that any MCP-compatible client can drive.

## Standalone Agent

Start the interactive orchestrator in a terminal:

```bash
python opensearch_orchestrator/orchestrator.py
```

The orchestrator guides you through sample collection, requirements gathering, solution planning, and execution — all in one interactive session.

## MCP Server (Cursor, Claude Desktop, etc.)

The MCP server exposes the same orchestrator workflow as a set of phase tools. A client LLM drives the conversation with the user and calls the tools in order.

### Prerequisites

Install [uv](https://docs.astral.sh/uv/) (one-time, no sudo needed):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Running manually

```bash
uv run opensearch_orchestrator/mcp_server.py
```

`uv` reads the inline script metadata in `opensearch_orchestrator/mcp_server.py` and auto-installs dependencies into a cached virtual environment.

### Running from PyPI (`uvx`)

After publishing to PyPI, run the MCP server without cloning the repo:

```bash
uvx opensearch-orchestrator@latest
```

If you install via `pip`, you can also run:

```bash
opensearch-orchestrator
```

Important: this command starts a stdio MCP server (JSON-RPC), not an interactive CLI. It should be launched by an MCP client such as Cursor, Claude Desktop, or MCP Inspector. If you want an interactive terminal workflow, run:

```bash
python opensearch_orchestrator/orchestrator.py
```

### MCP workflow tools

The server exposes high-level phase tools that mirror the standalone orchestrator workflow:

| Tool | Phase | Description |
|------|-------|-------------|
| `load_sample` | 1 | Load a sample document (built-in, file, URL, index, or paste); localhost-index mode supports explicit auth mode/credentials |
| `set_preferences` | 2 | Set budget, performance, query pattern, deployment preferences |
| `start_planning` | 3 | Start the planning agent; returns initial architecture proposal |
| `refine_plan` | 3 | Send user feedback to refine the proposal |
| `finalize_plan` | 3 | Finalize the plan when the user confirms |
| `talk_to_client_llm` | 3/4 | General MCP client-sampling bridge for client LLM turns |
| `set_plan_from_planning_complete` | 3 | Parse/store a `<planning_complete>` planner response |
| `execute_plan` | 4 | Return manual worker bootstrap payload (no server-side Bedrock execution in MCP) |
| `set_execution_from_execution_report` | 4 | Parse/store normalized `<execution_report>` and update retry state |
| `retry_execution` | 4 | Return resume bootstrap payload from last failed step |
| `cleanup` | Post | Remove test documents on user request |

The following execution/knowledge tools are exposed by default for manual client-driven execution:
`create_index`, `create_and_attach_pipeline`, `create_bedrock_embedding_model`,
`create_local_pretrained_model`, `apply_capability_driven_verification`,
`launch_search_ui`, `set_search_ui_suggestions`, `read_knowledge_base`,
`read_dense_vector_models`, `read_sparse_vector_models`, `search_opensearch_org`.

Advanced tools (`set_plan`, raw sample-submit variants, indexing helpers, etc.) are hidden by default and only exposed when `OPENSEARCH_MCP_ENABLE_ADVANCED_TOOLS=true`.

Localhost index auth contract (Option 3 / `source_type="localhost_index"`):
- `localhost_auth_mode="default"`: force username `admin` with password `myStrongPassword123!`
- `localhost_auth_mode="none"`: force no authentication
- `localhost_auth_mode="custom"`: require `localhost_auth_username` + `localhost_auth_password`
- Local Docker auto-bootstrap always uses the `admin` username and sets `OPENSEARCH_INITIAL_ADMIN_PASSWORD` from `OPENSEARCH_PASSWORD` when provided (otherwise uses `myStrongPassword123!`).

Planner backend in MCP mode:
- MCP planning uses client sampling / client LLM only (no Bedrock fallback in MCP mode).
- Manual fallback: if the MCP client does not support `sampling/createMessage`,
  `start_planning` returns `manual_planning_required=true` plus
  `manual_planner_system_prompt` and `manual_planner_initial_input`; run planner turns
  with the client LLM and call `set_plan_from_planning_complete(planner_response)`.

### Cursor integration

1. Add the following to `.cursor/mcp.json` in your workspace (adjust `cwd` to the repo path):

```json
{
  "mcpServers": {
    "opensearch-orchestrator": {
      "command": "uv",
      "args": ["run", "opensearch_orchestrator/mcp_server.py"],
      "cwd": "/path/to/agent-poc"
    }
  }
}
```

2. Reload the Cursor window (`Cmd+Shift+P` → "Developer: Reload Window"), then enable the server in **Cursor Settings → MCP**.

3. A Cursor rule at `.cursor/rules/opensearch-workflow.mdc` auto-activates when you ask about OpenSearch solution design and teaches the LLM the tool sequence.

If Cursor cannot find `uv` on its PATH, use the absolute path (e.g. `~/.local/bin/uv`).

### Claude Desktop integration

1. Copy `claude_desktop_config.example.json` to your Claude Desktop config directory:

   - **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

2. Edit the `cwd` path to point to this repo.

3. Restart Claude Desktop. The `opensearch_workflow` prompt is available in the prompt picker and describes the full tool sequence.

### Generic MCP clients

Any MCP-compatible client can connect via stdio and discover tools with `tools/list`. The `opensearch_workflow` prompt (available via `prompts/list`) describes the workflow. Tool docstrings also include prerequisite hints.

### Without uv

If you prefer not to install `uv`, install dependencies manually and use Python directly:

```bash
pip install mcp opensearch-py
```

```json
{
  "mcpServers": {
    "opensearch-orchestrator": {
      "command": "python3",
      "args": ["opensearch_orchestrator/mcp_server.py"],
      "cwd": "/path/to/agent-poc"
    }
  }
}
```

## Release checklist

Build and validate before publishing:

```bash
# 1) bump version manually (not automatic)
#    update both files to the same value, e.g. 0.10.1
#    - pyproject.toml: [project].version
#    - opensearch_orchestrator/__init__.py: __version__
#
# optional sanity check:
python -c "import tomllib; p=tomllib.load(open('pyproject.toml','rb')); import opensearch_orchestrator as pkg; print('pyproject=', p['project']['version'], 'package=', pkg.__version__)"

# 2) all tests have to pass
uv run pytest -q

# 3) build and verify artifacts
uv build
for whl in dist/*.whl; do python -m zipfile -l "$whl"; done
python -c "import opensearch_orchestrator.mcp_server as m; print(hasattr(m, 'main'))"
# pick wheel for the current package version (avoids selecting older builds)
VERSION="$(python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")"
WHEEL_PATH="$(ls dist/opensearch_orchestrator-${VERSION}-*.whl)"
uvx --from "$WHEEL_PATH" opensearch-orchestrator

# 4) upload to PyPI (needs a PyPI account + API token)
uv publish --token pypi-YOUR-TOKEN
```

Then publish to TestPyPI for smoke tests, followed by PyPI.
