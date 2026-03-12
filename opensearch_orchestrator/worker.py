import json
import re
import sys
from pathlib import Path

from strands import Agent, tool
from strands.models import BedrockModel
if __package__ in {None, ""}:
    from pathlib import Path
    import sys

    _SCRIPT_EXECUTION_PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
    if _SCRIPT_EXECUTION_PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _SCRIPT_EXECUTION_PROJECT_ROOT)

from opensearch_orchestrator.handler import ThinkingCallbackHandler
from opensearch_orchestrator.opensearch_ops_tools import (
    SEARCH_UI_HOST,
    SEARCH_UI_PORT,
    apply_capability_driven_verification as apply_capability_driven_verification_impl,
    create_index as create_index_impl,
    create_and_attach_pipeline,
    create_bedrock_embedding_model,
    create_local_pretrained_model,
    create_bedrock_agentic_model_with_creds,
    create_agentic_search_agent,
    create_agentic_search_flow_agent,
    create_agentic_search_pipeline,
    delete_doc,
    launch_search_ui,
    recover_local_opensearch_container,
    set_search_ui_suggestions,
)
from opensearch_orchestrator.tools import BUILTIN_IMDB_SAMPLE_PATH, search_opensearch_org
from opensearch_orchestrator.shared import (
    SUPPORTED_SAMPLE_FILE_EXTENSION_REGEX,
    mark_execution_completed,
    set_last_worker_context,
    set_last_worker_run_state,
    get_last_worker_run_state,
)

# -------------------------------------------------------------------------
# System Prompt
# -------------------------------------------------------------------------

SYSTEM_PROMPT = """
# OpenSearch Implementation Engineer

You are an expert OpenSearch implementation engineer.
Your goal is to execute the technical plan provided in the context.

## Your Responsibilities
1.  **Analyze the Plan**: specific index settings, mappings, and configurations.
2.  **Execute (in specific order)**:
    *   **First**: Create necessary models (e.g., Bedrock embedding models, Local pretrained models, Bedrock agentic models).
    *   **Second**: Create the index with the correct settings and mappings.
    *   **Third**: Create the ingest pipeline and attach it to the index (this often requires models to be ready).
    *   **Third-A (Hybrid lexical+semantic only)**: Create and attach a search pipeline with normalization + combination weights for hybrid query score blending.
    *   **Third-B (Agentic Search only)**: 
        - Register Bedrock Claude 4 Sonnet model using `create_bedrock_agentic_model` with AWS credentials from context
        - Create agentic search agent using `create_agentic_search_flow_agent` (flow type with IndexMappingTool + QueryPlanningTool)
        - Create and attach agentic search pipeline using `create_agentic_search_pipeline`
    *   **Fourth (Capability + Verification)**: Call `apply_capability_driven_verification` using the full approved plan text and an explicit `index_name` (target index for this run). This step both parses `Search Capabilities` and indexes capability-selected verification docs. The result dict contains `suggestion_meta` — pass it to `set_search_ui_suggestions` (as JSON) with the same `index_name` before launching the UI.
    *   **Fifth (UX Handoff)**: Launch the custom React Search Builder UI using `launch_search_ui` so users can run queries immediately.
    *   **Sixth (Cleanup policy)**: Do NOT delete verification docs automatically. Keep them for user testing and clearly mention cleanup happens only when user explicitly asks.
3.  **Report**: Confirm successful execution of all steps.

## Important Rules
1. When using sparse vector search with SEISMIC or ANN, you should use `sparse_vector` field instead of `rank_features` field.
2. Always verify your work by indexing a sample document.
3. Use `apply_capability_driven_verification` for verification ingestion in this workflow.
4. Pipeline `field_map` source fields must exist in index mapping/sample schema. Do not assume a `text` field exists.
5. Schema-only exception: if no suitable text-like source field exists for embeddings, skip embedding setup only for this schema mismatch.
6. If model registration/deployment fails (especially with memory pressure), stop execution, mark `model_setup` as failed, and tell the user to reconnect Docker and retry. Do NOT continue with lexical-only fallback for this failure mode.
7. `apply_capability_driven_verification` must run before `launch_search_ui` or `delete_doc`.
8. If any step fails, mark that step as failed in the execution report and do not claim overall success.
9. Lock-order hard stop: if one step fails, do not execute any later step.
10. Producer-driven boolean typing policy is strict:
    - Use `boolean` mapping only when sample/producer values are native booleans.
    - If producer values are string flags such as '0'/'1', map as `keyword` (do not coerce to boolean).
11. For `knn_vector` method definitions, always set `method.engine` explicitly.
    - Never use `nmslib` (deprecated).
    - For `hnsw`, prefer `lucene` unless the approved plan explicitly requires `faiss`.
    - For `ivf`, use `faiss`.
12. For Agentic Search:
    - AWS credentials (access_key, secret_key, session_token, region) are provided in the context
    - Use `create_bedrock_agentic_model` to register and deploy the Bedrock model
    - Use `create_agentic_search_flow_agent` to create the flow agent (NOT conversational agent in Phase 1)
    - Use `create_agentic_search_pipeline` to create and attach the search pipeline
    - Model ID: us.anthropic.claude-sonnet-4-20250514-v1:0
    - Agent type: flow (with IndexMappingTool + QueryPlanningTool)

## Execution Report Contract (Required)
At the end of your response, you MUST include an exact machine-readable block:
<execution_report>
{"status":"success|failed","steps":{"model_setup":"success|failed|skipped","index_setup":"success|failed|skipped","pipeline_setup":"success|failed|skipped","capability_precheck":"success|failed|skipped","ui_launch":"success|failed|skipped"},"failed_step":"<step-id-or-empty>","notes":["..."]}
</execution_report>

Canonical step IDs:
- model_setup
- index_setup
- pipeline_setup
- capability_precheck
- ui_launch
"""

