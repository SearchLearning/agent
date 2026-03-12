---
name: "opensearch-launchpad"
displayName: "OpenSearch Launchpad"
description: "Accelerate proof-of-concept search applications with guided, end-to-end architecture planning. Ingests sample documents, captures preferences, designs the solution architecture, provisions indices, ML models, ingest pipelines, and a search UI locally, then deploys to Amazon OpenSearch Service or Amazon OpenSearch Serverless."
keywords: ["opensearch", "search", "semantic search", "vector search", "hybrid search", "RAG", "embeddings", "knn", "neural search", "BM25", "index", "search architecture", "Amazon OpenSearch", "aws", "serverless", "Amazon OpenSearch Serverless"]
author: "AWS"
---

# Onboarding

## Prerequisites

1. **Python 3.11+** and `uv` installed ([Install uv](https://docs.astral.sh/uv/getting-started/installation/))
2. **Docker** installed and running ([Download Docker](https://docs.docker.com/get-docker/))
3. **For Phase 5 (AWS deployment)**: AWS credentials configured

## AWS Setup (for Phase 5 deployment)

Phase 5 (AWS deployment) is optional. Only complete this setup if you want to deploy to AWS OpenSearch.

### Step 1: Add AWS MCP Servers

Before starting Phase 5, add the required MCP servers to your power configuration:

1. Open the power's `mcp.json` file (located in the power directory)
2. Add the following servers to the `mcpServers` section:

```json
{
  "mcpServers": {
    "opensearch-launchpad": {
      "command": "uvx",
      "args": ["opensearch-launchpad@latest"],
      "env": {
        "FASTMCP_LOG_LEVEL": "ERROR"
      },
      "disabled": false,
      "autoApprove": []
    },
    "awslabs.aws-api-mcp-server": {
      "command": "uvx",
      "args": ["awslabs.aws-api-mcp-server@latest"],
      "env": {
        "FASTMCP_LOG_LEVEL": "ERROR"
      },
      "disabled": false,
      "autoApprove": []
    },
    "aws-docs": {
      "command": "uvx",
      "args": ["awslabs.aws-documentation-mcp-server@latest"],
      "env": {
        "FASTMCP_LOG_LEVEL": "ERROR"
      },
      "disabled": false,
      "autoApprove": []
    },
    "opensearch-mcp-server": {
      "command": "uvx",
      "args": ["opensearch-mcp-server-py@latest"],
      "env": {
        "FASTMCP_LOG_LEVEL": "ERROR"
      },
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

3. Save the file and restart Kiro or reconnect the MCP servers

### Step 2: Install AWS CLI

Install AWS CLI if not already installed:

```bash
# macOS
brew install awscli

# Linux
pip install awscli

# Windows
# Download from https://aws.amazon.com/cli/
```

### Step 3: Configure AWS Credentials

Choose one method:

**Option A: AWS CLI configuration** (recommended):
```bash
aws configure
# Enter your AWS Access Key ID
# Enter your AWS Secret Access Key
# Enter your default region (e.g., us-east-1)
# Enter default output format (json)
```

**Option B: Environment variables**:
```bash
export AWS_ACCESS_KEY_ID="your-access-key"
export AWS_SECRET_ACCESS_KEY="your-secret-key"
export AWS_REGION="us-east-1"
```

### Step 4: Verify Setup

Verify AWS credentials:
```bash
aws sts get-caller-identity
```

### Step 5: Ensure IAM Permissions

Your AWS user/role needs permissions for:
- OpenSearch Service: Create/manage domains and serverless collections
- IAM: Create and manage roles for OpenSearch
- Bedrock: Invoke models (for semantic search and agentic search)

Once configured, the AWS MCP servers will be available for Phase 5 deployment.

## Troubleshooting

### If you get a `spawn uvx ENOENT` error or If Docker is running but the MCP server can't find it

Some MCP clients may be unable to find `uvx` or `docker` from the JSON config
environment. This will result in error messages like
`Could not connect to MCP server dbt-mcp`, `Error: spawn uvx ENOENT`, or Docker
not found errors even when Docker is installed and running.

Solution: Locate the full path to `uvx` and `docker`, then ensure your MCP
`env.PATH` includes that directory:

macOS/Linux:
- Run `which uvx`
- Run `which docker` (example output: `/usr/local/bin/docker`)

Windows:
- Run `where uvx`
- Run `where docker`

If `which docker` returns `/usr/local/bin/docker`, add `/usr/local/bin` to
`env.PATH` in your MCP config.

1. Open the Command Palette in Kiro (`Cmd+Shift+P` on macOS, `Ctrl+Shift+P` on Windows/Linux), then run `Kiro: Open user MCP config (JSON)` (or open workspace MCP config).
2. In `mcpServers`, find the namespaced server entry (for example, `power-kiro-power-opensearch-launchpad`) and update it to match the example below:

```jsonc
{
  "mcpServers": {
    "opensearch-launchpad": {
      "command": "uvx",
      "args": [
        "opensearch-launchpad@latest"
      ],
      "env": {
        "FASTMCP_LOG_LEVEL": "ERROR",
        "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/anaconda3/bin"
      },
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

3. Save. Kiro applies changes on save and reconnects automatically (or reconnect from the MCP panel if needed).
4. If the connection still fails, open the MCP Server view and retry manually:
   - Go to `View` -> `Open View`
   - Type `MCP Servers`
   - Open the MCP Server view
   - Retry connect to `power-kiro-power-opensearch-launchpad`


## Quick Test

After configuration, try: *"I want to build a semantic search app with 10M docs"*

---

# Overview

An MCP-powered assistant that guides you from requirements to a running OpenSearch search setup.

This power provides an OpenSearch Search Solution building workflow. It collects a sample document, gathers your preferences (budget, performance, query pattern), plans an architecture using an AI planner agent, and executes the plan to create indices, models, pipelines, and a search UI.

## Workflow Phases

### Phase 1: Collect Sample Document (mandatory first step)
- Ask the user how they want to provide a sample document. Present as a numbered list:
  1. Use built-in IMDB dataset
  2. Load from a local file or URL
  3. Load from a localhost OpenSearch index
  4. Paste JSON directly
- Based on the user choice, call `load_sample(source_type, source_value, localhost_auth_mode, localhost_auth_username, localhost_auth_password)`.
  - source_type: `builtin_imdb`, `local_file`, `url`, `localhost_index`, or `paste`
  - source_value: file path, URL, index name, or pasted JSON content (empty string for builtin_imdb)
  - localhost auth args are used only for `source_type="localhost_index"`:
    - `localhost_auth_mode`: `default`, `none`, or `custom` (`default` is internal fallback; do not present it as a user-facing choice)
    - `localhost_auth_username` and `localhost_auth_password`: required only for `custom`
    - mode behavior:
      - `none` => force no authentication
      - `custom` => force provided username/password
      - `default` => force `admin` / `myStrongPassword123!` (internal-only fallback)
- For option 2, determine whether the user provided a local file path or a URL and use the appropriate source_type (`local_file` or `url`).
- For option 3 (localhost index):
  - Ask auth mode first only when needed with these user-facing choices: `none` (no-auth) or `custom` (username/password). Do not present `default` as a user-facing choice.
  - If the user does not explicitly request `none` or `custom`, set `localhost_auth_mode="default"` internally.
  - If auth mode is `custom`, ask for username and password first (before asking for index name). If already provided, do not ask again.
  - After auth details are ready (or immediately for `none`/`default`), call `load_sample("localhost_index", "", <mode>, <username>, <password>)` first to fetch available non-system indices.
  - Present the returned indices as a numbered list and ask the user to pick one (by number or exact name). Then call `load_sample("localhost_index", <selected_index>, <mode>, <username>, <password>)`.
  - If the user already supplied a candidate index name, still validate it against the returned index list and ask for re-selection if it does not exist.
  - If the user already provided both username and password, do not ask for credentials again.
  - If the selected index is empty (has no documents), explain the issue and offer alternatives: ingest at least one document and retry, provide a local file/URL (option 2), use built-in IMDB (option 1), or paste JSON (option 4).
- For option 4, ask the user to paste 1-3 representative JSON records, then call `load_sample("paste", <pasted_content>)`.
- The result includes `inferred_text_fields` and `text_search_required`. Use these to skip redundant questions in Phase 2.
- A sample document is required before any planning or execution.

### Phase 2: Gather Preferences
- Ask one preference question at a time, in this order.
- Present each question as a numbered list and ask the user to reply with the number of their choice.

- If `text_search_required=true`:
  **Query pattern:**
  1. Mostly-exact (e.g. "Carmencita 1894")
  2. Mostly-semantic (e.g. "early silent films about dancers")
  3. Balanced (mix of both)

  **Performance priority:**
  1. Speed-first
  2. Balanced
  3. Accuracy-first

  **Budget:**
  1. Flexible
  2. Cost-sensitive

- If `text_search_required=true` and query pattern is balanced or mostly-semantic, ask deployment preference as a separate follow-up question:

  **Deployment preference:**
  1. OpenSearch node
  2. SageMaker endpoint
  3. External embedding API

- If `text_search_required=false`, skip query-pattern and deployment-preference questions.
  Keep the solution numeric/filter/aggregation-first, and do not suggest changing or enriching
  data purely to force semantic search unless the user explicitly requests semantic search.
- Call `set_preferences(budget, performance, query_pattern, deployment_preference)`.

### Phase 3: Plan
- Call `start_planning()` to get the initial architecture proposal.
- If `start_planning()` returns `manual_planning_required=true`, follow the returned planner bootstrap payload and call `set_plan_from_planning_complete(planner_response)` after user confirmation.
- Present the proposal to the user.
- If the user has feedback or questions, call `refine_plan(user_feedback)`. Repeat as needed.
- When the user confirms:
  - tool-driven path: call `finalize_plan()` and use {solution, search_capabilities, keynote}
  - manual path: call `set_plan_from_planning_complete(planner_response)` with the finalized planner output

### Phase 4: Execute
- Call `execute_plan()` to run index/model/pipeline/UI setup.
- If `execute_plan()` returns manual execution bootstrap payload, follow it and then call `set_execution_from_execution_report(worker_response, execution_context)` to persist normalized execution state.
- If execution fails, the user can fix the issue (e.g., restart Docker) and call `retry_execution()`.

### Phase 4.5: Evaluate Search Quality (optional)
- After successful Phase 4 execution, offer to evaluate search quality before deploying to AWS.
- Ask the user: *"Would you like to evaluate the search quality? I can analyze relevance, coverage, and capability gaps, and suggest improvements."*
- If the user agrees, present exactly these two numbered options and wait for the user to choose:
  *"Which path works for you?"*
  *"1. With AWS credentials (Interactive UI + Auto-Judge)"*
  *"2. Without AWS credentials (LLM-generated evaluation)"*

#### CRITICAL ROUTING RULES:
- **If the user picks 1**: You MUST follow Path A below. Do NOT call `start_evaluation()`. Do NOT skip to Path B.
- **If the user picks 2**: You MUST follow Path B below. Do NOT call `launch_interactive_ui()`.

#### Path A: With AWS Credentials (Interactive UI + Auto-Judge)
- **Step A1**: Call `launch_interactive_ui()` to start the Interactive UI with a fresh judgment cache. Tell the user the URL.
- **Step A2**: Tell the user they can enter their AWS credentials directly in the UI by clicking the **🔑 AWS** button in the evaluation panel (visible after running a search). Credentials are sent to the local websocket server only and not stored on disk.
- **Step A3**: Tell the user to click **📊 Evaluate All** in the Interactive UI to auto-judge all queries, then review the dashboard.
- **Step A4**: Tell the user: *"When you're done reviewing, click **🚀 Send to Kiro** in the dashboard, then come back here and let me know."*
- **Step A5**: When the user responds after clicking Send to Kiro, call `get_interactive_evaluation()` to pull the results into the engine. The response includes `next_action` and `next_action_prompt` — follow those instructions to automatically present the restart-or-deploy choice to the user.
- Do NOT ask the user to configure AWS credentials via the terminal or paste them into the chat. The UI handles credential input directly.

#### Path B: Without AWS Credentials (LLM-Generated Evaluation)
- Call `start_evaluation()` to have the client LLM analyze the plan and verification queries.
- If `start_evaluation()` returns `manual_evaluation_required=true`, act as the evaluator using the returned prompt and call `set_evaluation_from_evaluation_complete(evaluator_response)`.

#### After Evaluation (both paths)
- Present the evaluation findings to the user, including:
  - `search_quality_summary`: overall quality narrative
  - `issues`: identified problems or gaps
  - `suggested_preferences`: recommended `set_preferences` arguments for a fresh iteration
- If `suggested_preferences` is non-empty, offer to start over with the new preferences. Present the specific changes and ask:
  *"The evaluation suggests restarting with these preference changes: [list changes]. This will affect hybrid weights, model selection, and pipeline configuration. Would you like to restart with these improvements?"*
- If the user agrees to restart, go back to Phase 1 (`load_sample`) and apply the suggested preferences automatically in Phase 2.
- If the user declines evaluation or declines to restart, proceed to Phase 5.

### Phase 5: Deploy to AWS OpenSearch (optional)
- After successful local execution, offer to deploy the search strategy to AWS OpenSearch.
- **Important**: If the user already configured AWS credentials during Phase 4.5 Path A, skip credential setup. Otherwise, guide the user through the AWS Setup in the Onboarding section before proceeding.
- Call `prepare_aws_deployment()` to get the deployment context. This returns:
  - `deployment_target`: "serverless" or "domain" (determined automatically from search strategy)
  - `search_strategy`: the detected strategy (bm25, neural_sparse, dense_vector, hybrid, agentic)
  - `steering_files`: ordered list of steering files to follow
  - `local_config`: extracted configuration from local setup (text fields, sample doc, preferences)
  - `required_mcp_servers`: MCP servers that must be connected
  - `state_file_template`: JSON template for `.opensearch-deploy-state.json`
- Write the `state_file_template` to `.opensearch-deploy-state.json` in the project root.
- Verify all `required_mcp_servers` are connected before proceeding.
- Follow each steering file from `steering_files` in order:
  - **Serverless track**: `serverless-01-provision.md` → `serverless-02-deploy-search.md`
  - **Domain track**: `domain-01-provision.md` → `domain-02-deploy-search.md` → `domain-03-agentic-setup.md` (agentic only)
- Between each steering file, read `.opensearch-deploy-state.json` for accumulated state (ARNs, IDs, endpoints).
- After each major step, update `.opensearch-deploy-state.json` with new values as instructed by the steering file.
- For cost, security, or HA questions, refer to `aws-reference.md`.
- Provide the user with AWS endpoint URLs and access instructions.

### Post-Execution
- After successful Phase 4 execution, show the standalone Search Builder UI URL
  from `ui_access.primary_url` (e.g. `http://localhost:8765`).
  Do NOT show the interactive UI URL here — the interactive UI is only
  offered in Phase 4.5 Path A via `launch_interactive_ui`.
- `cleanup()` removes test documents when the user explicitly asks.
- After Phase 5 AWS deployment, provide AWS endpoint URLs and configuration details.
- After Phase 5, call `connect_search_ui_to_endpoint(endpoint, port, use_ssl, username, password, index_name)` to switch the Search UI to the AWS endpoint so the user can test searches against the cloud deployment directly from the same UI. The UI header badge will update to show "AWS Cloud" with a green connection dot.

## Available Tools

### High-Level Workflow Tools
| Tool | Phase | Description |
|------|-------|-------------|
| `load_sample` | 1 | Load a sample document (built-in, file, URL, index, or paste) |
| `set_preferences` | 2 | Set budget, performance, query pattern, deployment preferences |
| `start_planning` | 3 | Start planning; may return `manual_planning_required` with planner prompt/input |
| `refine_plan` | 3 | Send user feedback to refine the proposal |
| `finalize_plan` | 3 | Finalize the plan when the user confirms |
| `set_plan_from_planning_complete` | 3 | Parse/store finalized planner output for manual planning mode |
| `execute_plan` | 4 | Execute the plan (create index, models, pipelines, UI) |
| `set_execution_from_execution_report` | 4 | Parse/store finalized worker output for manual execution mode |
| `retry_execution` | 4 | Resume from a failed execution step |
| `start_evaluation` | 4.5 | Evaluate search quality; may return `manual_evaluation_required` with evaluator prompt |
| `set_evaluation_from_evaluation_complete` | 4.5 | Parse/store evaluator output for manual evaluation mode |
| `get_interactive_evaluation` | 4.5 | Pull evaluation dashboard results from Interactive UI into the engine (after user runs Evaluate All) |
| `prepare_aws_deployment` | 5 | Get deployment target, local config, steering file list, and state template for AWS deployment |
| `connect_search_ui_to_endpoint` | 5 | Switch Search UI to query an AWS OpenSearch endpoint after deployment |
| `cleanup` | Post | Remove test documents on user request |

### Knowledge Tools
| Tool | Description |
|------|-------------|
| `read_knowledge_base` | Read the OpenSearch Semantic Search Guide |
| `read_dense_vector_models` | Read the Dense Vector Models Guide |
| `read_sparse_vector_models` | Read the Sparse Vector Models Guide |
| `search_opensearch_org` | Search opensearch.org documentation |

## Rules
- **CRITICAL**: You MUST ask exactly ONE preference question per message. Do NOT batch multiple preference questions together. Wait for the user's answer before asking the next question.
- Never skip Phase 1. A sample document is mandatory before planning.
- Prefer planner tools for plan generation.
- If `manual_planning_required=true`, use the returned planner prompt/input and persist via `set_plan_from_planning_complete(...)`.
- Show the planner's proposal text to the user verbatim; do not summarize it away.
- For preference questions, ask one question per turn and use user-input UI fixed options. Accept either a number or free-text answer.
- Do not ask redundant clarification questions for items already inferred from the sample data.
- Phase 4.5 (evaluation) is optional and should only be offered after successful Phase 4 execution. Always proactively mention it is available.
- If evaluation suggests new preferences, offer to restart from Phase 1 with those preferences pre-applied.
- Phase 5 (AWS deployment) is optional and should only be offered after successful Phase 4 execution.

## Prerequisites
- Python 3.11+, uv, and Docker are required for Phases 1-4 (local development)
- AWS credentials and MCP servers are required for Phase 5 (AWS deployment) - see AWS Setup section
- See the [Onboarding](#onboarding) section for detailed setup instructions
