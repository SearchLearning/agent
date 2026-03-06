# /// script
# dependencies = ["anyio", "mcp", "opensearch-py", "pandas>=2.3.3", "pyarrow>=23.0.1", "strands-agents"]
# ///

"""MCP server exposing the OpenSearch orchestrator workflow as phase tools.

Clients (Cursor, Claude Desktop, generic MCP) call these tools in order:
  load_sample -> set_preferences -> start_planning -> refine_plan/finalize_plan -> execute_plan

Low-level domain tools can be optionally exposed for advanced use.
"""

from __future__ import annotations

if __package__ in {None, ""}:
    from pathlib import Path
    import sys

    _SCRIPT_EXECUTION_PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
    if _SCRIPT_EXECUTION_PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _SCRIPT_EXECUTION_PROJECT_ROOT)

import errno
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

import anyio
from mcp import types as mcp_types
from mcp.server.fastmcp import Context, FastMCP

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None

from opensearch_orchestrator.orchestrator import create_transport_agnostic_engine
from opensearch_orchestrator.planning_session import PlanningSession
from opensearch_orchestrator.scripts.shared import Phase
from opensearch_orchestrator.solution_planning_assistant import (
    SYSTEM_PROMPT as PLANNER_SYSTEM_PROMPT,
)
from opensearch_orchestrator.scripts.tools import (
    BUILTIN_IMDB_SAMPLE_PATH,
    submit_sample_doc,
    submit_sample_doc_from_local_file,
    submit_sample_doc_from_localhost_index,
    submit_sample_doc_from_url,
    get_sample_docs_for_verification,
    read_knowledge_base,
    read_agentic_search_guide,
    read_dense_vector_models,
    read_sparse_vector_models,
    search_opensearch_org,
)
from opensearch_orchestrator.scripts.opensearch_ops_tools import (
    SEARCH_UI_HOST,
    SEARCH_UI_PORT,
    create_index as create_index_impl,
    create_and_attach_pipeline as create_and_attach_pipeline_impl,
    create_bedrock_embedding_model as create_bedrock_embedding_model_impl,
    create_bedrock_agentic_model_with_creds as create_bedrock_agentic_model_with_creds_impl,
    create_local_pretrained_model as create_local_pretrained_model_impl,
    create_agentic_search_flow_agent as create_agentic_search_flow_agent_impl,
    create_agentic_search_pipeline as create_agentic_search_pipeline_impl,
    index_doc as index_doc_impl,
    index_verification_docs as index_verification_docs_impl,
    delete_doc as delete_doc_impl,
    cleanup_docs as cleanup_docs_impl,
    apply_capability_driven_verification as apply_capability_driven_verification_impl,
    preview_cap_driven_verification as preview_cap_driven_verification_impl,
    launch_search_ui as launch_search_ui_impl,
    cleanup_ui_server as cleanup_ui_server_impl,
    set_search_ui_suggestions as set_search_ui_suggestions_impl,
    RUNTIME_MODE_ENV,
    RUNTIME_MODE_MCP,
)
from opensearch_orchestrator.worker import (
    SYSTEM_PROMPT as WORKER_SYSTEM_PROMPT,
    _RESUME_WORKER_MARKER,
    build_worker_initial_input,
    commit_execution_report,
)

# Force MCP runtime mode for downstream tool behavior (for example semantic rewrite LLM disablement).
os.environ[RUNTIME_MODE_ENV] = RUNTIME_MODE_MCP

# -------------------------------------------------------------------------
# Workflow prompt (shared by MCP prompt and Cursor rule)
# -------------------------------------------------------------------------

WORKFLOW_PROMPT = """\
You are an OpenSearch Solution Architect assistant.
Use the opensearch-orchestrator MCP tools to guide the user from requirements to a running OpenSearch setup.

## Workflow Phases

### Phase 1: Collect Sample Document (mandatory first step)
- If a sample is not already loaded, first ask the user to choose one source option:
  1. Use built-in IMDB dataset
  2. Load from a local file or URL
  3. Load from a localhost OpenSearch index
  4. Paste JSON directly
- Call `load_sample(source_type, source_value, localhost_auth_mode, localhost_auth_username, localhost_auth_password)`.
  - source_type: "builtin_imdb" | "local_file" | "url" | "localhost_index" | "paste"
  - source_value: file path, URL, index name, or pasted JSON content (empty string for builtin_imdb)
  - localhost auth args are used only for `source_type="localhost_index"`:
    - localhost_auth_mode: "default" | "none" | "custom"
      - "default": use localhost auth `admin` / `myStrongPassword123!`
      - "none": force no authentication
      - "custom": use provided username/password
    - localhost_auth_username / localhost_auth_password: required only when mode is "custom"
  - For localhost index flow, ask for index name first and call `load_sample` with `localhost_auth_mode="default"` unless the user explicitly requests `none` or `custom`.
  - User-facing auth follow-ups must only offer "none" (no-auth) or "custom" (username/password). Never present "default" as a user-facing choice.
  - If the user already provided both username and password, do not ask for credentials again.
- The result includes `inferred_text_fields` and `text_search_required`.
- A sample document is required before any planning or execution.

### Phase 2: Gather Preferences
- Ask one preference question at a time, in this order:
  - **Budget**: flexible or cost-sensitive
  - **Performance priority**: speed-first, balanced, or accuracy-first
  - If `text_search_required=true`, ask **Query pattern**: mostly-exact (like "Carmencita 1894"),
    mostly-semantic (like "early silent films about dancers"), or balanced (mix of both).
- Use the client user-input UI for each question (fixed options only, not free-text).
- If `text_search_required=true` and query pattern is balanced or mostly-semantic, ask
  **Deployment preference** as a separate follow-up question:
  opensearch-node, sagemaker-endpoint, or external-embedding-api (also via user-input UI).
- If `text_search_required=false`, do not ask query-pattern or deployment-preference questions.
  Keep planning numeric/filter/aggregation-first and do not suggest changing or enriching data
  solely to force semantic search unless the user explicitly asks for semantic search.
- Call `set_preferences(budget, performance, query_pattern, deployment_preference)` with the collected values.

### Phase 3: Plan
- Call `start_planning()` to get an initial architecture proposal from the client LLM planner.
- If `start_planning()` returns `manual_planning_required=true`, follow the returned planner bootstrap payload and call `set_plan_from_planning_complete(...)` once the user confirms.
- Otherwise, present the proposal to the user verbatim (do not summarize it away).
- If the user has feedback or questions, call `refine_plan(user_feedback)`. Repeat as needed.
- When the user confirms, call `finalize_plan()`.
  This returns {solution, search_capabilities, keynote}.

### Phase 4: Execute
- Call `execute_plan()` to run index/model/pipeline/UI setup.
- If `execute_plan()` returns manual execution bootstrap payload, follow it and then commit the final worker response via `set_execution_from_execution_report(worker_response, execution_context)`.
- If execution fails, the user can fix the issue (e.g., restart Docker) and call `retry_execution()`.

### Post-Execution
- After successful execution completion, explicitly tell the user
  how to access the UI using the returned `ui_access` URLs.
- `cleanup()` removes test/verification documents when the user explicitly asks.

## Rules
- Never skip Phase 1. A sample document is mandatory before planning.
- Prefer planner tools for plan generation.
- If manual planning is required (`sampling/createMessage` unavailable), generate the plan with the
  client LLM using the provided planner prompt/input and persist it with
  `set_plan_from_planning_complete(...)` before execution.
- When a tool returns manual bootstrap payload fields, follow that payload instead of inventing alternate orchestration steps.
- Show the planner's proposal text to the user verbatim; do not summarize it away.
- For preference questions, ask one question per turn and use user-input UI fixed options, not free-text.
- Do not ask redundant clarification questions for items already inferred from the sample data.
"""