# -------------------------------------------------------------------------
# Worker Execution
# -------------------------------------------------------------------------

_CANONICAL_CAPABILITY_PREFIX = re.compile(
    r"^[-*]\s*(Exact|Semantic|Structured|Combined|Autocomplete|Fuzzy)\s*:",
    re.IGNORECASE,
)
_EXECUTION_REPORT_PATTERN = re.compile(
    r"<execution_report>(.*?)</execution_report>",
    re.IGNORECASE | re.DOTALL,
)
_RESUME_WORKER_MARKER = "[RESUME_WORKER_FROM_FAILED_STEP]"
_CANONICAL_STEP_ORDER = (
    "model_setup",
    "index_setup",
    "pipeline_setup",
    "capability_precheck",
    "ui_launch",
)
_ALLOWED_STEP_STATUS = {"success", "failed", "skipped"}
_HYBRID_WEIGHT_PROFILE_PATTERN = re.compile(
    r"hybrid\s+weight\s+profile\s*:\s*(semantic-heavy|balanced|lexical-heavy)\b",
    re.IGNORECASE,
)
_LOCALHOST_SOURCE_POLICY_PATTERN = re.compile(
    r"execution\s+policy\s*:\s*source\s+is\s+localhost\s+opensearch\s+index(?:\s+'([^']+)')?",
    re.IGNORECASE,
)
_LOCALHOST_SAMPLE_STATUS_PATTERN = re.compile(
    r"sample\s+document\s+loaded\s+from\s+localhost\s+opensearch\s+index\s+'([^']+)'",
    re.IGNORECASE,
)
_LOCALHOST_SOURCE_LINE_PATTERN = re.compile(
    r"(?:^|\n)[^\n]*?(?:\*\*)?\b(?:source|data\s+source)\b(?:\*\*)?\s*:\s*localhost\s+opensearch\s+index"
    r"(?:\s+(?:'([^']+)'|\"([^\"]+)\"|`([^`]+)`|([A-Za-z0-9._-]+)))?",
    re.IGNORECASE,
)
_LOCALHOST_SOURCE_INDEX_JSON_PATTERN = re.compile(
    r'"source_index_name"\s*:\s*"([^"]+)"',
    re.IGNORECASE,
)
_SOURCE_LOCAL_FILE_JSON_PATTERN = re.compile(
    r'"source_local_file"\s*:\s*"([^"]+)"',
    re.IGNORECASE,
)
_SOURCE_LOCAL_FILE_STATUS_PATTERN = re.compile(
    r"sample\s+document\s+loaded\s+from\s+'([^']+)'",
    re.IGNORECASE,
)
_SOURCE_LINE_PATTERN = re.compile(
    r"(?:^|\n)[^\n]*?(?:\*\*)?\bsource\b(?:\*\*)?\s*:\s*([^\n]+)",
    re.IGNORECASE,
)
_SOURCE_LOCAL_FILE_TOKEN_PATTERN = re.compile(
    rf"[^\s,;]+(?:{SUPPORTED_SAMPLE_FILE_EXTENSION_REGEX})\b",
    re.IGNORECASE,
)
_SAMPLE_DOC_LINE_PATTERN = re.compile(
    r"(?:^|\n)\s*(?:[-*]\s*)?(?:\*\*)?\s*sample\s+doc(?:ument)?(?:\s+(?:structure|schema))?\s*(?:\*\*)?\s*:\s*(\{.*\})\s*(?=\n|$)",
    re.IGNORECASE,
)
_MODEL_FAILURE_TOKENS = (
    "model deployment failed",
    "model deployment has failed",
    "model registration failed",
    "model registration has failed",
    "model setup failed",
)
_MODEL_MEMORY_FAILURE_TOKENS = (
    "memory constraints",
    "memory constraint",
    "native memory",
    "out of memory",
    "outofmemory",
    "circuit_breaking_exception",
    "ml_commons.native_memory_threshold",
)
_DOCKER_RECONNECT_GUIDANCE = (
    "Execution halted: model setup failed due to OpenSearch memory pressure. "
    "Please reconnect Docker (restart Docker Desktop/service) and retry this run. "
    "Lexical-only fallback is disabled for this failure mode."
)
_LOCAL_UI_HOST_ALIASES = ("localhost", "127.0.0.1")


def _unique_preserve_order(items: list[str]) -> list[str]:
    unique: list[str] = []
    for item in items:
        if item and item not in unique:
            unique.append(item)
    return unique


def _build_ui_access_urls() -> list[str]:
    host = "localhost" if SEARCH_UI_HOST in {"0.0.0.0", "::"} else SEARCH_UI_HOST
    candidates = [
        f"http://{host}:{SEARCH_UI_PORT}",
        *[f"http://{alias}:{SEARCH_UI_PORT}" for alias in _LOCAL_UI_HOST_ALIASES],
    ]
    return _unique_preserve_order(candidates)


def _should_append_ui_access_hint(report: dict) -> bool:
    if not isinstance(report, dict):
        return False
    if str(report.get("status", "")).strip().lower() != "success":
        return False
    steps = report.get("steps", {})
    if not isinstance(steps, dict):
        return False
    return str(steps.get("ui_launch", "")).strip().lower() == "success"


