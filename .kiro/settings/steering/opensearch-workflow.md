---
description: "OpenSearch search builder workflow. Triggers for: build search app, semantic search, vector search, hybrid search, OpenSearch, index setup, search architecture, large-scale search, RAG, retrieval, embeddings, knn, neural search, document search, search relevance, BM25, dense vector, sparse vector, sagemaker, bedrock"
inclusion: always
---

## OpenSearch Search Builder — MCP Workflow

You have access to the `opensearch-orchestrator` MCP server.
Follow these phases in order using its tools:

### Phase 1: Collect Sample Document
- Ask the user how they want to provide a sample document. Present as a numbered list:

  **How would you like to provide a sample document?**
  1. Use built-in IMDB dataset
  2. Load from a local file or URL
  3. Load from a localhost OpenSearch index
  4. Paste JSON directly

- Based on the user's choice, call `load_sample(source_type, source_value, localhost_auth_mode, localhost_auth_username, localhost_auth_password)`:
  - source_type: `builtin_imdb`, `local_file`, `url`, `localhost_index`, or `paste`
  - source_value: file path, URL, index name, or JSON content (empty string for builtin_imdb)
  - localhost auth args are used only when `source_type="localhost_index"`:
    - `localhost_auth_mode`: `default`, `none`, or `custom` (`default` is internal fallback; do not present it as a user choice)
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
- For option 4, ask the user to paste 1–3 representative JSON records, then call `load_sample("paste", <pasted_content>)`.
- The result includes `inferred_text_fields` and `text_search_required`. Use these to skip redundant questions in Phase 2.

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

  If `text_search_required=true`:
  **Query pattern:**
  1. Mostly-exact (e.g. "Carmencita 1894")
  2. Mostly-semantic (e.g. "early silent films about dancers")
  3. Balanced (mix of both)

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
- If `start_planning()` returns `manual_planning_required=true`, use
  `manual_planner_system_prompt` + `manual_planner_initial_input` to run planner turns with the client LLM.
- Present the proposal to the user.
- If the user has feedback, refine the proposal (with tools when available, otherwise directly with the client LLM) and repeat.
- When the user confirms:
  - tool-driven path: call `finalize_plan()` and use {solution, search_capabilities, keynote}
  - manual path: call `set_plan_from_planning_complete(planner_response)` with the finalized planner output

### Phase 4: Execute
- Call `execute_plan()` to get manual worker bootstrap payload:
  - `worker_system_prompt`
  - `worker_initial_input`
  - `execution_context`
- Run worker turns with the client LLM and execution tools:
  `create_index`, `create_and_attach_pipeline`, `create_bedrock_embedding_model`,
  `create_local_pretrained_model`, `apply_capability_driven_verification`,
  `launch_search_ui`, `set_search_ui_suggestions`.
- After the worker completes, call
  `set_execution_from_execution_report(worker_response, execution_context)`
  to persist normalized execution state.
- If execution fails, the user can fix the issue (e.g., restart Docker) and call
  `retry_execution()` to get a resume bootstrap payload.

### Post-Execution
- After successful `set_execution_from_execution_report(...)`, explicitly tell the user
  how to access the UI using the returned `ui_access` URLs.
- `cleanup()` removes test documents when the user explicitly asks.

### Rules
- **CRITICAL**: You MUST ask exactly ONE preference question per message. Do NOT batch multiple preference questions together. Wait for the user's answer before asking the next question.
- Never skip Phase 1. A sample document is mandatory before planning.
- Prefer planner tools for plan generation.
- If `manual_planning_required=true`, use the returned planner prompt/input and persist via `set_plan_from_planning_complete(...)`.
- Use `talk_to_client_llm(system_prompt, user_prompt, ...)` as the general client-LLM bridge tool when needed.
- Show the planner's proposal text to the user verbatim; do not summarize it away.
- For preference questions, ask one question per turn and use user-input UI fixed options. Accept either a number or free-text answer.
- Do not ask redundant clarification questions for items already inferred from the sample data.