# -------------------------------------------------------------------------
# Shared workflow engine (single session per stdio connection)
# -------------------------------------------------------------------------

_engine = create_transport_agnostic_engine()

# -------------------------------------------------------------------------
# MCP server
# -------------------------------------------------------------------------

mcp = FastMCP("OpenSearch Orchestrator", json_response=True)

# -------------------------------------------------------------------------
# Phase tools
# -------------------------------------------------------------------------

PLANNER_MODE_ENV = "OPENSEARCH_MCP_PLANNER_MODE"
PLANNER_MODE_CLIENT = "client"
ADVANCED_TOOLS_ENV = "OPENSEARCH_MCP_ENABLE_ADVANCED_TOOLS"
_DEFAULT_LLM_CONVERSATION_ID = "default"
_PLANNER_LLM_CONVERSATION_ID = "__planner__"
_SEMANTIC_REWRITE_SYSTEM_PROMPT = (
    "You rewrite document snippets into one concise semantic search query.\n"
    "Rules:\n"
    "- Output only one single-line query.\n"
    "- Keep it natural and specific (about 4-12 words).\n"
    "- Do not include URLs, domain fragments, or boilerplate words.\n"
    "- Do not add explanations, labels, bullets, or quotes.\n"
    "- Prefer core topic/entities and user intent."
)
_PLANNING_COMPLETE_PATTERN = re.compile(
    r"<planning_complete>(.*?)</planning_complete>",
    re.DOTALL | re.IGNORECASE,
)
_SOLUTION_PATTERN = re.compile(r"<solution>(.*?)</solution>", re.DOTALL | re.IGNORECASE)
_CAPABILITIES_PATTERN = re.compile(
    r"<search_capabilities>(.*?)</search_capabilities>",
    re.DOTALL | re.IGNORECASE,
)
_KEYNOTE_PATTERN = re.compile(r"<keynote>(.*?)</keynote>", re.DOTALL | re.IGNORECASE)
_OPENSEARCH_AUTH_MODE_ENV = "OPENSEARCH_AUTH_MODE"
_OPENSEARCH_USER_ENV = "OPENSEARCH_USER"
_OPENSEARCH_PASSWORD_ENV = "OPENSEARCH_PASSWORD"
_LOCALHOST_AUTH_MODE_DEFAULT = "default"
_LOCALHOST_AUTH_MODE_NONE = "none"
_LOCALHOST_AUTH_MODE_CUSTOM = "custom"
_VALID_LOCALHOST_AUTH_MODES = {
    _LOCALHOST_AUTH_MODE_DEFAULT,
    _LOCALHOST_AUTH_MODE_NONE,
    _LOCALHOST_AUTH_MODE_CUSTOM,
}
_MCP_STATE_PERSIST_ENV = "OPENSEARCH_MCP_PERSIST_STATE"
_MCP_STATE_FILE_ENV = "OPENSEARCH_MCP_STATE_FILE"
_DEFAULT_MCP_STATE_FILE = (
    Path.home() / ".opensearch_orchestrator" / "mcp_state.json"
)
_MCP_STATE_VERSION = 1
_PERSISTED_STATE_FIELDS = (
    "sample_doc_json",
    "source_local_file",
    "source_index_name",
    "source_index_doc_count",
    "inferred_text_search_required",
    "inferred_semantic_text_fields",
    "budget_preference",
    "performance_priority",
    "model_deployment_preference",
    "prefix_wildcard_enabled",
    "hybrid_weight_profile",
    "pending_localhost_index_options",
    "localhost_auth_mode",
    "localhost_auth_username",
)