def _append_ui_access_hint(response_text: str, report: dict) -> str:
    text = str(response_text or "").rstrip()
    if not _should_append_ui_access_hint(report):
        return text

    urls = _build_ui_access_urls()
    if any(url in text for url in urls):
        return text

    if not urls:
        return text

    ui_hint_lines = [f"UI access: open {urls[0]} in your browser."]
    if len(urls) > 1:
        fallback_urls = " or ".join(urls[1:])
        ui_hint_lines.append(f"If needed, try {fallback_urls}.")
    return text + "\n\n" + "\n".join(ui_hint_lines)


def _extract_hybrid_weight_profile(context: str) -> str:
    match = _HYBRID_WEIGHT_PROFILE_PATTERN.search(context or "")
    if not match:
        return ""
    return str(match.group(1)).strip().lower()


def _is_hybrid_context(context: str) -> bool:
    return "hybrid" in (context or "").lower()


def _is_lexical_semantic_hybrid_context(context: str) -> bool:
    lowered = (context or "").lower()
    if not _is_hybrid_context(lowered):
        return False

    lexical_negative_signals = (
        "no bm25",
        "without bm25",
        "no lexical component",
        "bm25 component is redundant",
    )
    if any(signal in lowered for signal in lexical_negative_signals):
        return False

    lexical_signals = (
        "bm25",
        "lexical",
        "keyword search",
        "inverted index",
    )
    semantic_signals = (
        "dense vector",
        "semantic",
        "neural",
        "embedding",
    )

    has_lexical = any(signal in lowered for signal in lexical_signals)
    has_semantic = any(signal in lowered for signal in semantic_signals)
    return has_lexical and has_semantic


def _resolve_hybrid_search_pipeline_weights(context: str) -> tuple[bool, list[float], str]:
    if not _is_lexical_semantic_hybrid_context(context):
        return False, [0.5, 0.5], ""

    profile = _extract_hybrid_weight_profile(context)
    profile_map: dict[str, list[float]] = {
        "semantic-heavy": [0.2, 0.8],  # [lexical, semantic]
        "balanced": [0.5, 0.5],        # [lexical, semantic]
        "lexical-heavy": [0.8, 0.2],   # [lexical, semantic]
    }

    if profile in profile_map:
        return True, profile_map[profile], profile
    return True, profile_map["balanced"], "balanced"


def _resolve_localhost_source_protection(context: str) -> tuple[bool, str]:
    """Detect whether sample data came from a localhost OpenSearch index.

    Tries four patterns in priority order: an explicit execution-policy line,
    a sample-document-loaded status message, a generic "Source: localhost OpenSearch index ..."
    line, and a JSON ``source_index_name`` field.
    Returns (is_localhost_source, source_index_name). When True, the source index
    should be protected from being overwritten by the worker.
    """
    text = context or ""

    policy_match = _LOCALHOST_SOURCE_POLICY_PATTERN.search(text)
    if policy_match:
        index_name = str(policy_match.group(1) or "").strip()
        return True, index_name

    status_match = _LOCALHOST_SAMPLE_STATUS_PATTERN.search(text)
    if status_match:
        index_name = str(status_match.group(1) or "").strip()
        return True, index_name

    source_line_match = _LOCALHOST_SOURCE_LINE_PATTERN.search(text)
    if source_line_match:
        index_name = str(
            source_line_match.group(1)
            or source_line_match.group(2)
            or source_line_match.group(3)
            or source_line_match.group(4)
            or ""
        ).strip()
        return True, index_name

    lowered = text.lower()
    if '"source_localhost_index": true' in lowered:
        json_match = _LOCALHOST_SOURCE_INDEX_JSON_PATTERN.search(text)
        if json_match:
            return True, str(json_match.group(1) or "").strip()
        return True, ""

    return False, ""


def _resolve_source_local_file(context: str) -> str:
    """Extract local sample-file path hints from execution context when available."""
    text = context or ""

    def _clean_path_candidate(raw_value: str) -> str:
        candidate = str(raw_value or "").strip().strip("'\"`")
        candidate = candidate.lstrip("([{<")
        candidate = candidate.rstrip(")]}>.,;!?")
        if not candidate:
            return ""
        lowered = candidate.lower()
        if lowered.startswith("http://") or lowered.startswith("https://"):
            return ""
        # Keep Windows drive paths (e.g., C:\data\sample.parquet) but reject URL-like tokens.
        if "://" in candidate and not re.match(r"^[A-Za-z]:[\\/]", candidate):
            return ""
        if not re.search(
            rf"(?:{SUPPORTED_SAMPLE_FILE_EXTENSION_REGEX})\b",
            candidate,
            flags=re.IGNORECASE,
        ):
            return ""
        return candidate

    def _find_path_token(haystack: str) -> str:
        for token_match in _SOURCE_LOCAL_FILE_TOKEN_PATTERN.finditer(haystack):
            candidate = _clean_path_candidate(token_match.group(0))
            if candidate:
                return candidate
        return ""

    json_match = _SOURCE_LOCAL_FILE_JSON_PATTERN.search(text)
    if json_match:
        candidate = _clean_path_candidate(str(json_match.group(1) or ""))
        if candidate:
            return candidate

    status_match = _SOURCE_LOCAL_FILE_STATUS_PATTERN.search(text)
    if status_match:
        candidate = _clean_path_candidate(str(status_match.group(1) or ""))
        if candidate:
            return candidate

    for source_match in _SOURCE_LINE_PATTERN.finditer(text):
        candidate = _find_path_token(str(source_match.group(1) or ""))
        if candidate:
            return candidate

    candidate = _find_path_token(text)
    if candidate:
        return candidate

    if BUILTIN_IMDB_SAMPLE_PATH in text:
        return BUILTIN_IMDB_SAMPLE_PATH

    return ""


