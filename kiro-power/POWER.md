---
name: "opensearch-search-builder"
displayName: "Build a POC search application with OpenSearch"
description: "Accelerate proof-of-concept search applications with guided, end-to-end architecture planning. Ingests sample documents, captures preferences, designs the solution architecture, and provisions indices, ML models, ingest pipelines, and a search UI."
keywords: ["opensearch", "search", "semantic search", "vector search", "hybrid search", "RAG", "embeddings", "knn", "neural search", "BM25", "index", "search architecture", "Amazon OpenSearch", "aws", "serverless", "Amazon OpenSearch Serverless"]
author: "AWS"
---

# Onboarding

## Prerequisites

1. **Python 3.10+** and `uv` installed ([Install uv](https://docs.astral.sh/uv/getting-started/installation/))
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
    "opensearch-orchestrator": {
      "command": "uvx",
      "args": ["opensearch-orchestrator@latest"],
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
    "opensearch-mcp-server": {
      "command": "uvx",
      "args": ["opensearch-mcp-server@latest"],
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
  - source_type: "builtin_imdb" | "local_file" | "url" | "localhost_index" | "paste"
  - source_value: file path, URL, index name, or pasted JSON content (empty string for builtin_imdb)
  - localhost auth args are used only for `source_type="localhost_index"`:
    - localhost_auth_mode: "default" | "none" | "custom"
    - localhost_auth_username / localhost_auth_password: required only when mode is "custom"
- For option 2, detect whether the user provided a local file path or URL and use `local_file` or `url`.
- For option 3, ask for index name first and default to `localhost_auth_mode="default"` unless the user explicitly asks for `none` or `custom`.
- For option 4, ask for 1-3 representative JSON records before calling `load_sample("paste", ...)`.
- The result includes `inferred_text_fields` and `text_search_required`.
- A sample document is required before any planning or execution.

### Phase 2: Gather Preferences
- Ask one preference question at a time, in this order.
- Present each question as a numbered list and ask the user to reply with the number of their choice.

  **Budget:**
  1. Flexible
  2. Cost-sensitive

  **Performance priority:**
  1. Speed-first
  2. Balanced
  3. Accuracy-first

  **Query pattern:**
  1. Mostly-exact (e.g. "Carmencita 1894")
  2. Mostly-semantic (e.g. "early silent films about dancers")
  3. Balanced (mix of both)

- If query pattern is balanced or mostly-semantic, ask deployment preference as a separate follow-up question:

  **Deployment preference:**
  1. OpenSearch node
  2. SageMaker endpoint
  3. External embedding API

- Call `set_preferences(budget, performance, query_pattern, deployment_preference)` with the values.

### Phase 3: Plan
- Call `start_planning()` to get the initial architecture proposal.
- If `start_planning()` returns `manual_planning_required=true`, use
  `manual_planner_system_prompt` + `manual_planner_initial_input` to run planner turns with the client LLM.
- Present the proposal to the user **verbatim** (do not summarize it away).
- If the user has feedback, refine the proposal (with tools when available, otherwise directly with the client LLM) and repeat.
- When the user confirms:
  - tool-driven path: call `finalize_plan()` and use {solution, search_capabilities, keynote}
  - manual path: call `set_plan_from_planning_complete(planner_response)` with the finalized planner output
- After plan finalization, write a manifest file to the customer's local directory documenting the recommended search strategy, including architecture decisions, model choices, index configuration, and pipeline setup.

### Phase 4: Execute
- Call `execute_plan()` to create the index, models, pipelines, and launch the UI.
- If execution fails, the user can fix the issue (e.g., restart Docker) and you
  call `retry_execution()`.

### Phase 5: Deploy to AWS OpenSearch (optional)
- After successful local execution, offer to deploy the search strategy to AWS OpenSearch.
- **Important**: Before starting Phase 5, guide the user to add AWS MCP servers to the power's mcp.json configuration (see AWS Setup in Onboarding section). Verify the servers are configured before proceeding.
- Choose deployment target based on search strategy:
  - **OpenSearch Serverless (AOSS)**: For Neural Sparse, Dense Vector, BM25, and Hybrid search
  - **OpenSearch Domain (AOS)**: Required for Agentic Search; also supports all other strategies
- Use AWS API MCP tools (from the aws-api-mcp-server) to provision resources.
- Use OpenSearch MCP tools (from opensearch-mcp-server) to interact with the deployed cluster.
- Follow the appropriate AWS deployment steering file:
  - `aws-opensearch-serverless.md` for AOSS deployment
  - `aws-opensearch-domain.md` for AOS deployment (Agentic Search)
- Migrate the local configuration (indices, models, pipelines) to AWS.
- Configure AWS-specific settings (IAM roles, security, network access).
- Provide the user with AWS endpoint URLs and access instructions.

### Post-Execution
- After successful `execute_plan()`/`retry_execution()`, explicitly tell the user
  how to access the UI using the `ui_access` URLs returned by the tool result.
- After Phase 5 AWS deployment, provide AWS endpoint URLs and configuration details.
- `cleanup()` removes test documents when the user explicitly asks.

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
| `retry_execution` | 4 | Resume from a failed execution step |
| `cleanup` | Post | Remove test documents on user request |

### Knowledge Tools
| Tool | Description |
|------|-------------|
| `read_knowledge_base` | Read the OpenSearch Semantic Search Guide |
| `read_dense_vector_models` | Read the Dense Vector Models Guide |
| `read_sparse_vector_models` | Read the Sparse Vector Models Guide |
| `search_opensearch_org` | Search opensearch.org documentation |

### Operation Tools (advanced)
| Tool | Description |
|------|-------------|
| `create_index` | Create an OpenSearch index with configuration |
| `create_bedrock_embedding_model` | Register a Bedrock embedding model |
| `create_local_pretrained_model` | Deploy a local pretrained model |
| `create_and_attach_pipeline` | Create and attach ingest/search pipelines |
| `index_doc` | Index a document |
| `delete_doc` | Delete a document |

## Rules
- **CRITICAL**: You MUST ask exactly ONE preference question per message. Do NOT batch multiple preference questions together. Wait for the user's answer before asking the next question.
- Never skip Phase 1. A sample document is mandatory before planning.
- Prefer planner tools for plan generation.
- If `manual_planning_required=true`, use the returned planner prompt/input and persist via `set_plan_from_planning_complete(...)`.
- Show the planner's proposal text to the user verbatim; do not summarize it away.
- For preference questions, present numbered lists. Accept either a number or a free-text answer.
- Do not ask redundant clarification questions for items already inferred from the sample data.
- Phase 5 (AWS deployment) is optional and should only be offered after successful Phase 4 execution.

## Prerequisites
- Python 3.10+, uv, and Docker are required for Phases 1-4 (local development)
- AWS credentials and MCP servers are required for Phase 5 (AWS deployment) - see AWS Setup section
- See the [Onboarding](#onboarding) section for detailed setup instructions