def _mcp_state_persistence_enabled() -> bool:
    raw = str(os.getenv(_MCP_STATE_PERSIST_ENV, "1") or "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _resolve_mcp_state_file_path() -> Path:
    configured = str(os.getenv(_MCP_STATE_FILE_ENV, "") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return _DEFAULT_MCP_STATE_FILE


@contextmanager
def _mcp_state_file_lock(path: Path):
    """Best-effort cross-process lock for persisted MCP state operations."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_fd: int | None = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        if fcntl is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        if lock_fd is None:
            return
        if fcntl is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except Exception:
                pass
        try:
            os.close(lock_fd)
        except Exception:
            pass


def _read_persisted_engine_payload() -> dict[str, object]:
    if not _mcp_state_persistence_enabled():
        return {}

    path = _resolve_mcp_state_file_path()
    if not path.exists():
        return {}

    try:
        with _mcp_state_file_lock(path):
            payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(
            f"[mcp_server.state] Failed to read persisted state '{path}': {exc}",
            file=sys.stderr,
            flush=True,
        )
        return {}

    if isinstance(payload, dict):
        return payload
    return {}


def _read_persisted_state_snapshot() -> dict[str, object]:
    payload = _read_persisted_engine_payload()
    state_payload = payload.get("state", {})
    if isinstance(state_payload, dict):
        return state_payload
    return {}


def _build_persistable_engine_payload() -> dict[str, object]:
    state_payload: dict[str, object] = {}
    state = getattr(_engine, "state", None)
    if state is not None:
        for field_name in _PERSISTED_STATE_FIELDS:
            if not hasattr(state, field_name):
                continue
            value = getattr(state, field_name, None)
            if isinstance(value, tuple):
                value = list(value)
            state_payload[field_name] = value

    phase_obj = getattr(_engine, "phase", None)
    phase_name = str(getattr(phase_obj, "name", "") or "").strip()
    plan_result = getattr(_engine, "plan_result", None)
    normalized_plan_result = (
        dict(plan_result)
        if isinstance(plan_result, dict)
        else None
    )
    return {
        "version": _MCP_STATE_VERSION,
        "phase": phase_name,
        "state": state_payload,
        "plan_result": normalized_plan_result,
    }


def _persist_engine_state(reason: str = "", *, recreate: bool = False) -> None:
    if not _mcp_state_persistence_enabled():
        return

    path = _resolve_mcp_state_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _mcp_state_file_lock(path):
            if recreate:
                try:
                    path.unlink(missing_ok=True)
                except TypeError:
                    if path.exists():
                        path.unlink()
            payload = _build_persistable_engine_payload()
            temp_path = path.with_suffix(path.suffix + ".tmp")
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(path)
    except Exception as exc:
        detail = f" ({reason})" if reason else ""
        print(
            f"[mcp_server.state] Failed to persist state{detail}: {exc}",
            file=sys.stderr,
            flush=True,
        )


def _restore_engine_state_from_file() -> None:
    if not _mcp_state_persistence_enabled():
        return

    payload = _read_persisted_engine_payload()
    if not isinstance(payload, dict):
        return

    state_payload = payload.get("state", {})
    state = getattr(_engine, "state", None)
    if isinstance(state_payload, dict) and state is not None:
        for field_name in _PERSISTED_STATE_FIELDS:
            if field_name not in state_payload:
                continue
            try:
                setattr(state, field_name, state_payload[field_name])
            except Exception:
                continue

    phase_name = str(payload.get("phase", "") or "").strip()
    if phase_name:
        try:
            _engine.phase = Phase[phase_name]
        except Exception:
            pass

    plan_result = payload.get("plan_result")
    if isinstance(plan_result, dict):
        try:
            _engine.plan_result = dict(plan_result)
        except Exception:
            pass


def _resolve_planner_mode() -> str:
    raw = str(os.getenv(PLANNER_MODE_ENV, PLANNER_MODE_CLIENT)).strip().lower()
    if raw == PLANNER_MODE_CLIENT:
        return raw
    return PLANNER_MODE_CLIENT


def _advanced_tools_enabled() -> bool:
    raw = str(os.getenv(ADVANCED_TOOLS_ENV, "")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _is_method_not_found_error(exc: Exception) -> bool:
    message = str(exc or "").lower()
    return "method not found" in message


_restore_engine_state_from_file()


def _resolve_execution_auth_override_from_state() -> tuple[str, str, str] | None:
    """Return localhost auth override from engine state for localhost-index sessions."""
    persisted_state = _read_persisted_state_snapshot()
    persisted_source_index_name = str(
        persisted_state.get("source_index_name", "") or ""
    ).strip()
    persisted_mode = str(
        persisted_state.get("localhost_auth_mode", _LOCALHOST_AUTH_MODE_DEFAULT) or ""
    ).strip().lower()
    persisted_username = str(
        persisted_state.get("localhost_auth_username", "") or ""
    ).strip()

    state = getattr(_engine, "state", None)
    if state is None:
        if not persisted_source_index_name:
            return None
        if persisted_mode not in _VALID_LOCALHOST_AUTH_MODES:
            persisted_mode = _LOCALHOST_AUTH_MODE_DEFAULT
        if persisted_mode == _LOCALHOST_AUTH_MODE_CUSTOM and persisted_username:
            # Password is intentionally not persisted; cannot override custom auth on restart.
            return None
        return persisted_mode, "", ""

    source_index_name = str(getattr(state, "source_index_name", "") or "").strip()
    if not source_index_name:
        source_index_name = persisted_source_index_name
    if not source_index_name:
        return None

    mode = str(
        getattr(state, "localhost_auth_mode", _LOCALHOST_AUTH_MODE_DEFAULT) or ""
    ).strip().lower()
    if not mode:
        mode = persisted_mode
    if mode not in _VALID_LOCALHOST_AUTH_MODES:
        mode = _LOCALHOST_AUTH_MODE_DEFAULT

    if mode == _LOCALHOST_AUTH_MODE_CUSTOM:
        username = str(getattr(state, "localhost_auth_username", "") or "").strip()
        password = str(getattr(state, "localhost_auth_password", "") or "").strip()
        if not username:
            username = persisted_username
        if not username or not password:
            return None
        return mode, username, password
    return mode, "", ""


def _resolve_sample_source_defaults(
    *,
    sample_doc_json: str = "",
    source_local_file: str = "",
    source_index_name: str = "",
) -> tuple[str, str, str]:
    """Resolve sample-source arguments, preferring explicit args then persisted state."""
    resolved_sample_doc_json = str(sample_doc_json or "").strip()
    resolved_source_local_file = str(source_local_file or "").strip()
    resolved_source_index_name = str(source_index_name or "").strip()

    persisted_state = _read_persisted_state_snapshot()
    if not resolved_sample_doc_json:
        resolved_sample_doc_json = str(
            persisted_state.get("sample_doc_json", "") or ""
        ).strip()
    if not resolved_source_local_file:
        resolved_source_local_file = str(
            persisted_state.get("source_local_file", "") or ""
        ).strip()
    if not resolved_source_index_name:
        resolved_source_index_name = str(
            persisted_state.get("source_index_name", "") or ""
        ).strip()

    state = getattr(_engine, "state", None)
    if state is None:
        return (
            resolved_sample_doc_json,
            resolved_source_local_file,
            resolved_source_index_name,
        )

    # Compatibility fallback for cases where file persistence is disabled or unavailable.
    if not resolved_sample_doc_json:
        resolved_sample_doc_json = str(
            getattr(state, "sample_doc_json", "") or ""
        ).strip()
    if not resolved_source_local_file:
        resolved_source_local_file = str(
            getattr(state, "source_local_file", "") or ""
        ).strip()
    if not resolved_source_index_name:
        resolved_source_index_name = str(
            getattr(state, "source_index_name", "") or ""
        ).strip()
    return (
        resolved_sample_doc_json,
        resolved_source_local_file,
        resolved_source_index_name,
    )


@contextmanager
def _temporary_execution_auth_env():
    override = _resolve_execution_auth_override_from_state()
    if override is None:
        yield
        return

    mode, username, password = override
    previous_mode = os.environ.get(_OPENSEARCH_AUTH_MODE_ENV)
    previous_user = os.environ.get(_OPENSEARCH_USER_ENV)
    previous_password = os.environ.get(_OPENSEARCH_PASSWORD_ENV)
    try:
        os.environ[_OPENSEARCH_AUTH_MODE_ENV] = mode
        if mode == _LOCALHOST_AUTH_MODE_CUSTOM:
            os.environ[_OPENSEARCH_USER_ENV] = username
            os.environ[_OPENSEARCH_PASSWORD_ENV] = password
        else:
            os.environ.pop(_OPENSEARCH_USER_ENV, None)
            os.environ.pop(_OPENSEARCH_PASSWORD_ENV, None)
        yield
    finally:
        if previous_mode is None:
            os.environ.pop(_OPENSEARCH_AUTH_MODE_ENV, None)
        else:
            os.environ[_OPENSEARCH_AUTH_MODE_ENV] = previous_mode
        if previous_user is None:
            os.environ.pop(_OPENSEARCH_USER_ENV, None)
        else:
            os.environ[_OPENSEARCH_USER_ENV] = previous_user
        if previous_password is None:
            os.environ.pop(_OPENSEARCH_PASSWORD_ENV, None)
        else:
            os.environ[_OPENSEARCH_PASSWORD_ENV] = previous_password


def _build_current_planning_context(additional_context: str = "") -> str:
    build_fn = getattr(_engine, "_build_planning_context", None)
    state = getattr(_engine, "state", None)
    if not callable(build_fn) or state is None:
        return str(additional_context or "")
    return str(build_fn(state, additional_context))


def _build_manual_planner_bootstrap(additional_context: str = "") -> dict[str, str]:
    """Build bootstrap prompts for manual client-LLM planning fallback.

    MCP client-mode usage flow:
    1. Call `load_sample(...)` (include localhost auth args when source_type is localhost_index),
       then `set_preferences(...)`, then `start_planning()`.
    2. If `start_planning()` returns `manual_planning_required=true`, run planner turns
       in the client LLM using:
       - system message: `manual_planner_system_prompt`
       - first user message: `manual_planner_initial_input`
       - follow-up user feedback turns until the user confirms the plan
    3. Commit the confirmed plan via
       `set_plan_from_planning_complete(planner_response)`, where
       `planner_response` includes:
       `<planning_complete><solution>...</solution><search_capabilities>...</search_capabilities><keynote>...</keynote></planning_complete>`
    4. Continue with `execute_plan()` (and `retry_execution()` if needed).
    """
    planning_context = _build_current_planning_context(additional_context)
    parser = PlanningSession(agent=lambda _prompt: "")
    return {
        "manual_planner_system_prompt": PLANNER_SYSTEM_PROMPT,
        "manual_planner_initial_input": parser._build_initial_input(planning_context),
    }


def _parse_planning_complete_response(response_text: str) -> dict[str, str] | dict[str, object]:
    text = str(response_text or "")
    match = _PLANNING_COMPLETE_PATTERN.search(text)
    if match is None:
        return {
            "error": "No <planning_complete> block found.",
            "details": [
                "Provide the planner output containing <planning_complete>...</planning_complete>.",
            ],
        }

    content = match.group(1)
    solution_match = _SOLUTION_PATTERN.search(content)
    capabilities_match = _CAPABILITIES_PATTERN.search(content)
    keynote_match = _KEYNOTE_PATTERN.search(content)
    solution = solution_match.group(1).strip() if solution_match else ""
    search_capabilities = capabilities_match.group(1).strip() if capabilities_match else ""
    keynote = keynote_match.group(1).strip() if keynote_match else ""
    if not solution:
        return {
            "error": "Invalid <planning_complete> block.",
            "details": ["<solution> is required."],
        }
    return {
        "solution": solution,
        "search_capabilities": search_capabilities,
        "keynote": keynote,
    }


def _normalize_manual_plan(
    *,
    solution: str,
    search_capabilities: str,
    keynote: str,
    additional_context: str = "",
) -> dict[str, object]:
    planning_context = _build_current_planning_context(additional_context)
    parser = PlanningSession(agent=lambda _prompt: "")
    parser._initial_context = planning_context
    parser._confirmation_received = True

    wrapped = (
        "<planning_complete>\n"
        "<solution>\n"
        f"{str(solution or '').strip()}\n"
        "</solution>\n"
        "<search_capabilities>\n"
        f"{str(search_capabilities or '').strip()}\n"
        "</search_capabilities>\n"
        "<keynote>\n"
        f"{str(keynote or '').strip()}\n"
        "</keynote>\n"
        "</planning_complete>"
    )
    match = _PLANNING_COMPLETE_PATTERN.search(wrapped)
    if match is None:
        return {
            "error": "Failed to parse manual plan.",
            "details": ["Unable to construct <planning_complete> block for validation."],
        }

    retry_feedback = parser._try_extract_result(match)
    if retry_feedback is not None:
        return {
            "error": "Manual plan failed planner validation.",
            "details": [retry_feedback],
            "hint": (
                "Regenerate the planner output using the same planner prompt/initial input "
                "and submit a corrected <planning_complete> block."
            ),
        }
    result = parser._result
    if not isinstance(result, dict):
        return {
            "error": "Manual plan failed planner validation.",
            "details": ["No normalized planner result was produced."],
        }
    return result


def _sampling_content_to_text(content: object) -> str:
    if isinstance(content, mcp_types.TextContent):
        return str(content.text or "")
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, mcp_types.TextContent):
                text_parts.append(str(item.text or ""))
        if text_parts:
            return "\n".join(part for part in text_parts if part)
    return str(content or "")


class _ClientSamplingBridge:
    """Reusable MCP client-LLM bridge keyed by conversation_id."""

    def __init__(self) -> None:
        self._messages_by_conversation: dict[str, list[mcp_types.SamplingMessage]] = {}

    def _resolve_conversation_id(self, conversation_id: str) -> str:
        normalized = str(conversation_id or "").strip()
        return normalized or _DEFAULT_LLM_CONVERSATION_ID

    def reset(self, conversation_id: str) -> str:
        resolved = self._resolve_conversation_id(conversation_id)
        self._messages_by_conversation.pop(resolved, None)
        return resolved

    async def send(
        self,
        *,
        session: Any,
        conversation_id: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        reset_conversation: bool = False,
    ) -> dict[str, Any]:
        resolved_conversation_id = self._resolve_conversation_id(conversation_id)
        if reset_conversation:
            self._messages_by_conversation.pop(resolved_conversation_id, None)

        messages = self._messages_by_conversation.setdefault(
            resolved_conversation_id,
            [],
        )
        prompt_text = str(user_prompt or "").strip()
        appended_user = False
        if prompt_text:
            messages.append(
                mcp_types.SamplingMessage(
                    role="user",
                    content=mcp_types.TextContent(type="text", text=prompt_text),
                )
            )
            appended_user = True

        try:
            result = await session.create_message(
                messages=messages,
                max_tokens=max(1, int(max_tokens)),
                system_prompt=str(system_prompt or ""),
            )
        except Exception:
            if appended_user and messages:
                messages.pop()
            raise

        assistant_text = _sampling_content_to_text(result.content)
        messages.append(
            mcp_types.SamplingMessage(
                role="assistant",
                content=mcp_types.TextContent(type="text", text=assistant_text),
            )
        )
        return {
            "conversation_id": resolved_conversation_id,
            "response": assistant_text,
            "llm_backend": "client_sampling",
        }


_client_sampling_bridge = _ClientSamplingBridge()


class _ClientSamplingPlannerAgent:
    """Planner callable that delegates generation to MCP client sampling bridge."""

    def __init__(self, ctx: Context) -> None:
        self._session = ctx.session
        self._conversation_id = _client_sampling_bridge.reset(_PLANNER_LLM_CONVERSATION_ID)

    def reset(self) -> None:
        _client_sampling_bridge.reset(self._conversation_id)

    async def __call__(self, prompt: str) -> str:
        result = await _client_sampling_bridge.send(
            session=self._session,
            conversation_id=self._conversation_id,
            system_prompt=PLANNER_SYSTEM_PROMPT,
            user_prompt=str(prompt or ""),
            max_tokens=4000,
            reset_conversation=False,
        )
        return str(result.get("response", ""))


def _build_ui_access_payload() -> dict[str, object]:
    public_host = "localhost" if SEARCH_UI_HOST in {"0.0.0.0", "::"} else SEARCH_UI_HOST
    urls: list[str] = []
    for host in (public_host, "127.0.0.1", "localhost"):
        url = f"http://{host}:{SEARCH_UI_PORT}"
        if url not in urls:
            urls.append(url)
    return {
        "primary_url": urls[0],
        "alternate_urls": urls[1:],
    }


def _build_manual_llm_payload(
    *,
    conversation_id: str,
    system_prompt: str,
    user_prompt: str,
    details: list[str] | None = None,
    error: str = "Client sampling is unavailable.",
) -> dict[str, object]:
    return {
        "error": error,
        "conversation_id": str(conversation_id or _DEFAULT_LLM_CONVERSATION_ID),
        "manual_llm_required": True,
        "manual_system_prompt": str(system_prompt or ""),
        "manual_user_prompt": str(user_prompt or ""),
        "details": list(details or []),
    }


def _build_worker_bootstrap_payload(execution_context: str) -> dict[str, object]:
    """Build bootstrap prompts for manual client-LLM execution fallback.

    MCP client-mode usage flow:
    1. Call `load_sample(...)` (include localhost auth args when source_type is localhost_index),
       then `set_preferences(...)`, then `start_planning()`.
    2. If `start_planning()` returns `manual_planning_required=true`, run planner turns
       in the client LLM using:
       - system message: `manual_planner_system_prompt`
       - first user message: `manual_planner_initial_input`
       - follow-up user feedback turns until the user confirms the plan
    3. Commit the confirmed plan via
       `set_plan_from_planning_complete(planner_response)`, where
       `planner_response` includes:
       `<planning_complete><solution>...</solution><search_capabilities>...</search_capabilities><keynote>...</keynote></planning_complete>`
    4. Continue with `execute_plan()` (and `retry_execution()` if needed).
    """
    worker_context = str(execution_context or "").strip()
    return {
        "manual_execution_required": True,
        "execution_backend": "client_manual",
        "worker_system_prompt": WORKER_SYSTEM_PROMPT,
        "worker_initial_input": build_worker_initial_input(worker_context),
        "execution_context": worker_context,
        "ui_access": _build_ui_access_payload(),
    }


def _extract_retry_context_details(retry_context: str) -> tuple[str, bool]:
    text = str(retry_context or "").strip()
    if not text:
        return "", False
    if text.startswith(_RESUME_WORKER_MARKER):
        return text.split("\n", 1)[1].strip() if "\n" in text else "", True
    return text, False


def _build_retry_worker_bootstrap_payload(
    retry_context: str,
    *,
    failed_step: str = "",
    previous_steps: dict[str, str] | None = None,
) -> dict[str, object]:
    execution_context, is_resume = _extract_retry_context_details(retry_context)
    return {
        "manual_execution_required": True,
        "execution_backend": "client_manual",
        "is_retry": True,
        "worker_system_prompt": WORKER_SYSTEM_PROMPT,
        "worker_initial_input": build_worker_initial_input(
            execution_context,
            resume_mode=is_resume,
            resume_step=str(failed_step or ""),
            previous_steps=previous_steps or {},
        ),
        "execution_context": retry_context,
        "ui_access": _build_ui_access_payload(),
    }


async def _rewrite_semantic_suggestion_entries_with_client_llm(
    *,
    result: dict[str, object],
    ctx: Context | None,
) -> dict[str, object]:
    if not isinstance(result, dict):
        return result
    suggestion_meta = result.get("suggestion_meta", [])
    if not isinstance(suggestion_meta, list) or not suggestion_meta:
        return result
    if ctx is None:
        return result

    rewritten_entries: list[dict[str, object]] = []
    for entry in suggestion_meta:
        if not isinstance(entry, dict):
            rewritten_entries.append(entry)
            continue
        capability = str(entry.get("capability", "")).strip().lower()
        text = str(entry.get("text", "")).strip()
        if capability != "semantic" or not text:
            rewritten_entries.append(dict(entry))
            continue

        try:
            llm_result = await _client_sampling_bridge.send(
                session=ctx.session,
                conversation_id="semantic_rewrite",
                system_prompt=_SEMANTIC_REWRITE_SYSTEM_PROMPT,
                user_prompt=f"Rewrite this snippet into one semantic search query only:\n{text[:1800]}",
                max_tokens=120,
                reset_conversation=True,
            )
        except Exception as exc:
            if _is_method_not_found_error(exc):
                rewritten_entries.append(dict(entry))
                continue
            rewritten_entries.append(dict(entry))
            continue

        rewritten = str(llm_result.get("response", "")).strip()
        if not rewritten:
            rewritten_entries.append(dict(entry))
            continue
        rewritten = rewritten.splitlines()[0].strip()
        rewritten = re.sub(r"^[-*]\s+", "", rewritten)
        rewritten = re.sub(
            r"^(?:semantic\s+query|query)\s*:\s*",
            "",
            rewritten,
            flags=re.IGNORECASE,
        )
        rewritten = rewritten.strip().strip("`").strip("'").strip('"').strip()
        if not rewritten:
            rewritten_entries.append(dict(entry))
            continue
        item = dict(entry)
        item["text"] = rewritten[:120]
        rewritten_entries.append(item)

    normalized = dict(result)
    normalized["suggestion_meta"] = rewritten_entries
    return normalized


@mcp.tool()
def load_sample(
    source_type: str,
    source_value: str = "",
    localhost_auth_mode: str = "default",
    localhost_auth_username: str = "",
    localhost_auth_password: str = "",
) -> dict:
    """Load a sample document for OpenSearch solution design.
    This MUST be called first before any planning or execution.

    Args:
        source_type: One of "builtin_imdb", "local_file", "url",
                     "localhost_index", or "paste".
        source_value: File path, URL, index name, or pasted JSON content.
                      Use empty string for builtin_imdb.
        localhost_auth_mode: "default", "none", or "custom" (localhost_index only).
            - default: use localhost default credentials admin/myStrongPassword123!
            - none: force no authentication
            - custom: use localhost_auth_username/localhost_auth_password
        localhost_auth_username: Username for localhost custom auth mode.
        localhost_auth_password: Password for localhost custom auth mode.

    Returns:
        dict with sample_doc, inferred_text_fields, text_search_required,
        and status message.
    """
    result = _engine.load_sample(
        source_type=source_type,
        source_value=source_value,
        localhost_auth_mode=localhost_auth_mode,
        localhost_auth_username=localhost_auth_username,
        localhost_auth_password=localhost_auth_password,
    )
    # Entering step 1 starts a fresh persisted conversation snapshot.
    _persist_engine_state("load_sample", recreate=True)
    return result


@mcp.tool()
def set_preferences(
    budget: str = "flexible",
    performance: str = "balanced",
    query_pattern: str = "balanced",
    deployment_preference: str = "",
) -> dict:
    """Set user preferences for budget, performance, query pattern, and deployment.
    Call this after load_sample and before start_planning.

    Args:
        budget: "flexible" or "cost-sensitive".
        performance: "speed-first", "balanced", or "accuracy-first".
        query_pattern: "mostly-exact", "balanced", or "mostly-semantic".
        deployment_preference: "opensearch-node", "sagemaker-endpoint", or
            "external-embedding-api". Used when query_pattern is
            "balanced" or "mostly-semantic". Defaults to "opensearch-node".

    Returns:
        dict confirming stored preferences and generated context notes.
    """
    result = _engine.set_preferences(
        budget=budget,
        performance=performance,
        query_pattern=query_pattern,
        deployment_preference=deployment_preference,
    )
    _persist_engine_state("set_preferences")
    return result


@mcp.tool()
async def talk_to_client_llm(
    system_prompt: str,
    user_prompt: str,
    conversation_id: str = _DEFAULT_LLM_CONVERSATION_ID,
    reset_conversation: bool = False,
    max_tokens: int = 4000,
    ctx: Context | None = None,
) -> dict:
    """General-purpose client-LLM bridge over MCP sampling.

    Returns `{"conversation_id","response","llm_backend":"client_sampling"}` on success.
    Returns manual fallback payload when client sampling is unavailable.
    """
    resolved_conversation_id = str(conversation_id or _DEFAULT_LLM_CONVERSATION_ID).strip() or _DEFAULT_LLM_CONVERSATION_ID
    if ctx is None:
        return _build_manual_llm_payload(
            conversation_id=resolved_conversation_id,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            details=["MCP context is unavailable for client sampling."],
            error="Client LLM call failed.",
        )

    try:
        return await _client_sampling_bridge.send(
            session=ctx.session,
            conversation_id=resolved_conversation_id,
            system_prompt=str(system_prompt or ""),
            user_prompt=str(user_prompt or ""),
            max_tokens=max_tokens,
            reset_conversation=bool(reset_conversation),
        )
    except Exception as exc:
        if _is_method_not_found_error(exc):
            return _build_manual_llm_payload(
                conversation_id=resolved_conversation_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                details=[f"client-sampling LLM call failed: {exc}"],
                error="Client LLM call failed.",
            )
        return {
            "error": "Client LLM call failed.",
            "conversation_id": resolved_conversation_id,
            "details": [f"client-sampling LLM call failed: {exc}"],
        }


@mcp.tool()
async def start_planning(additional_context: str = "", ctx: Context | None = None) -> dict:
    """Start the solution planning phase. Returns the planner's initial proposal.
    Call this after set_preferences.

    Args:
        additional_context: Optional extra context to include.

    Returns:
        dict with response text, is_complete flag, and result (if complete).
    """
    if ctx is None:
        return {
            "error": "Planning failed in client mode.",
            "details": ["MCP context is unavailable for client sampling."],
            "hint": "Call start_planning via an MCP client session.",
        }
    try:
        result = await _engine.start_planning(
            additional_context=additional_context,
            planning_agent=_ClientSamplingPlannerAgent(ctx),
        )
        result["planner_backend"] = "client_sampling"
        _persist_engine_state("start_planning")
        return result
    except Exception as exc:
        if _is_method_not_found_error(exc):
            bootstrap = _build_manual_planner_bootstrap(additional_context)
            return {
                "error": "Planning failed in client mode.",
                "details": [f"client-sampling planner failed: {exc}"],
                "planner_backend": "client_manual",
                "manual_planning_required": True,
                "hint": (
                    "The MCP client does not support `sampling/createMessage`. "
                    "Use the returned manual planner prompt/input to generate planner turns "
                    "with the client LLM, then call `set_plan_from_planning_complete(...)` "
                    "after user confirmation."
                ),
                **bootstrap,
            }
        return {
            "error": "Planning failed in client mode.",
            "details": [f"client-sampling planner failed: {exc}"],
        }


@mcp.tool()
async def refine_plan(user_feedback: str) -> dict:
    """Send user feedback to the planner and get a refined proposal.
    Call after start_planning. Repeat as needed.

    Args:
        user_feedback: User's feedback, questions, or change requests.

    Returns:
        dict with response text, is_complete flag, and result (if complete).
    """
    result = await _engine.refine_plan(user_feedback)
    _persist_engine_state("refine_plan")
    return result


@mcp.tool()
async def finalize_plan() -> dict:
    """Force the planner to finalize and return the structured plan.
    Call when the user confirms they are satisfied with the proposal.

    Returns:
        dict with solution, search_capabilities, and keynote.
    """
    result = await _engine.finalize_plan()
    _persist_engine_state("finalize_plan")
    return result


def set_plan(solution: str, search_capabilities: str = "", keynote: str = "") -> dict:
    """Store a client-authored finalized plan for execution after planner validation.
    Call this when the MCP client cannot run `start_planning` via client sampling
    and the client LLM authored the proposal directly.

    Args:
        solution: Finalized architecture plan text.
        search_capabilities: Search capability section text.
        keynote: Key assumptions and caveats.

    Returns:
        dict with status and stored normalized plan.
    """
    normalized = _normalize_manual_plan(
        solution=solution,
        search_capabilities=search_capabilities,
        keynote=keynote,
    )
    if "error" in normalized:
        return normalized
    result = _engine.set_plan(
        solution=str(normalized.get("solution", "")),
        search_capabilities=str(normalized.get("search_capabilities", "")),
        keynote=str(normalized.get("keynote", "")),
    )
    _persist_engine_state("set_plan")
    return result


@mcp.tool()
def set_plan_from_planning_complete(planner_response: str, additional_context: str = "") -> dict:
    """Parse and store planner output from a `<planning_complete>` block.
    Preferred manual-mode commit path when `manual_planning_required=true`.

    Args:
        planner_response: Full planner response text containing `<planning_complete>`.
        additional_context: Optional context to include for normalization/validation.

    Returns:
        dict with status and stored normalized plan, or validation feedback.
    """
    parsed = _parse_planning_complete_response(planner_response)
    if "error" in parsed:
        return parsed
    normalized = _normalize_manual_plan(
        solution=str(parsed.get("solution", "")),
        search_capabilities=str(parsed.get("search_capabilities", "")),
        keynote=str(parsed.get("keynote", "")),
        additional_context=additional_context,
    )
    if "error" in normalized:
        return normalized
    result = _engine.set_plan(
        solution=str(normalized.get("solution", "")),
        search_capabilities=str(normalized.get("search_capabilities", "")),
        keynote=str(normalized.get("keynote", "")),
    )
    _persist_engine_state("set_plan_from_planning_complete")
    return result


@mcp.tool()
async def execute_plan(additional_context: str = "") -> dict:
    """Build manual execution bootstrap for the finalized plan.
    Call after finalize_plan, set_plan, or set_plan_from_planning_complete.

    Args:
        additional_context: Optional extra instructions for the worker.

    Returns:
        dict with manual execution payload for client LLM worker turns.
    """
    payload = _engine.build_execution_context(
        additional_context=additional_context,
    )
    if "error" in payload:
        return payload
    execution_context = str(payload.get("execution_context", "")).strip()
    if not execution_context:
        return {"error": "Failed to build execution context for manual execution."}
    return _build_worker_bootstrap_payload(execution_context)


@mcp.tool()
async def retry_execution() -> dict:
    """Build manual retry bootstrap from the last failed step.
    Call after execution fails and the user has fixed the issue.

    Returns:
        dict with manual retry payload for client LLM worker turns.
    """
    payload = _engine.build_retry_execution_context()
    if "error" in payload:
        return payload
    retry_context = str(payload.get("execution_context", "")).strip()
    if not retry_context:
        return {"error": "No checkpoint context available. Run execute_plan first."}
    return _build_retry_worker_bootstrap_payload(
        retry_context,
        failed_step=str(payload.get("failed_step", "")),
        previous_steps=(
            dict(payload.get("previous_steps", {}))
            if isinstance(payload.get("previous_steps", {}), dict)
            else {}
        ),
    )


@mcp.tool()
def set_execution_from_execution_report(
    worker_response: str,
    execution_context: str = "",
) -> dict:
    """Commit a client-authored worker response containing `<execution_report>`.

    Args:
        worker_response: Full worker response text with `<execution_report>` block.
        execution_context: Context returned by execute_plan()/retry_execution().

    Returns:
        dict with normalized execution_report, ui_access, and status.
    """
    committed = commit_execution_report(
        worker_response,
        execution_context=execution_context,
    )
    if "error" in committed:
        return committed

    report = committed.get("execution_report", {})
    status = str(report.get("status", "")).strip().lower() if isinstance(report, dict) else ""
    _engine.phase = Phase.DONE if status == "success" else Phase.EXEC_FAILED
    _persist_engine_state("set_execution_from_execution_report")
    return {
        "status": str(committed.get("status", "Execution report stored.")),
        "execution_report": report,
        "execution_context": str(committed.get("execution_context", "")),
        "ui_access": _build_ui_access_payload(),
    }


@mcp.tool()
def create_index(
    index_name: str,
    body: dict | None = None,
    replace_if_exists: bool = True,
    sample_doc_json: str = "",
    source_local_file: str = "",
    source_index_name: str = "",
) -> str:
    """Create an OpenSearch index for MCP manual execution mode."""
    (
        resolved_sample_doc_json,
        resolved_source_local_file,
        resolved_source_index_name,
    ) = _resolve_sample_source_defaults(
        sample_doc_json=sample_doc_json,
        source_local_file=source_local_file,
        source_index_name=source_index_name,
    )
    with _temporary_execution_auth_env():
        return create_index_impl(
            index_name=index_name,
            body=body,
            replace_if_exists=replace_if_exists,
            sample_doc_json=resolved_sample_doc_json,
            source_local_file=resolved_source_local_file,
            source_index_name=resolved_source_index_name,
        )


@mcp.tool()
def create_and_attach_pipeline(
    pipeline_name: str,
    pipeline_body: dict | None = None,
    index_name: str = "",
    pipeline_type: str = "ingest",
    replace_if_exists: bool = True,
    is_hybrid_search: bool = False,
    hybrid_weights: list[float] | None = None,
    body: dict | None = None,
) -> str:
    """Create and attach ingest/search pipelines for MCP manual execution mode."""
    resolved_pipeline_body = pipeline_body if pipeline_body is not None else body
    if resolved_pipeline_body is None:
        resolved_pipeline_body = {}
    if not isinstance(resolved_pipeline_body, dict):
        return "Error: pipeline_body must be a JSON object."

    resolved_index_name = str(index_name or "").strip()
    if not resolved_index_name:
        return "Error: index_name is required."

    with _temporary_execution_auth_env():
        return create_and_attach_pipeline_impl(
            pipeline_name=pipeline_name,
            pipeline_body=resolved_pipeline_body,
            index_name=resolved_index_name,
            pipeline_type=pipeline_type,
            replace_if_exists=replace_if_exists,
            is_hybrid_search=is_hybrid_search,
            hybrid_weights=hybrid_weights,
        )


@mcp.tool()
def create_bedrock_embedding_model(model_name: str) -> str:
    """Create a Bedrock embedding model."""
    with _temporary_execution_auth_env():
        return create_bedrock_embedding_model_impl(model_name=model_name)


@mcp.tool()
def create_local_pretrained_model(model_name: str) -> str:
    """Create a local OpenSearch-hosted pretrained model."""
    with _temporary_execution_auth_env():
        return create_local_pretrained_model_impl(model_name=model_name)


@mcp.tool()
def create_bedrock_agentic_model_with_creds(
    access_key: str,
    secret_key: str,
    region: str,
    session_token: str,
    model_name: str,
) -> str:
    """Create a Bedrock agentic model with explicit AWS credentials.
    
    Args:
        access_key: AWS access key ID
        secret_key: AWS secret access key
        region: AWS region (e.g., us-east-1)
        session_token: AWS session token
        model_name: Name for the model in OpenSearch
    
    Returns:
        str: Success or error message
    """
    with _temporary_execution_auth_env():
        return create_bedrock_agentic_model_with_creds_impl(
            access_key=access_key,
            secret_key=secret_key,
            region=region,
            session_token=session_token,
            model_name=model_name,
        )


@mcp.tool()
def create_agentic_search_flow_agent(agent_name: str, model_id: str) -> str:
    """Create an agentic search flow agent with IndexMappingTool and QueryPlanningTool.
    
    Args:
        agent_name: Name for the agent
        model_id: OpenSearch model ID (from create_bedrock_agentic_model_with_creds)
    
    Returns:
        str: Agent ID or error message
    """
    with _temporary_execution_auth_env():
        return create_agentic_search_flow_agent_impl(
            agent_name=agent_name,
            model_id=model_id,
        )


@mcp.tool()
def create_agentic_search_pipeline(
    pipeline_name: str,
    agent_id: str,
    index_name: str,
    replace_if_exists: bool = True,
) -> str:
    """Create an agentic search pipeline and attach it to an index.
    
    Args:
        pipeline_name: Name for the search pipeline
        agent_id: Agent ID (from create_agentic_search_flow_agent)
        index_name: Index to attach the pipeline to
        replace_if_exists: Whether to replace existing pipeline
    
    Returns:
        str: Success or error message
    """
    with _temporary_execution_auth_env():
        return create_agentic_search_pipeline_impl(
            pipeline_name=pipeline_name,
            agent_id=agent_id,
            index_name=index_name,
            replace_if_exists=replace_if_exists,
        )


@mcp.tool()
async def apply_capability_driven_verification(
    worker_output: str,
    index_name: str = "",
    count: int = 10,
    id_prefix: str = "verification",
    sample_doc_json: str = "",
    source_local_file: str = "",
    source_index_name: str = "",
    existing_verification_doc_ids: str = "",
    ctx: Context | None = None,
) -> dict[str, object]:
    """Apply capability-driven verification and MCP semantic-query rewrite via client LLM."""
    (
        resolved_sample_doc_json,
        resolved_source_local_file,
        resolved_source_index_name,
    ) = _resolve_sample_source_defaults(
        sample_doc_json=sample_doc_json,
        source_local_file=source_local_file,
        source_index_name=source_index_name,
    )
    with _temporary_execution_auth_env():
        result = apply_capability_driven_verification_impl(
            worker_output=worker_output,
            index_name=index_name,
            count=count,
            id_prefix=id_prefix,
            sample_doc_json=resolved_sample_doc_json,
            source_local_file=resolved_source_local_file,
            source_index_name=resolved_source_index_name,
            existing_verification_doc_ids=existing_verification_doc_ids,
        )
    # write semantic query
    return await _rewrite_semantic_suggestion_entries_with_client_llm(result=result, ctx=ctx)


@mcp.tool()
def launch_search_ui(index_name: str = "") -> str:
    """Launch Search Builder UI."""
    with _temporary_execution_auth_env():
        return launch_search_ui_impl(index_name=index_name)


@mcp.tool()
def set_search_ui_suggestions(index_name: str, suggestion_meta_json: str) -> str:
    """Store search suggestion metadata for UI bootstrap."""
    return set_search_ui_suggestions_impl(
        index_name=index_name,
        suggestion_meta_json=suggestion_meta_json,
    )


@mcp.tool()
def prepare_aws_deployment() -> dict:
    """Prepare structured context for deploying the local search strategy to AWS OpenSearch.
    Call after successful Phase 4 execution.

    Returns deployment target (serverless or domain), search strategy, local configuration,
    list of steering files to follow in order, required MCP servers, and a state file
    template for tracking deployment progress.
    """
    result = _engine.prepare_aws_deployment()
    if "error" not in result:
        _persist_engine_state("prepare_aws_deployment")
    return result


@mcp.tool()
def cleanup() -> str:
    """Remove verification/test documents from the OpenSearch index.
    Call only when the user explicitly asks for cleanup.

    Returns:
        str: Cleanup result message.
    """
    with _temporary_execution_auth_env():
        return cleanup_docs_impl()


# Expose minimal knowledge tools by default for MCP manual planning/execution flows.
mcp.tool()(read_knowledge_base)
mcp.tool()(read_agentic_search_guide)
mcp.tool()(read_dense_vector_models)
mcp.tool()(read_sparse_vector_models)
mcp.tool()(search_opensearch_org)


# -------------------------------------------------------------------------
# MCP prompt (for Claude Desktop and generic MCP clients)
# -------------------------------------------------------------------------

@mcp.prompt()
def opensearch_workflow() -> str:
    """OpenSearch Solution Architect workflow guide.

    Select this prompt to learn how to use the opensearch-orchestrator
    tools for designing and deploying an OpenSearch search solution.
    """
    return WORKFLOW_PROMPT


# -------------------------------------------------------------------------
# Low-level domain tools (kept for advanced / direct-access clients)
# -------------------------------------------------------------------------

if _advanced_tools_enabled():
    # Legacy manual planning commit path kept for advanced/direct-access clients.
    mcp.tool()(set_plan)

    # Raw ingestion/index helpers are advanced-only.
    mcp.tool()(submit_sample_doc)
    mcp.tool()(submit_sample_doc_from_local_file)
    mcp.tool()(submit_sample_doc_from_url)
    mcp.tool()(get_sample_docs_for_verification)
    mcp.tool()(index_doc_impl)
    mcp.tool()(index_verification_docs_impl)
    mcp.tool()(delete_doc_impl)
    mcp.tool()(preview_cap_driven_verification_impl)
    mcp.tool()(cleanup_ui_server_impl)


def _flatten_exception_leaves(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        leaves: list[BaseException] = []
        for nested in exc.exceptions:
            leaves.extend(_flatten_exception_leaves(nested))
        return leaves
    return [exc]


def _is_expected_stdio_disconnect(exc: BaseException) -> bool:
    leaves = _flatten_exception_leaves(exc)
    if not leaves:
        return False

    expected_types = (
        anyio.BrokenResourceError,
        anyio.ClosedResourceError,
        BrokenPipeError,
        EOFError,
    )

    for leaf in leaves:
        if isinstance(leaf, expected_types):
            continue
        if isinstance(leaf, OSError) and leaf.errno in {errno.EPIPE, errno.EBADF}:
            continue
        return False
    return True


def main() -> None:
    """Entry point for the MCP server (used by both `uv run` and PyPI console_scripts)."""
    if sys.stdin.isatty():
        print(
            "This MCP server uses JSON-RPC over stdio and must be launched by an MCP client "
            "(Cursor/Claude Desktop/Inspector)."
        )
        print("For an interactive local workflow, run: python opensearch_orchestrator/orchestrator.py")
        raise SystemExit(0)
    # IDE MCP integrations commonly run stdio servers as child processes (for this repo:
    # clients like Cursor launches `uv run opensearch_orchestrator/mcp_server.py` from `.cursor/mcp.json`).
    # Reconnect-like events (window reload/restart, MCP toggle, cancel/disconnect/re-init)
    # close and reopen the stdio pipe. When that pipe closes, this process should exit
    # cleanly; the client starts a new process for the new connection.
    # In practice: reconnect == restart (new PID, fresh in-memory session state).
    try:
        mcp.run(transport="stdio")
    except BaseException as exc:
        if _is_expected_stdio_disconnect(exc):
            raise SystemExit(0)
        raise


if __name__ == "__main__":
    main()