def _extract_sample_doc_json(context: str) -> str:
    text = context or ""

    for line_match in _SAMPLE_DOC_LINE_PATTERN.finditer(text):
        raw_json = str(line_match.group(1) or "").strip()
        if not raw_json:
            continue
        try:
            parsed = json.loads(raw_json)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return json.dumps({"sample_doc": parsed}, ensure_ascii=False)

    sample_doc_marker = re.search(r'"sample_doc"\s*:\s*', text, re.IGNORECASE)
    if sample_doc_marker:
        start = text.find("{", sample_doc_marker.end())
        if start >= 0:
            decoder = json.JSONDecoder()
            try:
                parsed_value, _ = decoder.raw_decode(text[start:])
            except Exception:
                parsed_value = None
            if isinstance(parsed_value, dict):
                return json.dumps({"sample_doc": parsed_value}, ensure_ascii=False)
    return ""


def _has_canonical_search_capabilities(context: str) -> bool:
    in_section = False
    parsed_count = 0

    for raw_line in context.splitlines():
        line = raw_line.strip()
        lowered = line.lower()

        if not in_section and "search capabilities" in lowered:
            in_section = True
            continue

        if not in_section:
            continue

        if not line:
            if parsed_count > 0:
                break
            continue

        if line.startswith("##") or line.startswith("---"):
            break

        if not (line.startswith("-") or line.startswith("*")):
            if parsed_count > 0:
                break
            continue

        if _CANONICAL_CAPABILITY_PREFIX.match(line):
            parsed_count += 1
            continue

        return False

    return parsed_count > 0


def _strip_resume_marker(context: str) -> tuple[bool, str]:
    text = (context or "").strip()
    if text.startswith(_RESUME_WORKER_MARKER):
        return True, text[len(_RESUME_WORKER_MARKER):].strip()
    return False, text


def _extract_execution_report(response_text: str) -> dict | None:
    matches = _EXECUTION_REPORT_PATTERN.findall(response_text or "")
    if not matches:
        return None
    raw = matches[-1].strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _first_failed_step(report: dict) -> str:
    steps = report.get("steps", {}) if isinstance(report, dict) else {}
    if not isinstance(steps, dict):
        return ""
    for step in _CANONICAL_STEP_ORDER:
        if str(steps.get(step, "")).lower() == "failed":
            return step
    return ""


def _enforce_fail_stop_order(steps: dict[str, str]) -> tuple[dict[str, str], str]:
    """Enforce lock-order semantics: once a step fails, all following steps are skipped."""
    normalized: dict[str, str] = {}
    failed_step = ""
    stop = False

    for step in _CANONICAL_STEP_ORDER:
        value = str(steps.get(step, "skipped")).strip().lower()
        status = value if value in _ALLOWED_STEP_STATUS else "skipped"

        if stop:
            normalized[step] = "skipped"
            continue

        if status == "failed":
            normalized[step] = "failed"
            failed_step = step
            stop = True
            continue

        normalized[step] = status

    return normalized, failed_step


def _normalize_report(report: dict | None) -> dict:
    steps: dict[str, str] = {step: "skipped" for step in _CANONICAL_STEP_ORDER}
    if isinstance(report, dict):
        provided_steps = report.get("steps", {})
        if isinstance(provided_steps, dict):
            for step in _CANONICAL_STEP_ORDER:
                value = str(provided_steps.get(step, "skipped")).strip().lower()
                steps[step] = value if value in _ALLOWED_STEP_STATUS else "skipped"

    steps, failed_step = _enforce_fail_stop_order(steps)
    status = "failed" if failed_step else "success"
    if status == "failed" and not failed_step:
        failed_step = "unknown"

    notes: list[str] = []
    if isinstance(report, dict):
        raw_notes = report.get("notes", [])
        if isinstance(raw_notes, list):
            notes = [str(item).strip() for item in raw_notes if str(item).strip()]
        elif isinstance(raw_notes, str) and raw_notes.strip():
            notes = [raw_notes.strip()]
        explicit_failed = str(report.get("failed_step", "")).strip()
        if explicit_failed and not failed_step:
            if explicit_failed in steps:
                steps[explicit_failed] = "failed"
                steps, failed_step = _enforce_fail_stop_order(steps)
                status = "failed"

    return {
        "status": status,
        "steps": steps,
        "failed_step": failed_step if status == "failed" else "",
        "notes": notes,
    }


def _build_fallback_failed_report(reason: str, failed_step: str = "unknown") -> dict:
    steps = {step: "skipped" for step in _CANONICAL_STEP_ORDER}
    if failed_step in steps:
        steps[failed_step] = "failed"
    return {
        "status": "failed",
        "steps": steps,
        "failed_step": failed_step,
        "notes": [reason],
    }


def _merge_resume_progress(report: dict, previous_steps: dict, resume_step: str) -> dict:
    merged = dict(report)
    steps = dict(merged.get("steps", {}))
    resume_index = _CANONICAL_STEP_ORDER.index(resume_step) if resume_step in _CANONICAL_STEP_ORDER else 0
    for step in _CANONICAL_STEP_ORDER[:resume_index]:
        if str(previous_steps.get(step, "")).lower() == "success":
            steps[step] = "success"
    steps, failed_step = _enforce_fail_stop_order(steps)
    merged["steps"] = steps
    merged["status"] = "failed" if failed_step else "success"
    merged["failed_step"] = failed_step if failed_step else ""
    return merged


def _render_execution_report_block(report: dict) -> str:
    return "<execution_report>\n" + json.dumps(report, ensure_ascii=False) + "\n</execution_report>"


def _store_worker_run_state(execution_context: str, report: dict, report_raw: str) -> None:
    previous_state = get_last_worker_run_state()
    previous_context = str(previous_state.get("context", "")).strip() if isinstance(previous_state, dict) else ""
    try:
        previous_attempt = int(previous_state.get("attempt", 0)) if isinstance(previous_state, dict) else 0
    except Exception:
        previous_attempt = 0
    attempt = previous_attempt + 1 if previous_context == execution_context else 1

    source_local_file = _resolve_source_local_file(execution_context)
    source_local_file = str(source_local_file or "").strip()
    previous_source_local_file = (
        str(previous_state.get("source_local_file", "")).strip()
        if isinstance(previous_state, dict)
        else ""
    )
    if not source_local_file:
        source_local_file = previous_source_local_file

    has_localhost_source, source_index_name = _resolve_localhost_source_protection(execution_context)
    source_index_name = str(source_index_name or "").strip() if has_localhost_source else ""
    previous_source_index_name = (
        str(previous_state.get("source_index_name", "")).strip()
        if isinstance(previous_state, dict)
        else ""
    )
    if not source_index_name:
        source_index_name = previous_source_index_name

    sample_doc_json = _extract_sample_doc_json(execution_context)
    sample_doc_json = str(sample_doc_json or "").strip()
    previous_sample_doc_json = (
        str(previous_state.get("sample_doc_json", "")).strip()
        if isinstance(previous_state, dict)
        else ""
    )
    if not sample_doc_json:
        sample_doc_json = previous_sample_doc_json

    set_last_worker_context(execution_context)
    set_last_worker_run_state(
        {
            "context": execution_context,
            "failed_step": report.get("failed_step", ""),
            "steps": dict(report.get("steps", {})),
            "last_report_raw": report_raw,
            "attempt": attempt,
            "status": report.get("status", "failed"),
            "source_local_file": source_local_file,
            "source_index_name": source_index_name,
            "sample_doc_json": sample_doc_json,
        }
    )


def _resolve_resume_source_defaults(previous_state: dict | None) -> tuple[str, str, str]:
    """Extract checkpointed source defaults used for resume fallback."""
    if not isinstance(previous_state, dict):
        return "", "", ""
    source_local_file = str(previous_state.get("source_local_file", "")).strip()
    source_index_name = str(previous_state.get("source_index_name", "")).strip()
    sample_doc_json = str(previous_state.get("sample_doc_json", "")).strip()
    return source_local_file, source_index_name, sample_doc_json


def _contains_model_memory_failure(response_text: str) -> bool:
    lowered = (response_text or "").lower()
    has_model_failure = any(token in lowered for token in _MODEL_FAILURE_TOKENS)
    has_memory_failure = any(token in lowered for token in _MODEL_MEMORY_FAILURE_TOKENS)
    return has_model_failure and has_memory_failure


def _enforce_model_setup_failure_policy(response_text: str, report: dict) -> tuple[str, dict]:
    normalized_response = str(response_text or "")
    normalized_report = dict(report) if isinstance(report, dict) else _build_fallback_failed_report(
        "Worker response missing normalized execution report.",
        failed_step="unknown",
    )

    if _contains_model_memory_failure(normalized_response):
        normalized_report = _build_fallback_failed_report(
            "Model setup failed due to OpenSearch memory pressure. Reconnect Docker and retry. "
            "Lexical-only fallback is disabled for this failure mode.",
            failed_step="model_setup",
        )

    if str(normalized_report.get("failed_step", "")).strip().lower() == "model_setup":
        if "reconnect docker" not in normalized_response.lower():
            normalized_response = normalized_response.rstrip() + "\n\n" + _DOCKER_RECONNECT_GUIDANCE

    return normalized_response, normalized_report


def _finalize_worker_response(response_text: str, execution_context: str, report: dict) -> str:
    normalized_response = _append_ui_access_hint(response_text, report)
    report_block = _render_execution_report_block(report)
    final_text = normalized_response.rstrip() + "\n\n" + report_block
    _store_worker_run_state(execution_context, report, report_block)
    mark_execution_completed()
    return final_text


def build_worker_initial_input(
    execution_context: str,
    *,
    resume_mode: bool = False,
    resume_step: str = "",
    previous_steps: dict[str, str] | None = None,
) -> str:
    """
    Build the worker user-input prompt from finalized context.
    This is shared by server-side worker execution and MCP manual execution mode.
    In MCP mode, mcp_server builds `worker_initial_input` from this for client-LLM worker turns.

    Args:
        execution_context: The finalized execution context.
        resume_mode: Whether to resume from a failed step.
        resume_step: The step to resume from.
        previous_steps: The previous steps.

    Returns:
        The worker user-input prompt.

    Example:
        >>> build_worker_initial_input("Here is the approved plan:...")
        "Here is the approved plan:\n...\nPlease implement this plan.\n..."
    """
    requires_hybrid_search_pipeline, hybrid_weights, hybrid_profile = _resolve_hybrid_search_pipeline_weights(
        execution_context
    )
    protect_localhost_source, localhost_source_index_name = _resolve_localhost_source_protection(
        execution_context
    )

    instruction = (
        f"Here is the approved plan:\n{execution_context}\n"
        "Please implement this plan.\n"
        "Capability alignment step (must run after pipeline setup and before verification docs):\n"
        "- Call apply_capability_driven_verification with worker_output set to the full approved plan text above and explicit index_name for this run.\n"
        "- This call also indexes capability-selected verification docs (count=10 by default).\n"
        "- Do not omit index_name in that call.\n"
        "- The result contains a 'suggestion_meta' list. Pass it as JSON to set_search_ui_suggestions(index_name, suggestion_meta_json) before launching the UI.\n"
        "- Continue even if capability coverage is partial; capture any notes in your final report.\n"
        "Verification requirements:\n"
        "- Keep verification docs for interactive testing.\n"
        "- Launch UI with launch_search_ui only when verification is clean (indexed_count > 0 and errors is empty).\n"
        "- Do not perform cleanup unless the user explicitly asks.\n"
        "Search capabilities precondition:\n"
        "- The provided context includes canonical 'Search Capabilities' from solution_planning_assistant.\n"
        "- Treat planner-provided search capabilities as authoritative for verification/suggestion alignment.\n"
        "Schema alignment requirements:\n"
        "- Use only existing source fields from sample/index mapping for pipeline field_map.\n"
        "- Do not invent a `text` source field if it does not exist.\n"
        "- Schema-only exception: if no suitable source field exists for embeddings, skip embedding setup only for this schema mismatch.\n"
        "- If model creation/deployment fails (especially due to memory pressure), stop execution, mark model_setup as failed, and ask the user to reconnect Docker and retry.\n"
        "- Do NOT continue with lexical-only fallback after model creation/deployment failures.\n"
        "- Enforce producer-driven boolean typing: map fields as boolean only for native booleans; map string flags ('0'/'1') as keyword.\n"
        "Mandatory final output:\n"
        "- End your response with exactly one <execution_report> JSON block with canonical step IDs and statuses.\n"
        "- If any step fails, set status='failed' and set failed_step to the earliest failed canonical step.\n"
        "- Lock-order hard stop: do not execute any later step after the first failed step."
    )

    if protect_localhost_source and localhost_source_index_name:
        instruction += (
            "\nLocalhost source policy:\n"
            f"- Sample source is localhost OpenSearch index '{localhost_source_index_name}'.\n"
            "- Do NOT recreate this index (replace_if_exists=false). "
            "Choose a different target index name to avoid overwriting the source data.\n"
        )

    if requires_hybrid_search_pipeline:
        instruction += (
            "\nHybrid lexical+semantic pipeline requirements:\n"
            f"- Detected hybrid weight profile: {hybrid_profile}.\n"
            f"- Use hybrid query weight order [lexical, semantic] with weights {hybrid_weights}.\n"
            "- You MUST create and attach a search pipeline by calling create_and_attach_pipeline.\n"
            "- Call signature requirements: pipeline_type='search', is_hybrid_search=True, "
            f"hybrid_weights={hybrid_weights}.\n"
            "- You MAY pass pipeline_body={} to use default Step-3 hybrid normalization pipeline generation.\n"
            "- Ensure index.search.default_pipeline is attached to the target index.\n"
        )

    if resume_mode:
        steps = previous_steps or {}
        succeeded_before: list[str] = []
        if resume_step in _CANONICAL_STEP_ORDER:
            resume_index = _CANONICAL_STEP_ORDER.index(resume_step)
            for step in _CANONICAL_STEP_ORDER[:resume_index]:
                if str(steps.get(step, "")).lower() == "success":
                    succeeded_before.append(step)
        instruction += (
            "\nRecovery mode:\n"
            f"- Resume from failed step: {resume_step}\n"
            "- Do not redo steps that already succeeded before this step.\n"
            f"- Previously successful steps before resume point: {succeeded_before}\n"
            "- Preserve already-created resources and proceed from the failure point.\n"
        )
    return instruction


def commit_execution_report(
    worker_response: str,
    *,
    execution_context: str = "",
) -> dict[str, object]:
    """Parse, normalize, and persist a worker execution report."""
    resume_mode, parsed_context = _strip_resume_marker(execution_context)
    previous_state = get_last_worker_run_state()
    previous_steps: dict[str, str] = {}
    resume_step = ""

    if not parsed_context and isinstance(previous_state, dict):
        parsed_context = str(previous_state.get("context", "")).strip()

    if resume_mode and isinstance(previous_state, dict):
        resume_step = str(previous_state.get("failed_step", "")).strip()
        if isinstance(previous_state.get("steps", {}), dict):
            previous_steps = dict(previous_state.get("steps", {}))

    if not parsed_context:
        return {
            "error": "execution_context is required to commit execution report.",
            "details": [
                "Pass execution_context returned by execute_plan()/retry_execution().",
            ],
        }

    parsed_report = _extract_execution_report(worker_response)
    if parsed_report is None:
        return {
            "error": "worker_response is missing a valid <execution_report> JSON block.",
            "details": [
                "Include exactly one <execution_report>{...}</execution_report> block.",
            ],
        }

    normalized_report = _normalize_report(parsed_report)
    if resume_mode and resume_step:
        normalized_report = _merge_resume_progress(
            normalized_report,
            previous_steps,
            resume_step,
        )

    normalized_response, normalized_report = _enforce_model_setup_failure_policy(
        str(worker_response or ""),
        normalized_report,
    )
    report_block = _render_execution_report_block(normalized_report)
    _store_worker_run_state(parsed_context, normalized_report, report_block)
    mark_execution_completed()

    return {
        "status": "Execution report stored.",
        "execution_context": parsed_context,
        "execution_report": normalized_report,
        "normalized_worker_response": normalized_response,
    }


def _run_worker_once(context: str) -> str:
    """Execute one worker attempt. Exceptions are propagated to the caller."""
    model_id = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

    resume_mode, execution_context = _strip_resume_marker(context)
    previous_state = get_last_worker_run_state() if resume_mode else {}
    resume_step = ""
    previous_steps: dict[str, str] = {}
    checkpoint_source_local_file = ""
    checkpoint_source_index_name = ""
    checkpoint_sample_doc_json = ""

    if resume_mode:
        (
            checkpoint_source_local_file,
            checkpoint_source_index_name,
            checkpoint_sample_doc_json,
        ) = _resolve_resume_source_defaults(previous_state)
        if not execution_context and isinstance(previous_state, dict):
            execution_context = str(previous_state.get("context", "")).strip()
        resume_step = str(previous_state.get("failed_step", "")).strip() if isinstance(previous_state, dict) else ""
        previous_steps = (
            dict(previous_state.get("steps", {}))
            if isinstance(previous_state, dict) and isinstance(previous_state.get("steps", {}), dict)
            else {}
        )
        if not execution_context or not resume_step:
            report = _build_fallback_failed_report(
                "Recovery precondition failed: no recoverable checkpoint context/failed_step found.",
                failed_step="unknown",
            )
            return _finalize_worker_response(
                "Recovery precondition failed: no recoverable checkpoint found. Run full execution first.",
                execution_context or "",
                report,
            )

    if not _has_canonical_search_capabilities(execution_context):
        report = _build_fallback_failed_report(
            "Missing canonical Search Capabilities section in worker context.",
            failed_step="capability_precheck",
        )
        return _finalize_worker_response(
            "Precondition failed: missing canonical 'Search Capabilities' section in worker context. "
            "Expected applicable bullets prefixed with Exact:/Semantic:/Structured:/Combined:/Autocomplete:/Fuzzy:."
            ,
            execution_context,
            report,
        )

    protect_localhost_source, localhost_source_index_name = _resolve_localhost_source_protection(
        execution_context
    )

    model = BedrockModel(
        model_id=model_id,
        max_tokens=8192,
        additional_request_fields={
            "thinking": {
                "type": "enabled",
                "budget_tokens": 1024,
            }
        }
    )

    default_source_index_name = (
        str(localhost_source_index_name).strip()
        if protect_localhost_source and localhost_source_index_name
        else ""
    )
    if not default_source_index_name:
        default_source_index_name = checkpoint_source_index_name
    inferred_source_local_file = _resolve_source_local_file(execution_context)
    if not inferred_source_local_file:
        inferred_source_local_file = checkpoint_source_local_file
    default_source_local_file = ""
    if inferred_source_local_file:
        candidate_path = Path(inferred_source_local_file).expanduser()
        if candidate_path.exists() and candidate_path.is_file():
            default_source_local_file = str(candidate_path)
        elif inferred_source_local_file == BUILTIN_IMDB_SAMPLE_PATH:
            # Keep workspace-relative built-in sample path for portable runs.
            default_source_local_file = inferred_source_local_file
        else:
            default_source_local_file = inferred_source_local_file
    default_sample_doc_json = _extract_sample_doc_json(execution_context)
    if not default_sample_doc_json:
        default_sample_doc_json = checkpoint_sample_doc_json

    def create_index(
        index_name: str,
        body: dict = None,
        replace_if_exists: bool = True,
        sample_doc_json: str = "",
        source_local_file: str = "",
        source_index_name: str = "",
    ) -> str:
        """Create an index; protect the localhost source index from being overwritten."""
        effective_replace = bool(replace_if_exists)
        if protect_localhost_source:
            effective_replace = False
        effective_source_index = str(source_index_name or "").strip() or default_source_index_name
        effective_source_local_file = str(source_local_file or "").strip() or default_source_local_file
        effective_sample_doc_json = str(sample_doc_json or "").strip() or default_sample_doc_json

        return create_index_impl(
            index_name=index_name,
            body=body,
            replace_if_exists=effective_replace,
            sample_doc_json=effective_sample_doc_json,
            source_local_file=effective_source_local_file,
            source_index_name=effective_source_index,
        )

    def apply_capability_driven_verification(
        worker_output: str,
        index_name: str = "",
        count: int = 10,
        id_prefix: str = "verification",
        sample_doc_json: str = "",
        source_local_file: str = "",
        source_index_name: str = "",
        existing_verification_doc_ids: str = "",
    ) -> dict[str, object]:
        effective_source_index = str(source_index_name or "").strip() or default_source_index_name
        effective_source_local_file = str(source_local_file or "").strip() or default_source_local_file
        effective_sample_doc_json = str(sample_doc_json or "").strip() or default_sample_doc_json
        if not effective_source_index:
            _, inferred_source_index = _resolve_localhost_source_protection(worker_output)
            effective_source_index = str(inferred_source_index or "").strip()
        if not effective_source_index:
            effective_source_index = checkpoint_source_index_name
        if not effective_sample_doc_json:
            inferred_sample_doc_json = _extract_sample_doc_json(worker_output)
            effective_sample_doc_json = str(inferred_sample_doc_json or "").strip()
        if not effective_source_local_file:
            effective_source_local_file = checkpoint_source_local_file
        if not effective_sample_doc_json:
            effective_sample_doc_json = checkpoint_sample_doc_json
        return apply_capability_driven_verification_impl(
            worker_output=worker_output,
            index_name=index_name,
            count=count,
            id_prefix=id_prefix,
            sample_doc_json=effective_sample_doc_json,
            source_local_file=effective_source_local_file,
            source_index_name=effective_source_index,
            existing_verification_doc_ids=existing_verification_doc_ids,
        )
    
    agent = Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[
            tool(create_index),
            tool(search_opensearch_org),
            tool(create_and_attach_pipeline),
            tool(apply_capability_driven_verification),
            tool(create_bedrock_embedding_model),
            tool(create_local_pretrained_model),
            tool(create_bedrock_agentic_model_with_creds),
            tool(create_agentic_search_flow_agent),
            tool(create_agentic_search_pipeline),
            tool(launch_search_ui),
            tool(delete_doc),
            tool(set_search_ui_suggestions),
        ],
        callback_handler=ThinkingCallbackHandler(output_color="\033[92m") # Green for worker
    )

    instruction = build_worker_initial_input(
        execution_context,
        resume_mode=resume_mode,
        resume_step=resume_step,
        previous_steps=previous_steps,
    )
    
    response = agent(instruction)
    response_text = str(response)
    parsed_report = _extract_execution_report(response_text)
    if parsed_report is None:
        normalized_report = _build_fallback_failed_report(
            "Worker response missing/invalid <execution_report> JSON block.",
            failed_step="unknown",
        )
    else:
        normalized_report = _normalize_report(parsed_report)

    if resume_mode:
        normalized_report = _merge_resume_progress(normalized_report, previous_steps, resume_step)

    response_text, normalized_report = _enforce_model_setup_failure_policy(response_text, normalized_report)

    return _finalize_worker_response(response_text, execution_context, normalized_report)


def _run_worker_with_exception_recovery(
    context: str,
    max_retries_after_exception: int = 1,
) -> str:
    execution_context = _strip_resume_marker(context)[1]
    max_attempts = max(1, int(max_retries_after_exception) + 1)
    recovery_diagnostics: list[str] = []
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return _run_worker_once(context)
        except Exception as e:
            last_error = e
            recovery_diagnostics.append(f"attempt {attempt} exception: {e}")
            if attempt >= max_attempts:
                break

            recovered, recovery_note = recover_local_opensearch_container()
            recovery_diagnostics.append(f"docker recovery: {recovery_note}")
            if not recovered:
                break

    message = f"Error executing worker agent: {last_error}"
    reason = f"Unhandled worker exception: {last_error}"
    if recovery_diagnostics:
        diagnostics_text = "; ".join(recovery_diagnostics)
        message = f"{message}\nRecovery diagnostics: {diagnostics_text}"
        reason = f"{reason}. Recovery diagnostics: {diagnostics_text}"
    fallback = _build_fallback_failed_report(
        reason,
        failed_step="unknown",
    )
    return _finalize_worker_response(message, execution_context, fallback)


@tool
def worker_agent(context: str) -> str:
    """Set up the index and models based on the final plan.
    
    Args:
        context: The detailed technical plan that has been approved by the user.
        
    Returns:
        str: Status of the execution.
    """
    print(f"\n[Worker] Received context for execution:\n{context}", file=sys.stderr)
    return _run_worker_with_exception_recovery(context, max_retries_after_exception=1)

SAMPLE_CONTEXT = """
**Corpus Details:**
- Document Size: 10 million documents
- Language: English (mono-lingual)
- Sample Document: Text content similar to "The quick brown fox jumps over the lazy dog. This is a sample document for testing search capabilities."

**Search Architecture:**
- Hybrid Search: Dense Vector (diskANN) + BM25 keyword search
- Score Normalization: Min-Max or L2 normalization
- Hybrid Weighting: Start with 0.5/0.5, tune toward 0.6-0.7 for dense vector emphasis

**DiskANN Vector Configuration:**
- Engine: faiss
- Method: hnsw
- Mode: on_disk (Binary Quantization + disk re-ranking)
- Parameters for Maximum Accuracy:
  - ef_construction: 512-1000
  - ef_search: 200-500
  - m: 48-64
- Storage: Fast NVMe SSDs for optimal disk re-ranking performance

**Embedding Model:**
- Deployment: SageMaker GPU Endpoint
- Model Options:
  - Primary: sentence-transformers/all-mpnet-base-v2 (768 dimensions)
- Inference: GPU-based for 5-20ms latency

**BM25 Configuration:**
- Standard inverted index with English analyzer

**User Priorities:**
- Best accuracy possible
- No budget constraints
- User explicitly chose diskANN despite accuracy trade-offs

Please proceed with index and model setup.
"""

SAMPLE_CONTEXT_2 = """
- Retrieval Method: Hybrid Search (Dense Vector + Sparse Vector)
        - Dense Vector Configuration:
          * Algorithm: HNSW (lucene or faiss engine)
          * Model: amazon.titan-embed-text-v2 (1024 dimensions)
          * Deployment: Amazon Bedrock API
        - Sparse Vector Configuration:
          * Mode: Doc-only
          * Ingestion Model: amazon/neural-sparse/opensearch-neural-sparse-encoding-doc-v2-mini
          * Ingestion Deployment: OpenSearch Node (CPU)
          * Query Tokenizer: amazon/neural-sparse/opensearch-neural-sparse-tokenizer-v1
          * Query Deployment: OpenSearch Node (CPU)
          * Index Backend: rank_features (exact search)
        - Score Normalization: Min-Max or L2 (default 50/50 weight split)
        - No BM25 component (redundant with sparse vector)
"""

SAMPLE_CONTEXT_3 = """
- **Retrieval Method:** Hybrid Search (Dense Vector + Sparse Vector Neural Sparse)
        - **Dense Vector Configuration:**
          - Algorithm: HNSW (Hierarchical Navigable Small World)
          - Model Deployment: Amazon Bedrock Embedding API
          - Model: amazon.titan-embed-text-v2 (1024 dimensions)
        - **Sparse Vector Configuration:**
          - Mode: Doc-Only (for optimal query latency)
          - Index Backend: SEISMIC (ANN for sparse vectors)
          - Ingestion Model: amazon/neural-sparse/opensearch-neural-sparse-encoding-doc-v3-gte (deployed on SageMaker GPU, ml.g5.xlarge)
          - Search Model: amazon/neural-sparse/opensearch-neural-sparse-tokenizer-v1 (deployed on OpenSearch Nodes CPU)
        - **Score Normalization:** OpenSearch hybrid query with normalization (min-max or L2)
"""

if __name__ == "__main__":
    print(worker_agent(SAMPLE_CONTEXT_3))
