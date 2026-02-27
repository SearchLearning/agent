import asyncio
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field

from strands import Agent, tool
from strands.models import BedrockModel
if __package__ in {None, ""}:
    from pathlib import Path
    import sys

    _SCRIPT_EXECUTION_PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
    if _SCRIPT_EXECUTION_PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _SCRIPT_EXECUTION_PROJECT_ROOT)

from opensearch_orchestrator.scripts.handler import ThinkingCallbackHandler
from opensearch_orchestrator.scripts.tools import (
    BUILTIN_IMDB_SAMPLE_PATH,
    submit_sample_doc,
    submit_sample_doc_from_local_file,
    submit_sample_doc_from_localhost_index,
    submit_sample_doc_from_url,
)
from opensearch_orchestrator.scripts.shared import (
    Phase,
    SUPPORTED_SAMPLE_FILE_FORMATS_COMMA,
    SUPPORTED_SAMPLE_FILE_FORMATS_MARKDOWN,
    SUPPORTED_SAMPLE_FILE_FORMATS_SLASH,
    read_multiline_input,
    read_single_choice_input,
    check_and_clear_execution_flag,
    looks_like_new_request,
    looks_like_cancel,
    looks_like_cleanup_request,
    looks_like_worker_retry,
    looks_like_url_message,
    looks_like_local_path_message,
    looks_like_localhost_index_message,
    looks_like_builtin_imdb_sample_request,
    clear_last_worker_context,
    clear_last_worker_run_state,
    get_last_worker_run_state,
)
from opensearch_orchestrator.scripts.opensearch_ops_tools import cleanup_docs
from opensearch_orchestrator.solution_planning_assistant import solution_planning_assistant, reset_planner_agent
from opensearch_orchestrator.planning_session import PlanningSession
from opensearch_orchestrator.orchestrator_engine import OrchestratorEngine
from opensearch_orchestrator.worker import (
    worker_agent as worker_agent_impl,
    _extract_sample_doc_json as worker_extract_sample_doc_json,
    _resolve_localhost_source_protection as worker_resolve_localhost_source_protection,
    _resolve_source_local_file as worker_resolve_source_local_file,
)


# -------------------------------------------------------------------------
# System Prompt
# -------------------------------------------------------------------------

SYSTEM_PROMPT = (
    """
You are an intelligent Orchestrator Agent for an OpenSearch Solution Architect system.

Your goal is to guide the user from initial requirements to a finalized, executed solution.

### Workflow Phases

1.  **Collect Sample Document (Mandatory First Step)**:
    *   A sample document is required before any planning or execution.
    *   **Pre-loaded samples (highest priority)**: If the user message contains a
        "System note" stating a sample document has already been loaded, trust it
        and proceed to Phase 2. Do NOT ask the user to paste content or re-upload.
    *   Supported sample sources:
    *   `1` Built-in IMDb sample file: `opensearch_orchestrator/scripts/sample_data/imdb.title.basics.tsv`
    *   `2` User-provided local path or URL
    *       Supported formats: __SUPPORTED_SAMPLE_FILE_FORMATS_MARKDOWN__
    *       Example: `/path/to/your/data.json` or `https://example.com/sample.json`
    *   `3` Existing localhost OpenSearch index
    *       Authentication defaults to `admin` / `myStrongPassword123!`.
    *       Ask for index name first and assume default auth unless user explicitly asks for no-auth or custom credentials.
    *       Do NOT ask the user to choose `default` auth mode.
    *       If auth follow-up is needed, only offer: no-auth (`none`) or custom credentials (`custom`).
    *       If user has no credentials, use no-auth mode.
    *       If user provides credentials explicitly, use that username/password.
    *       If user already provided both username and password, do NOT ask for credentials again.
    *       If option 3 is selected without a valid index name, list current
        non-system localhost indices and ask the user to select one.
    *   `4` Paste sample content directly (preferably JSON records)
    *       Paste 1-3 representative JSON records, for example:
    *       `{"id":"1","title":"Example A","description":"Sample text A","category":"demo"}`
    *       `{"id":"2","title":"Example B","description":"Sample text B","category":"demo"}`
    *   If the user pastes sample content, call `submit_sample_doc`.
    *   If no sample is available yet, ask the user once to choose one of the 4 options above.
    *   Do not skip this step.

2.  **Clarify Requirements**:
    *   Based on your analysis of the sample doc, engage the user only **once** to gather REMAINING critical information.
    *   **Infer First**: If sample was loaded from URL/local source/localhost index, use inferred metadata from tool output before asking questions.
    *   **Document Size**: Infer approximate size from source/profile when possible. Default assumption: the loaded data is a representative sample and production data will continue to grow. Do NOT ask whether the source is complete, and do NOT ask growth-projection questions unless the user explicitly asks for capacity sizing.
    *   **Languages**: Infer likely language/script directly from sample data and treat sample/schema language coverage as final truth for planning. Do NOT ask about future additional languages or future cross-lingual needs unless the user explicitly asks for multilingual expansion.
    *   **Budget/Cost**: Use a single-choice selection with exactly two options: `flexible` or `cost-sensitive`. Do NOT ask open-ended free-text budget questions.
    *   **Performance vs Accuracy Priority**: Use a single-choice selection with exactly three options: `speed-first`, `balanced`, `accuracy-first`. Do NOT ask explicit response-time/P99 targets unless the user explicitly requests SLA-driven tuning.
    *   **Semantic Search Query-Pattern Preference (pre-planning)**: If text-based search is in scope, ask this once during requirement clarification before calling `solution_planning_assistant`.
        Use a fixed three-option query-pattern choice:
        - Mostly exact keywords (like "Carmencita 1894")
        - Semantic / natural language (like "early silent films about dancers")
        - Balanced mix of both (default)
        If pre-processing already provided either
        `Requirements note: semantic query-pattern preference = ...` or `Hybrid Weight Profile: ...`,
        treat query-pattern preference as already collected and do NOT ask again.
    *   **Natural-Language / Concept Search Scope**: Use semantic query-pattern preference to determine emphasis.
        If query pattern is semantic-dominant, treat natural-language/concept retrieval as a primary requirement.
        Do NOT ask a separate yes/no confirmation question.
    *   **Mapping-Clarity Assumption**: Do NOT ask whether mapping guidance is clear (including `isAdult` typing guidance). Assume users will raise concerns if needed.
    *   **Semantic Expansion Explanation Assumption**: Assume no proactive deep-dive explanation is desired. Do NOT ask whether the user wants more semantic-expansion explanation unless the user explicitly asks for details.
    *   **Model Deployment (Production)**: OpenSearch node, SageMaker endpoint, and external embedding API deployments are all valid.
        Ask the deployment-preference question when the selected query pattern is balanced
        or semantic-dominant (mostly natural language / semantic).
    *   **Launch UI Execution Scope**: Launch UI setup provisions only local OpenSearch-hosted pretrained models (dense or sparse).
        Treat this as tooling scope, not as a user rejection of SageMaker/external APIs.
    *   **Query Features (Conditional Prefix/Wildcard)**: Assume range queries and aggregations/facets are required by default. Include geospatial search only when lat/lon fields exist in the sample/schema.
        Assume prefix/wildcard matching is not required unless the user explicitly asks for it.
        Prefix/wildcard matching implies lexical BM25 capability.
    *   **Search Use Cases (Default Assumption)**: Do NOT ask a specific-use-cases checklist. Assume core use cases are required: range/filter retrieval, aggregation analytics, and pattern/trend analysis.
    *   **Additional Requirements Defaults (Do Not Ask)**:
        - Keep query/filter/aggregation capabilities only when supported by the sample/schema fields; skip unsupported capabilities.
        - Assume no specific dashboard or visualization requirement unless explicitly requested.
        - Assume real-time ingestion/search is required unless the user explicitly asks for batch-only behavior.
        - Assume no additional custom requirements unless the user explicitly provides them.
        - Do NOT ask checklist follow-up questions for these defaults.
    *   **Text Search Inference**: Determine text-search need by field analysis: if any field is suitable for non-keyword full-text/semantic retrieval, treat it as "yes, text-based search is needed."
    
    Only prompt the user **once** for these details. 
    Even there are missing information, do not repeatedly request in separate turns. Do proper assumptions from sample data and provided information.

3.  **Proposal & Refinement (Handover)**:
    *   Once the required information is gathered, call `solution_planning_assistant` with the collected context.
    *   **IMPORTANT**: The `solution_planning_assistant` will take over the conversation. It will handle the proposal presentation, answering user questions, and refining the plan until the user is satisfied.
    *   You (Orchestrator) will *wait* for this tool to complete.
    *   When hybrid retrieval is in scope, include a normalized context line passed to planner:
        *   `Hybrid Weight Profile: semantic-heavy|balanced|lexical-heavy`
        *   Use `balanced` if user did not provide a clear preference.
    *   The tool will return a structured result containing:
        *   `SOLUTION`: The final technical plan.
        *   `SEARCH_CAPABILITIES`: Canonical capability bullets for downstream verification/suggestion flow.
        *   `KEYNOTE`: A summary of the refinement conversation.

4.  **Execution (Final Step)**:
    *   When `solution_planning_assistant` returns `SOLUTION`, `SEARCH_CAPABILITIES`, and `KEYNOTE`:
    *   Call `worker_agent` immediately with a combined context that includes at least `SOLUTION` and `SEARCH_CAPABILITIES` (you may include `KEYNOTE` as well).
    *   You may mention `KEYNOTE` points to the user as confirmation their preferences were heard, but the primary action is to trigger the worker with planner-authored capabilities preserved.
    *   Confirm completion to the user.

### Important Rules

*   **Delegation**: Do NOT generate the plan yourself. Do NOT answer technical questions yourself. Always delegate to `solution_planning_assistant` for the planning and Q&A phase.
*   **State Awareness**: The `solution_planning_assistant` is interactive. Once you call it, trust it to handle the refinement loop.
*   **Worker Call**: You MUST call `worker_agent` immediately after the planning phase completes.
*   **Sample Doc Gate**: A sample document must exist before clarification/planning. When the message says a sample is pre-loaded, proceed directly — do not re-collect.
*   **No Redundant Questions**: Do not ask users to restate values already inferred from source profile/sample data. Only ask confirmation or forward-looking deltas.
*   **Performance Question Scope**: Do not ask a separate numeric latency-target question. Use only the speed-vs-accuracy priority question unless the user explicitly asks for P99/SLA tuning.
*   **Multiple-Choice Clarifications**: For budget, performance priority, semantic-search query-pattern preference (before solution planning when text-based search is in scope), and production model deployment preference (when the query-pattern preference is balanced or semantic-dominant), use fixed option selection format, not free-text prompts.
*   **Pre-Collected Query Pattern**: If `Requirements note: semantic query-pattern preference = ...` or `Hybrid Weight Profile: ...` appears in system context, query-pattern preference is already resolved. Do not ask a duplicate query-pattern question.
*   **Default Query Features**: Treat range queries and aggregations/facets as required baseline capabilities. Geospatial is conditional: include only if lat/lon fields exist in sample/schema.
*   **Prefix/Wildcard Scope**: Do NOT ask a clarification question for prefix/wildcard matching. Assume it is disabled unless the user explicitly requests it. If enabled, treat lexical BM25 capability as required. If disabled, do not force BM25 solely for prefix/wildcard support.
*   **Hybrid Method Limit**: If hybrid retrieval is used, limit to two retrieval methods. Do NOT propose three-way hybrids such as sparse + BM25 + dense.
*   **No Query-Pattern Checklist**: Do not ask example-style query-pattern questions when sample/schema is available. Infer directly.
*   **Default Specific Use Cases**: Assume yes for core use cases (range/filter retrieval, aggregation analytics, and pattern/trend analysis). Do not ask a separate specific-use-cases checklist.
*   **Additional Requirements Defaults**: Assume keep-if-supported query capability handling, no dashboard/visualization requirement, real-time ingestion/search required, and no extra custom requirements unless the user explicitly asks/provides them. Do not ask follow-up checklist questions for these defaults.
*   **Text Search Decision Rule**: If sample/schema includes any non-keyword text field suitable for semantic/full-text search, classify as "yes, text-based search is needed."
*   **Natural-Language/Concept Search Rule**: Use the selected semantic query pattern to set priority. If semantic-dominant, treat natural-language/concept retrieval as primary. Do not ask a separate confirmation question.
*   **Mapping Clarity Rule**: Do not ask "is mapping guidance clear?" style questions (including `isAdult` mapping clarity checks). Assume users will raise concerns if they have any.
*   **Sample Final-Truth Rule**: Treat the provided sample/schema as final truth. Do not ask whether future text-heavy fields will be added, and do not ask future multilingual/cross-lingual expectation questions.
*   **Semantic Expansion Explanation Assumption**: Assume no proactive deep-dive explanation preference. Do not ask whether the user wants more semantic-expansion explanation unless they explicitly request it.
*   **Deployment Scope**: For planning, allow OpenSearch node, SageMaker, or external embedding API deployment preferences.
    For Launch UI execution, local OpenSearch-hosted pretrained models are the supported setup path.
*   **Producer-Driven Typing**: Reinforce strict schema typing in planning/execution context: map `boolean` only when producer sends native booleans; if producer sends string flags like `0`/`1`, map as `keyword`.
*   **Persona**: You are the interface; be helpful, polite, and professional.
"""
    .replace(
        "__SUPPORTED_SAMPLE_FILE_FORMATS_MARKDOWN__",
        SUPPORTED_SAMPLE_FILE_FORMATS_MARKDOWN,
    )
)


# -------------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------------

MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

_RESUME_WORKER_MARKER = "[RESUME_WORKER_FROM_FAILED_STEP]"
_SYSTEM_SOURCE_CONTEXT_HEADER = "[SYSTEM SOURCE CONTEXT]"
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


@dataclass
class SessionState:
    sample_doc_json: str | None = None
    source_local_file: str | None = None
    source_index_name: str | None = None
    source_index_doc_count: int | None = None
    inferred_text_search_required: bool | None = None
    inferred_semantic_text_fields: list[str] = field(default_factory=list)
    budget_preference: str | None = None
    performance_priority: str | None = None
    model_deployment_preference: str | None = None
    prefix_wildcard_enabled: bool | None = None
    hybrid_weight_profile: str | None = None
    pending_localhost_index_options: list[str] = field(default_factory=list)
    localhost_auth_mode: str = _LOCALHOST_AUTH_MODE_DEFAULT
    localhost_auth_username: str | None = None
    localhost_auth_password: str | None = None

_NUMERIC_STRING_PATTERN = re.compile(r"[+-]?\d+(\.\d+)?")
_DATEISH_STRING_PATTERN = re.compile(
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}"
    r"(?:[T\s]\d{1,2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?"
)
_EXCLUDED_TEXT_FIELD_NAME_TOKENS = {
    "id",
    "uuid",
    "guid",
    "code",
    "zip",
    "postal",
    "lat",
    "lon",
    "lng",
    "latitude",
    "longitude",
}

_DEFAULT_QUERY_FEATURES_NOTE = (
    "Requirements note: default query features are required: range queries "
    "and aggregations/facets. "
    "Geospatial is conditional: include only when lat/lon fields exist in sample/schema. "
    "Do NOT ask the user to confirm these defaults."
)
_MODEL_DEPLOYMENT_SCOPE_NOTE = (
    "Requirements note: production model deployment options include OpenSearch node, SageMaker endpoint, "
    "and external embedding APIs. If text-based search is in scope and query-pattern preference is balanced or mostly-semantic, "
    "capture/reflect the preferred production deployment mode. "
    "Execution policy: Launch UI setup provisions local OpenSearch-hosted pretrained models only "
    "(dense or sparse) for bootstrap."
)
_PERFORMANCE_PRIORITY_SCOPE_NOTE = (
    "Requirements note: performance clarification should use one question only: "
    "speed-first, accuracy-first, or balanced. "
    "Do NOT ask explicit numeric latency/P99 targets unless the user explicitly asks for SLA tuning."
)
_SAMPLE_FINAL_TRUTH_NOTE = (
    "Requirements note: treat sample data/schema as final truth for planning. "
    "Do NOT ask whether future text-heavy fields will be added, and do NOT ask future multilingual/cross-lingual expectation questions."
)
_SEMANTIC_EXPANSION_EXPLANATION_NOTE = (
    "Requirements note: semantic expansion explanation preference = no proactive deep-dive. "
    "Do NOT ask whether the user wants more semantic-expansion explanation unless they explicitly request details."
)
_NATURAL_LANGUAGE_CONCEPT_SEARCH_NOTE = (
    "Requirements note: natural-language/concept-based retrieval should be treated as primary "
    "because the semantic query pattern is semantic-dominant. "
    "Do NOT ask a separate yes/no confirmation question."
)
_MAPPING_CLARITY_FEEDBACK_NOTE = (
    "Requirements note: do NOT ask whether field-mapping guidance is clear (including isAdult typing). "
    "Assume users will raise concerns if needed."
)
_DEFAULT_SPECIFIC_USE_CASES_NOTE = (
    "Requirements note: default specific use cases are enabled. "
    "Assume yes for range/filter retrieval, aggregation analytics, and pattern/trend analysis. "
    "Do NOT ask a separate specific-use-cases checklist."
)
_DEFAULT_QUERY_SUPPORT_SCOPE_NOTE = (
    "Requirements note: capability applicability should be data-backed. "
    "If the sample/schema supports a query/filter/aggregation capability, keep it; otherwise skip it."
)
_DEFAULT_DASHBOARD_REQUIREMENT_NOTE = (
    "Requirements note: dashboard/visualization requirement = none by default. "
    "Assume no specific dashboard or visualization needs unless the user explicitly asks."
)
_DEFAULT_REALTIME_REQUIREMENT_NOTE = (
    "Requirements note: real-time ingestion/search requirement = enabled by default. "
    "Assume near real-time ingestion and search freshness are required unless the user explicitly requests batch-only."
)
_DEFAULT_CUSTOM_REQUIREMENTS_NOTE = (
    "Requirements note: additional custom requirements = none by default unless user explicitly provides them. "
    "Do NOT ask an open-ended additional-requirements checklist."
)
_BUDGET_PREFERENCE_NOTE_PREFIX = "Requirements note: budget preference ="
_PERFORMANCE_PREFERENCE_NOTE_PREFIX = "Requirements note: performance priority ="
_SEMANTIC_QUERY_PATTERN_PREFERENCE_NOTE_PREFIX = "Requirements note: semantic query-pattern preference ="
_MODEL_DEPLOYMENT_PREFERENCE_NOTE_PREFIX = "Requirements note: production model deployment preference ="
_HYBRID_WEIGHT_PROFILE_PREFIX = "Hybrid Weight Profile:"
_BUDGET_OPTION_FLEXIBLE = "flexible"
_BUDGET_OPTION_COST_SENSITIVE = "cost-sensitive"
_PERFORMANCE_OPTION_SPEED = "speed-first"
_PERFORMANCE_OPTION_BALANCED = "balanced"
_PERFORMANCE_OPTION_ACCURACY = "accuracy-first"
_MODEL_DEPLOYMENT_OPTION_OPENSEARCH_NODE = "opensearch-node"
_MODEL_DEPLOYMENT_OPTION_SAGEMAKER_ENDPOINT = "sagemaker-endpoint"
_MODEL_DEPLOYMENT_OPTION_EXTERNAL_EMBEDDING_API = "external-embedding-api"
_HYBRID_WEIGHT_OPTION_SEMANTIC = "semantic-heavy"
_HYBRID_WEIGHT_OPTION_BALANCED = "balanced"
_HYBRID_WEIGHT_OPTION_LEXICAL = "lexical-heavy"
_QUERY_PATTERN_OPTION_MOSTLY_EXACT = "mostly-exact"
_QUERY_PATTERN_OPTION_BALANCED = "balanced"
_QUERY_PATTERN_OPTION_MOSTLY_SEMANTIC = "mostly-semantic"
_PREFIX_WILDCARD_OPTION_ENABLED = "enabled"
_PREFIX_WILDCARD_OPTION_DISABLED = "disabled"
_DEFAULT_QUERY_FEATURES_NOTE_PREFIX = "Requirements note: default query features are required:"
_PREFIX_WILDCARD_REQUIREMENT_NOTE_PREFIX = "Requirements note: prefix/wildcard matching preference ="
_TEXT_SEARCH_USE_CASE_NOTE_PREFIX = "Requirements note: inferred search use case ="
_MODEL_DEPLOYMENT_SCOPE_NOTE_PREFIX = "Requirements note: production model deployment options include OpenSearch node, SageMaker endpoint, and external embedding APIs."
_PERFORMANCE_PRIORITY_SCOPE_NOTE_PREFIX = "Requirements note: performance clarification should use one question only:"
_SAMPLE_FINAL_TRUTH_NOTE_PREFIX = "Requirements note: treat sample data/schema as final truth for planning."
_SEMANTIC_EXPANSION_EXPLANATION_NOTE_PREFIX = "Requirements note: semantic expansion explanation preference ="
_NATURAL_LANGUAGE_CONCEPT_SEARCH_NOTE_PREFIX = "Requirements note: natural-language/concept-based retrieval should be treated as primary"
_MAPPING_CLARITY_FEEDBACK_NOTE_PREFIX = "Requirements note: do NOT ask whether field-mapping guidance is clear"
_DEFAULT_SPECIFIC_USE_CASES_NOTE_PREFIX = "Requirements note: default specific use cases are enabled."
_DEFAULT_QUERY_SUPPORT_SCOPE_NOTE_PREFIX = "Requirements note: capability applicability should be data-backed."
_DEFAULT_DASHBOARD_REQUIREMENT_NOTE_PREFIX = "Requirements note: dashboard/visualization requirement = none by default."
_DEFAULT_REALTIME_REQUIREMENT_NOTE_PREFIX = "Requirements note: real-time ingestion/search requirement = enabled by default."
_DEFAULT_CUSTOM_REQUIREMENTS_NOTE_PREFIX = "Requirements note: additional custom requirements = none by default"


def _infer_budget_preference_from_text(text: str) -> str | None:
    """Infer budget preference from free-form user text."""
    lowered = (text or "").lower()
    if not lowered:
        return None

    no_budget_signals = (
        "no budget",
        "no cost constraint",
        "budget is flexible",
        "flexible with costs",
        "cost is not a concern",
        "no budget constraints",
        "no budget limitation",
    )
    if any(signal in lowered for signal in no_budget_signals):
        return _BUDGET_OPTION_FLEXIBLE

    cost_sensitive_signals = (
        "budget constraint",
        "cost-effective",
        "cost sensitive",
        "optimize cost",
        "low cost",
        "tight budget",
        "budget limited",
    )
    if any(signal in lowered for signal in cost_sensitive_signals):
        return _BUDGET_OPTION_COST_SENSITIVE
    return None


def _infer_performance_priority_from_text(text: str) -> str | None:
    """Infer performance-vs-accuracy priority from free-form user text."""
    lowered = (text or "").lower()
    if not lowered:
        return None

    if "speed-first" in lowered:
        return _PERFORMANCE_OPTION_SPEED
    if "accuracy-first" in lowered:
        return _PERFORMANCE_OPTION_ACCURACY
    if "balanced" in lowered:
        return _PERFORMANCE_OPTION_BALANCED

    speed_signals = ("ultra-fast", "fast", "speed", "low latency", "latency first")
    accuracy_signals = ("accuracy", "relevance", "precision", "quality first")

    has_speed = any(signal in lowered for signal in speed_signals)
    has_accuracy = any(signal in lowered for signal in accuracy_signals)
    if has_speed and not has_accuracy:
        return _PERFORMANCE_OPTION_SPEED
    if has_accuracy and not has_speed:
        return _PERFORMANCE_OPTION_ACCURACY
    if has_speed and has_accuracy:
        return _PERFORMANCE_OPTION_BALANCED
    return None


def _infer_prefix_wildcard_preference_from_text(text: str) -> bool | None:
    """Infer explicit user intent for prefix/wildcard behavior."""
    lowered = (text or "").lower()
    if not lowered:
        return None

    negative_patterns = (
        r"\b(?:do not|don't|dont|not)\s+(?:need|require|want|use|include|enable|support)\b[^.\n]{0,40}\b(?:prefix|wildcard)\b",
        r"\b(?:no|without)\s+(?:prefix|wildcard)\b",
        r"\bexact(?:\s+match(?:es)?)?\s+only\b",
    )
    if any(re.search(pattern, lowered) for pattern in negative_patterns):
        return False

    positive_patterns = (
        r"\b(?:need|require|want|use|include|enable|support)\b[^.\n]{0,40}\b(?:prefix|wildcard)\b",
        r"\b(?:prefix|wildcard)\b[^.\n]{0,40}\b(?:needed|required|requested|enabled|supported)\b",
    )
    if any(re.search(pattern, lowered) for pattern in positive_patterns):
        return True
    return None


def _build_budget_preference_note(preference: str) -> str:
    """Build canonical budget preference requirement note."""
    if preference == _BUDGET_OPTION_COST_SENSITIVE:
        return (
            "Requirements note: budget preference = cost-sensitive. "
            "Prioritize cost-effective architecture choices."
        )
    return (
        "Requirements note: budget preference = flexible (no strict budget constraints). "
        "Do not optimize primarily for cost."
    )


def _build_performance_preference_note(preference: str) -> str:
    """Build canonical performance priority requirement note."""
    if preference == _PERFORMANCE_OPTION_SPEED:
        return (
            "Requirements note: performance priority = speed-first. "
            "Prefer lower-latency retrieval settings when trade-offs are required."
        )
    if preference == _PERFORMANCE_OPTION_ACCURACY:
        return (
            "Requirements note: performance priority = accuracy-first. "
            "Prefer higher relevance/quality settings when trade-offs are required."
        )
    return (
        "Requirements note: performance priority = balanced. "
        "Use balanced speed-vs-relevance defaults."
    )


def _build_semantic_query_pattern_preference_note(profile: str) -> str:
    """Build canonical semantic query-pattern requirement note from hybrid profile."""
    normalized = str(profile or "").strip().lower()
    if normalized == _HYBRID_WEIGHT_OPTION_LEXICAL:
        return (
            "Requirements note: semantic query-pattern preference = mostly exact / navigational. "
            "This value is already collected; do NOT ask this query-pattern question again."
        )
    if normalized == _HYBRID_WEIGHT_OPTION_SEMANTIC:
        return (
            "Requirements note: semantic query-pattern preference = mostly natural language / semantic. "
            "This value is already collected; do NOT ask this query-pattern question again."
        )
    return (
        "Requirements note: semantic query-pattern preference = balanced. "
        "This value is already collected; do NOT ask this query-pattern question again."
    )


def _build_model_deployment_preference_note(preference: str) -> str:
    """Build canonical production semantic-model deployment preference note."""
    normalized = str(preference or "").strip().lower()
    if normalized == _MODEL_DEPLOYMENT_OPTION_SAGEMAKER_ENDPOINT:
        return (
            "Requirements note: production model deployment preference = SageMaker endpoint "
            "(separate compute, more flexible scaling)."
        )
    if normalized == _MODEL_DEPLOYMENT_OPTION_EXTERNAL_EMBEDDING_API:
        return (
            "Requirements note: production model deployment preference = external embedding API "
            "(managed service, e.g., OpenAI/Cohere)."
        )
    return (
        "Requirements note: production model deployment preference = OpenSearch node "
        "(co-located with search cluster, simplest ops)."
    )


def _build_prefix_wildcard_requirement_note(enabled: bool) -> str:
    """Build canonical requirement note for prefix/wildcard preference."""
    if enabled:
        return (
            "Requirements note: prefix/wildcard matching preference = enabled (user-requested). "
            "Include lexical BM25 capability to support prefix/wildcard behavior."
        )
    return (
        "Requirements note: prefix/wildcard matching preference = disabled (default unless user-requested). "
        "Do NOT force lexical BM25 solely for prefix/wildcard support."
    )


def _extract_text_field_preview(
    candidate_fields: list[str] | None = None,
    max_fields: int = 3,
) -> str:
    """Extract a stable, deduplicated field preview for user-facing prompts."""
    cleaned_fields: list[str] = []
    seen: set[str] = set()
    for raw_field in candidate_fields or []:
        field = str(raw_field or "").strip()
        if not field:
            continue
        lowered = field.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        cleaned_fields.append(field)
        if len(cleaned_fields) >= max(1, max_fields):
            break
    return ", ".join(cleaned_fields)


def _build_semantic_query_pattern_prompt(
    candidate_fields: list[str] | None = None,
    max_fields: int = 3,
) -> str:
    """Build semantic query-pattern prompt with optional text-field context."""
    preview = _extract_text_field_preview(candidate_fields, max_fields=max_fields)
    question = "Query pattern: How do you expect users to search?"
    if preview:
        return f"From your sample data, fields like {preview} look text-heavy.\n\n{question}"
    return question


def _build_model_deployment_preference_prompt(
    candidate_fields: list[str] | None = None,
    max_fields: int = 3,
) -> str:
    """Build a user-friendly embedding-hosting prompt with optional field context."""
    preview = _extract_text_field_preview(candidate_fields, max_fields=max_fields)

    shared_suffix = (
        "To enable semantic search, we use an embedding model "
        "(an AI model that turns text into meaning vectors) so search can match intent, "
        "not just exact words. For production, where should this embedding model run?"
    )
    if preview:
        return shared_suffix
    return f"Your sample data includes text content suitable for semantic search. {shared_suffix}"


def _read_model_deployment_preference_choice(
    candidate_fields: list[str] | None = None,
) -> str:
    """Read and normalize production semantic-model deployment preference."""
    selected = read_single_choice_input(
        title="Embedding Model Hosting",
        prompt=_build_model_deployment_preference_prompt(candidate_fields),
        options=[
            (
                _MODEL_DEPLOYMENT_OPTION_OPENSEARCH_NODE,
                "OpenSearch node (co-located with search cluster, simplest ops)",
            ),
            (
                _MODEL_DEPLOYMENT_OPTION_SAGEMAKER_ENDPOINT,
                "SageMaker endpoint (separate compute, more flexible scaling)",
            ),
            (
                _MODEL_DEPLOYMENT_OPTION_EXTERNAL_EMBEDDING_API,
                "External embedding API (e.g., OpenAI, Cohere - managed service)",
            ),
        ],
        default_value=_MODEL_DEPLOYMENT_OPTION_OPENSEARCH_NODE,
    )
    normalized = str(selected or _MODEL_DEPLOYMENT_OPTION_OPENSEARCH_NODE).strip().lower()
    if normalized not in {
        _MODEL_DEPLOYMENT_OPTION_OPENSEARCH_NODE,
        _MODEL_DEPLOYMENT_OPTION_SAGEMAKER_ENDPOINT,
        _MODEL_DEPLOYMENT_OPTION_EXTERNAL_EMBEDDING_API,
    }:
        return _MODEL_DEPLOYMENT_OPTION_OPENSEARCH_NODE
    return normalized



def _read_prefix_wildcard_preference_choice(
    candidate_fields: list[str] | None = None,
) -> bool:
    """Read whether prefix/wildcard capability should be enabled."""
    preview = _extract_text_field_preview(candidate_fields)
    prompt = (
        "Do you need prefix/wildcard matching support? "
        "Enabling this implies lexical BM25 support."
    )
    if preview:
        prompt = (
            f"From your sample data, fields like {preview} look text-heavy. "
            "Do you need prefix/wildcard matching support? Enabling this implies lexical BM25 support."
        )
    selected = read_single_choice_input(
        title="Prefix/Wildcard Matching",
        prompt=prompt,
        options=[
            (
                _PREFIX_WILDCARD_OPTION_ENABLED,
                "Yes - include prefix/wildcard matching (implies lexical BM25)",
            ),
            (
                _PREFIX_WILDCARD_OPTION_DISABLED,
                "No - semantic/structured search only for this capability",
            ),
        ],
        default_value=_PREFIX_WILDCARD_OPTION_DISABLED,
    )
    return str(selected or _PREFIX_WILDCARD_OPTION_DISABLED).strip().lower() == _PREFIX_WILDCARD_OPTION_ENABLED



def _is_semantic_dominant_query_pattern(profile: str | None) -> bool:
    """Return True when query-pattern preference indicates semantic-dominant traffic."""
    normalized = str(profile or "").strip().lower()
    return normalized in {
        _HYBRID_WEIGHT_OPTION_SEMANTIC,
        _QUERY_PATTERN_OPTION_MOSTLY_SEMANTIC,
    }


def _requires_model_deployment_preference(profile: str | None) -> bool:
    """Return True when deployment preference should be collected for query pattern."""
    normalized = str(profile or "").strip().lower()
    return normalized in {
        _HYBRID_WEIGHT_OPTION_BALANCED,
        _HYBRID_WEIGHT_OPTION_SEMANTIC,
        _QUERY_PATTERN_OPTION_BALANCED,
        _QUERY_PATTERN_OPTION_MOSTLY_SEMANTIC,
    }


def _build_hybrid_weight_profile_note(profile: str) -> str:
    """Build canonical hybrid-weight profile line for planner/worker context."""
    normalized = str(profile or "").strip().lower()
    if normalized not in {
        _HYBRID_WEIGHT_OPTION_SEMANTIC,
        _HYBRID_WEIGHT_OPTION_BALANCED,
        _HYBRID_WEIGHT_OPTION_LEXICAL,
    }:
        normalized = _HYBRID_WEIGHT_OPTION_BALANCED
    return f"{_HYBRID_WEIGHT_PROFILE_PREFIX} {normalized}"


def _read_hybrid_weight_profile_choice(
    candidate_fields: list[str] | None = None,
) -> str:
    """Read query-pattern preference that drives retrieval strategy decisions."""
    selected_profile = read_single_choice_input(
        title="Semantic Search Query Pattern",
        prompt=_build_semantic_query_pattern_prompt(candidate_fields),
        options=[
            (
                _QUERY_PATTERN_OPTION_MOSTLY_EXACT,
                'Mostly exact keywords (like "Carmencita 1894")',
            ),
            (
                _QUERY_PATTERN_OPTION_MOSTLY_SEMANTIC,
                'Semantic/natural language (like "early silent films about dancers")',
            ),
            (
                _QUERY_PATTERN_OPTION_BALANCED,
                "Balanced mix of both (default)",
            ),
        ],
        default_value=_QUERY_PATTERN_OPTION_BALANCED,
    )
    normalized = str(selected_profile or _QUERY_PATTERN_OPTION_BALANCED).strip().lower()
    if normalized == _QUERY_PATTERN_OPTION_MOSTLY_EXACT:
        return _HYBRID_WEIGHT_OPTION_LEXICAL
    if normalized == _QUERY_PATTERN_OPTION_MOSTLY_SEMANTIC:
        return _HYBRID_WEIGHT_OPTION_SEMANTIC
    return _HYBRID_WEIGHT_OPTION_BALANCED


def _extract_localhost_index_options_from_error(error_text: str) -> list[str]:
    """Extract index names from localhost-index error text that includes options."""
    if not error_text:
        return []
    options: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(
        r"(?m)^\s*-\s*([A-Za-z0-9._-]+)\s+\(docs=",
        error_text,
    ):
        index_name = match.group(1).strip()
        lowered = index_name.lower()
        if not index_name or lowered in seen:
            continue
        seen.add(lowered)
        options.append(index_name)
    return options


def _resolve_pending_localhost_index_selection(
    user_input: str,
    pending_options: list[str],
) -> str | None:
    """Resolve a user reply against pending localhost index options."""
    if not pending_options:
        return None

    raw = (user_input or "").strip()
    if not raw:
        return None

    number_match = re.fullmatch(r"(\d+)(?:[.)]+)?", raw)
    if number_match:
        index = int(number_match.group(1))
        if 1 <= index <= len(pending_options):
            return pending_options[index - 1]

    lowered_raw = raw.lower().strip("'\"")
    for option in pending_options:
        if lowered_raw == option.lower():
            return option

    for option in sorted(pending_options, key=len, reverse=True):
        if re.search(rf"\b{re.escape(option)}\b", raw, flags=re.IGNORECASE):
            return option
    return None


def _normalize_localhost_auth_mode(mode: str | None) -> str:
    normalized = str(mode or "").strip().lower()
    if normalized in _VALID_LOCALHOST_AUTH_MODES:
        return normalized
    return _LOCALHOST_AUTH_MODE_DEFAULT


def _set_localhost_auth_state(
    state: SessionState,
    mode: str,
    username: str = "",
    password: str = "",
) -> None:
    normalized_mode = _normalize_localhost_auth_mode(mode)
    state.localhost_auth_mode = normalized_mode
    if normalized_mode == _LOCALHOST_AUTH_MODE_CUSTOM:
        state.localhost_auth_username = str(username or "").strip() or None
        state.localhost_auth_password = str(password or "").strip() or None
    else:
        state.localhost_auth_username = None
        state.localhost_auth_password = None


def _resolve_localhost_auth_from_state(state: SessionState) -> tuple[str, str, str]:
    mode = _normalize_localhost_auth_mode(state.localhost_auth_mode)
    if mode == _LOCALHOST_AUTH_MODE_CUSTOM:
        username = str(state.localhost_auth_username or "").strip()
        password = str(state.localhost_auth_password or "").strip()
        return mode, username, password
    return mode, "", ""


@contextmanager
def _temporary_localhost_auth_env(
    mode: str,
    username: str = "",
    password: str = "",
):
    previous_mode = os.environ.get(_OPENSEARCH_AUTH_MODE_ENV)
    previous_user = os.environ.get(_OPENSEARCH_USER_ENV)
    previous_password = os.environ.get(_OPENSEARCH_PASSWORD_ENV)
    try:
        normalized_mode = _normalize_localhost_auth_mode(mode)
        os.environ[_OPENSEARCH_AUTH_MODE_ENV] = normalized_mode
        if normalized_mode == _LOCALHOST_AUTH_MODE_CUSTOM:
            os.environ[_OPENSEARCH_USER_ENV] = str(username or "")
            os.environ[_OPENSEARCH_PASSWORD_ENV] = str(password or "")
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


def _looks_like_no_credentials_phrase(text: str) -> bool:
    lowered = str(text or "").lower()
    if not lowered:
        return False
    patterns = (
        r"\b(?:no|without)\s+(?:credentials|username|password)\b",
        r"\b(?:do not|don't|dont)\s+have\b[^.\n]{0,50}\b(?:credentials|username|password)\b",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


def _extract_localhost_auth_override_from_text(
    text: str,
) -> tuple[str | None, str, str, str | None]:
    raw_text = str(text or "")
    if not raw_text.strip():
        return None, "", "", None

    kv_matches = re.finditer(
        r"""(?ix)\b(username|user|password|pass|auth|no_auth)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s,;]+))""",
        raw_text,
    )
    kv: dict[str, str] = {}
    for match in kv_matches:
        key = str(match.group(1) or "").strip().lower()
        value = str(match.group(2) or match.group(3) or match.group(4) or "").strip()
        if key and value:
            kv[key] = value

    username = str(kv.get("username") or kv.get("user") or "").strip()
    password = str(kv.get("password") or kv.get("pass") or "").strip()

    if username or password:
        if not username or not password:
            return (
                None,
                "",
                "",
                "Error: custom localhost auth requires both username and password (for example: username=alice password=secret).",
            )
        return _LOCALHOST_AUTH_MODE_CUSTOM, username, password, None

    explicit_auth_mode = str(kv.get("auth", "")).strip().lower()
    if explicit_auth_mode == _LOCALHOST_AUTH_MODE_NONE:
        return _LOCALHOST_AUTH_MODE_NONE, "", "", None

    explicit_no_auth = str(kv.get("no_auth", "")).strip().lower()
    if explicit_no_auth in {"1", "true", "yes", "y", "on"}:
        return _LOCALHOST_AUTH_MODE_NONE, "", "", None

    if _looks_like_no_credentials_phrase(raw_text):
        return _LOCALHOST_AUTH_MODE_NONE, "", "", None

    return None, "", "", None


def _redact_localhost_auth_secrets(text: str) -> str:
    redacted = str(text or "")
    redacted = re.sub(
        r"""(?ix)\b(password|pass)\s*=\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)""",
        lambda match: f"{match.group(1)}=***",
        redacted,
    )
    return redacted


def _looks_like_pasted_sample_content(user_input: str) -> bool:
    """Detect pasted JSON sample content for option 4 style input."""
    raw = str(user_input or "").strip()
    if not raw:
        return False

    if raw.startswith("{"):
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            return True

    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = None
        if isinstance(parsed, list) and parsed:
            return all(isinstance(item, dict) for item in parsed[:3])

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(lines) <= 1:
        return False
    parsed_line_count = 0
    for line in lines:
        if not line.startswith("{"):
            return False
        try:
            parsed_line = json.loads(line)
        except Exception:
            return False
        if not isinstance(parsed_line, dict):
            return False
        parsed_line_count += 1
    return parsed_line_count > 0


def _looks_like_semantic_text_value(raw_value: object) -> bool:
    """Return True when a value appears suitable for full-text/semantic retrieval."""
    if not isinstance(raw_value, str):
        return False

    value = raw_value.strip()
    if len(value) < 3:
        return False

    lowered = value.lower()
    if lowered in {"true", "false", "null", "none", "nan", "n/a", "na"}:
        return False
    if _NUMERIC_STRING_PATTERN.fullmatch(value):
        return False
    if _DATEISH_STRING_PATTERN.fullmatch(value):
        return False

    alpha_count = sum(1 for ch in value if ch.isalpha())
    if alpha_count < 3:
        return False
    if not re.search(r"[A-Za-z]{2,}", value):
        return False
    return True


def _infer_semantic_text_fields(sample_doc: object, max_fields: int = 6) -> list[str]:
    """Infer candidate non-keyword text fields from a sample document."""
    if not isinstance(sample_doc, dict):
        return []

    candidates: list[str] = []
    for raw_field, raw_value in sample_doc.items():
        field = str(raw_field or "").strip()
        if not field:
            continue

        lowered_field = field.lower()
        field_tokens = [token for token in re.split(r"[^a-z0-9]+", lowered_field) if token]
        if any(token in _EXCLUDED_TEXT_FIELD_NAME_TOKENS for token in field_tokens):
            continue

        values_to_check: list[object]
        if isinstance(raw_value, list):
            values_to_check = list(raw_value[:3])
        else:
            values_to_check = [raw_value]

        if any(_looks_like_semantic_text_value(item) for item in values_to_check):
            candidates.append(field)
            if len(candidates) >= max_fields:
                break

    return candidates


def _build_text_search_use_case_note(
    text_search_required: bool | None,
    candidate_fields: list[str] | None = None,
) -> str:
    """Build a deterministic requirement note for text-search inference."""
    fields = [str(field).strip() for field in (candidate_fields or []) if str(field).strip()]
    if text_search_required:
        field_suffix = f" Candidate fields: {', '.join(fields)}." if fields else ""
        return (
            "Requirements note: inferred search use case = yes, text-based search is needed, "
            "because sample/schema has non-keyword text fields suitable for semantic/full-text retrieval."
            f"{field_suffix} Do NOT ask whether text-based search is needed."
        )
    if text_search_required is False:
        return (
            "Requirements note: inferred search use case = primarily numeric/filter-based "
            "(no strong non-keyword text-field signal in the provided sample/schema). "
            "Do NOT ask a query-pattern checklist or future text-field expectation questions."
        )
    return (
        "Requirements note: infer search use case from sample/schema directly. "
        "Do NOT ask a query-pattern checklist."
    )


def _capture_sample_from_result(state: SessionState, result: str) -> bool:
    """Try to parse a submit result and update local state. Returns True on success."""
    try:
        parsed = json.loads(result)
        if isinstance(parsed, dict) and "sample_doc" in parsed:
            state.sample_doc_json = json.dumps(parsed["sample_doc"], ensure_ascii=False)
            source_local_file = str(parsed.get("source_local_file", "")).strip()
            source_index_name = str(parsed.get("source_index_name", "")).strip()
            state.source_local_file = source_local_file or None
            state.source_index_name = source_index_name or None
            return True
    except (json.JSONDecodeError, TypeError):
        pass
    return False


def _clear_orchestrator_sample_state(state: SessionState) -> None:
    state.sample_doc_json = None
    state.source_local_file = None
    state.source_index_name = None
    state.source_index_doc_count = None
    state.inferred_text_search_required = None
    state.inferred_semantic_text_fields = []
    state.pending_localhost_index_options = []


def _reset_session_state(state: SessionState) -> None:
    _clear_orchestrator_sample_state(state)
    state.budget_preference = None
    state.performance_priority = None
    state.model_deployment_preference = None
    state.prefix_wildcard_enabled = None
    state.hybrid_weight_profile = None
    _set_localhost_auth_state(state, _LOCALHOST_AUTH_MODE_DEFAULT)


def _orchestrator_submit_sample_doc(state: SessionState, doc: str) -> str:
    """Parse a sample document provided by the user.

    Args:
        doc: User-provided sample document, preferably JSON.

    Returns:
        str: JSON string with ``sample_doc`` on success, or an error message.
    """
    result = submit_sample_doc(doc)
    _capture_sample_from_result(state, result)
    return result


def _extract_sample_doc_from_state(sample_doc_json: str | None) -> dict | None:
    raw = str(sample_doc_json or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(parsed, dict):
        sample_doc = parsed.get("sample_doc")
        if isinstance(sample_doc, dict):
            return sample_doc
        return parsed
    return None


def _augment_worker_context_with_source(state: SessionState, context: str) -> str:
    raw_context = str(context or "")
    stripped = raw_context.strip()

    resume_mode = False
    execution_context = stripped
    if execution_context.startswith(_RESUME_WORKER_MARKER):
        resume_mode = True
        execution_context = execution_context[len(_RESUME_WORKER_MARKER) :].strip()

    if not execution_context:
        execution_context = stripped

    missing_lines: list[str] = []
    has_source_local_file = bool(worker_resolve_source_local_file(execution_context))
    has_localhost_source, _ = worker_resolve_localhost_source_protection(execution_context)
    if not has_source_local_file and not has_localhost_source:
        if state.source_local_file:
            missing_lines.append(f"Source: {state.source_local_file}")
        elif state.source_index_name:
            missing_lines.append(f"Source: localhost OpenSearch index '{state.source_index_name}'")

    has_sample_doc = bool(worker_extract_sample_doc_json(execution_context))
    if not has_sample_doc:
        sample_doc = _extract_sample_doc_from_state(state.sample_doc_json)
        if isinstance(sample_doc, dict) and sample_doc:
            sample_doc_line = json.dumps(sample_doc, ensure_ascii=False)
            missing_lines.append(f"Sample document: {sample_doc_line}")

    if (
        not missing_lines
        or _SYSTEM_SOURCE_CONTEXT_HEADER.lower() in execution_context.lower()
    ):
        return raw_context

    augmented = execution_context.rstrip()
    if augmented:
        augmented += "\n\n"
    augmented += _SYSTEM_SOURCE_CONTEXT_HEADER + "\n" + "\n".join(missing_lines)
    if resume_mode:
        return f"{_RESUME_WORKER_MARKER}\n{augmented}"
    return augmented


def _run_worker_agent_with_state(state: SessionState, context: str) -> str:
    worker_context = _augment_worker_context_with_source(state, context)
    if not state.source_index_name:
        return worker_agent_impl(worker_context)

    mode, username, password = _resolve_localhost_auth_from_state(state)
    with _temporary_localhost_auth_env(mode=mode, username=username, password=password):
        return worker_agent_impl(worker_context)


# -------------------------------------------------------------------------
# Shared Context Builders (used by CLI + MCP engine)
# -------------------------------------------------------------------------

def _iter_context_note_entries(state: SessionState) -> list[tuple[str, str]]:
    """Return ordered `(prefix, note)` entries for requirements context."""
    entries: list[tuple[str, str]] = [
        (_DEFAULT_QUERY_FEATURES_NOTE_PREFIX, _DEFAULT_QUERY_FEATURES_NOTE),
        (
            _TEXT_SEARCH_USE_CASE_NOTE_PREFIX,
            _build_text_search_use_case_note(
                state.inferred_text_search_required,
                state.inferred_semantic_text_fields,
            ),
        ),
        (_MODEL_DEPLOYMENT_SCOPE_NOTE_PREFIX, _MODEL_DEPLOYMENT_SCOPE_NOTE),
        (_PERFORMANCE_PRIORITY_SCOPE_NOTE_PREFIX, _PERFORMANCE_PRIORITY_SCOPE_NOTE),
        (_SAMPLE_FINAL_TRUTH_NOTE_PREFIX, _SAMPLE_FINAL_TRUTH_NOTE),
        (_SEMANTIC_EXPANSION_EXPLANATION_NOTE_PREFIX, _SEMANTIC_EXPANSION_EXPLANATION_NOTE),
        (_MAPPING_CLARITY_FEEDBACK_NOTE_PREFIX, _MAPPING_CLARITY_FEEDBACK_NOTE),
        (_DEFAULT_SPECIFIC_USE_CASES_NOTE_PREFIX, _DEFAULT_SPECIFIC_USE_CASES_NOTE),
        (_DEFAULT_QUERY_SUPPORT_SCOPE_NOTE_PREFIX, _DEFAULT_QUERY_SUPPORT_SCOPE_NOTE),
        (_DEFAULT_DASHBOARD_REQUIREMENT_NOTE_PREFIX, _DEFAULT_DASHBOARD_REQUIREMENT_NOTE),
        (_DEFAULT_REALTIME_REQUIREMENT_NOTE_PREFIX, _DEFAULT_REALTIME_REQUIREMENT_NOTE),
        (_DEFAULT_CUSTOM_REQUIREMENTS_NOTE_PREFIX, _DEFAULT_CUSTOM_REQUIREMENTS_NOTE),
    ]

    if (
        state.inferred_text_search_required
        and _is_semantic_dominant_query_pattern(state.hybrid_weight_profile)
    ):
        entries.append(
            (_NATURAL_LANGUAGE_CONCEPT_SEARCH_NOTE_PREFIX, _NATURAL_LANGUAGE_CONCEPT_SEARCH_NOTE)
        )

    if state.budget_preference:
        entries.append(
            (
                _BUDGET_PREFERENCE_NOTE_PREFIX,
                _build_budget_preference_note(state.budget_preference),
            )
        )
    if state.performance_priority:
        entries.append(
            (
                _PERFORMANCE_PREFERENCE_NOTE_PREFIX,
                _build_performance_preference_note(state.performance_priority),
            )
        )
    if state.inferred_text_search_required and state.hybrid_weight_profile:
        entries.append(
            (
                _SEMANTIC_QUERY_PATTERN_PREFERENCE_NOTE_PREFIX,
                _build_semantic_query_pattern_preference_note(state.hybrid_weight_profile),
            )
        )
        entries.append(
            (
                _HYBRID_WEIGHT_PROFILE_PREFIX,
                _build_hybrid_weight_profile_note(state.hybrid_weight_profile),
            )
        )
    if state.prefix_wildcard_enabled is not None:
        entries.append(
            (
                _PREFIX_WILDCARD_REQUIREMENT_NOTE_PREFIX,
                _build_prefix_wildcard_requirement_note(state.prefix_wildcard_enabled),
            )
        )
    if state.inferred_text_search_required and state.model_deployment_preference:
        entries.append(
            (
                _MODEL_DEPLOYMENT_PREFERENCE_NOTE_PREFIX,
                _build_model_deployment_preference_note(state.model_deployment_preference),
            )
        )

    return entries


def _build_localhost_execution_policy_note(state: SessionState) -> str | None:
    if not state.source_index_name:
        return None
    return (
        "Execution policy: source is localhost OpenSearch index "
        f"'{state.source_index_name}' (system-enforced, not user-stated); "
        "if target index already exists during setup, "
        "do NOT recreate it (replace_if_exists=false). "
        "Use a different target index name."
    )


def _build_localhost_doc_count_note(state: SessionState) -> str | None:
    if not isinstance(state.source_index_doc_count, int):
        return None
    return (
        "Requirements note: exact current document count already measured "
        f"from OpenSearch count API: {state.source_index_doc_count:,}. "
        "Do NOT ask current-count or growth-projection questions. "
        "Assume this is representative sample data and ingestion will continue."
    )


def _build_context_notes(state: SessionState) -> str:
    """Build the full requirement notes context block from session state."""
    return "\n".join(note for _, note in _iter_context_note_entries(state))


def _build_planning_context(state: SessionState, additional_context: str = "") -> str:
    """Build the full context string for the planning agent."""
    parts: list[str] = []

    if state.sample_doc_json:
        parts.append(f"Sample document: {state.sample_doc_json}")

    if state.source_index_name:
        policy_note = _build_localhost_execution_policy_note(state)
        if policy_note:
            parts.append(policy_note)
        doc_count_note = _build_localhost_doc_count_note(state)
        if doc_count_note:
            parts.append(doc_count_note)

    parts.append(_build_context_notes(state))

    if additional_context:
        parts.append(additional_context)

    return "\n\n".join(parts)


def create_transport_agnostic_engine(
    state: SessionState | None = None,
) -> OrchestratorEngine:
    """Create shared orchestration engine used by CLI and MCP adapters."""
    effective_state = state or SessionState()

    def _load_localhost_index_sample_with_auth(
        source_value: str = "",
        localhost_auth_mode: str = _LOCALHOST_AUTH_MODE_DEFAULT,
        localhost_auth_username: str = "",
        localhost_auth_password: str = "",
    ) -> str:
        with _temporary_localhost_auth_env(
            mode=localhost_auth_mode,
            username=localhost_auth_username,
            password=localhost_auth_password,
        ):
            return submit_sample_doc_from_localhost_index(source_value)

    return OrchestratorEngine(
        state=effective_state,
        clear_sample_state=_clear_orchestrator_sample_state,
        reset_state=_reset_session_state,
        capture_sample_from_result=_capture_sample_from_result,
        infer_semantic_text_fields=_infer_semantic_text_fields,
        is_semantic_dominant_query_pattern=_is_semantic_dominant_query_pattern,
        build_context_notes=_build_context_notes,
        build_planning_context=_build_planning_context,
        run_worker_with_state=_run_worker_agent_with_state,
        get_last_worker_run_state=get_last_worker_run_state,
        planning_session_factory=PlanningSession,
        load_builtin_sample=lambda: submit_sample_doc_from_local_file(BUILTIN_IMDB_SAMPLE_PATH),
        load_local_file_sample=submit_sample_doc_from_local_file,
        load_url_sample=submit_sample_doc_from_url,
        load_localhost_index_sample=_load_localhost_index_sample_with_auth,
        load_pasted_sample=submit_sample_doc,
        budget_option_flexible=_BUDGET_OPTION_FLEXIBLE,
        budget_option_cost_sensitive=_BUDGET_OPTION_COST_SENSITIVE,
        performance_option_speed=_PERFORMANCE_OPTION_SPEED,
        performance_option_balanced=_PERFORMANCE_OPTION_BALANCED,
        performance_option_accuracy=_PERFORMANCE_OPTION_ACCURACY,
        query_pattern_option_mostly_exact=_QUERY_PATTERN_OPTION_MOSTLY_EXACT,
        query_pattern_option_balanced=_QUERY_PATTERN_OPTION_BALANCED,
        query_pattern_option_mostly_semantic=_QUERY_PATTERN_OPTION_MOSTLY_SEMANTIC,
        model_deployment_option_opensearch_node=_MODEL_DEPLOYMENT_OPTION_OPENSEARCH_NODE,
        model_deployment_option_sagemaker_endpoint=_MODEL_DEPLOYMENT_OPTION_SAGEMAKER_ENDPOINT,
        model_deployment_option_external_embedding_api=_MODEL_DEPLOYMENT_OPTION_EXTERNAL_EMBEDDING_API,
        hybrid_weight_option_semantic=_HYBRID_WEIGHT_OPTION_SEMANTIC,
        hybrid_weight_option_balanced=_HYBRID_WEIGHT_OPTION_BALANCED,
        hybrid_weight_option_lexical=_HYBRID_WEIGHT_OPTION_LEXICAL,
        resume_marker=_RESUME_WORKER_MARKER,
    )


# -------------------------------------------------------------------------
# Agent Factory
# -------------------------------------------------------------------------

def _create_orchestrator_agent(state: SessionState) -> Agent:
    """Build a fresh orchestrator agent (no conversation history)."""
    model = BedrockModel(
        model_id=MODEL_ID,
        max_tokens=4000,
        additional_request_fields={
            "thinking": {
                "type": "enabled",
                "budget_tokens": 1024,
            }
        },
    )
    def submit_sample_doc(doc: str) -> str:
        return _orchestrator_submit_sample_doc(state, doc)

    def worker_agent(context: str) -> str:
        return _run_worker_agent_with_state(state, context)

    return Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[
            tool(submit_sample_doc),
            solution_planning_assistant,
            tool(worker_agent),
        ],
        callback_handler=ThinkingCallbackHandler(),
    )


# -------------------------------------------------------------------------
# State Management
# -------------------------------------------------------------------------

def _reset_all_state(
    state: SessionState,
    engine: OrchestratorEngine | None = None,
) -> tuple[Phase, Agent]:
    """Full reset: sample doc, planner session, and orchestrator agent."""
    if engine is not None:
        engine.reset()
    else:
        _reset_session_state(state)
    clear_last_worker_context()
    clear_last_worker_run_state()
    reset_planner_agent()
    agent = _create_orchestrator_agent(state)
    return Phase.COLLECT_SAMPLE, agent


# -------------------------------------------------------------------------
# Main Loop
# -------------------------------------------------------------------------

async def main():
    """Orchestrator main loop with explicit phase tracking and intent routing."""

    print(f"Initializing Orchestrator Agent with model: {MODEL_ID}...")

    state = SessionState()
    engine = create_transport_agnostic_engine(state)

    try:
        agent = _create_orchestrator_agent(state)
    except Exception as e:
        print(f"Failed to initialize orchestrator: {e}")
        return

    phase = Phase.COLLECT_SAMPLE

    print("Orchestrator ready. Type 'exit' or 'quit' to stop.")
    print("-" * 50)

    while True:
        try:
            user_input = read_multiline_input()

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit"):
                print("Goodbye!")
                break

            # Preserve the raw user message for source detection and final prompt handoff.
            raw_user_input = user_input
            safe_user_input = _redact_localhost_auth_secrets(raw_user_input)
            user_input = safe_user_input

            # ── Intent routing (hard-coded, before LLM) ────────────

            if phase in (Phase.DONE, Phase.EXEC_FAILED) and looks_like_cleanup_request(user_input):
                cleanup_result = cleanup_docs()
                print(f"Orchestrator: {cleanup_result}\n")
                continue

            # In failed-execution phase, default to resume-from-checkpoint for any
            # non-new-request/non-cancel input (e.g., "Docker is ready").
            if (
                phase == Phase.EXEC_FAILED
                and not looks_like_new_request(user_input)
                and not looks_like_cancel(user_input)
            ):
                if looks_like_worker_retry(user_input):
                    retry_reason = "explicit retry request"
                else:
                    retry_reason = "auto-resume from failed phase"

                retry_payload = await engine.retry_execution()
                if "error" in retry_payload:
                    print(f"Orchestrator: {retry_payload['error']}\n")
                    continue

                retry_result = str(retry_payload.get("execution_report", "")).strip()
                print(f"Orchestrator: ({retry_reason}) {retry_result}\n")
                check_and_clear_execution_flag()
                phase = engine.phase
                continue

            if looks_like_new_request(user_input):
                phase, agent = _reset_all_state(state, engine)
                user_input = (
                    "The user started a new request. Ignore all previous context.\n"
                    f"New request: {user_input}\n"
                    "Please start by collecting a sample document."
                )

            elif looks_like_cancel(user_input) and phase != Phase.COLLECT_SAMPLE:
                phase, agent = _reset_all_state(state, engine)
                print("Orchestrator: Request cancelled. Feel free to start a new request.\n")
                continue

            elif phase == Phase.DONE:
                # Any input after a completed flow starts a fresh cycle.
                phase, agent = _reset_all_state(state, engine)
                user_input = (
                    "The previous workflow is not active. The user has a new request.\n"
                    f"New request: {user_input}\n"
                    "Please start by collecting a sample document."
                )

            # ── Phase-specific pre-processing ───────────────────────

            if phase in (Phase.COLLECT_SAMPLE, Phase.GATHER_INFO):
                if state.sample_doc_json is None:
                    load_payload: dict | None = None
                    source_label = None
                    source_detection_input = raw_user_input
                    normalized_input = source_detection_input.strip().lower()
                    option_3_selected = normalized_input in {"3", "option 3", "choice 3"}
                    option_4_selected = normalized_input in {"4", "option 4", "choice 4"}
                    pending_selected_index = _resolve_pending_localhost_index_selection(
                        source_detection_input,
                        state.pending_localhost_index_options,
                    )
                    localhost_detection = bool(
                        pending_selected_index
                        or looks_like_localhost_index_message(source_detection_input)
                        or option_3_selected
                    )
                    if localhost_detection:
                        auth_mode, auth_username, auth_password, auth_error = (
                            _extract_localhost_auth_override_from_text(source_detection_input)
                        )
                        if auth_error:
                            load_payload = {"error": auth_error}
                            source_label = "localhost OpenSearch index"
                        elif auth_mode:
                            _set_localhost_auth_state(
                                state,
                                mode=auth_mode,
                                username=auth_username,
                                password=auth_password,
                            )

                    effective_auth_mode, effective_auth_username, effective_auth_password = (
                        _resolve_localhost_auth_from_state(state)
                    )

                    if load_payload is None and pending_selected_index:
                        load_payload = engine.load_sample(
                            source_type="localhost_index",
                            source_value=pending_selected_index,
                            localhost_auth_mode=effective_auth_mode,
                            localhost_auth_username=effective_auth_username,
                            localhost_auth_password=effective_auth_password,
                        )
                        source_label = "localhost OpenSearch index"
                        state.pending_localhost_index_options = []
                    elif load_payload is None and (
                        looks_like_localhost_index_message(source_detection_input)
                        or option_3_selected
                    ):
                        index_hint = "" if option_3_selected else source_detection_input
                        load_payload = engine.load_sample(
                            source_type="localhost_index",
                            source_value=index_hint,
                            localhost_auth_mode=effective_auth_mode,
                            localhost_auth_username=effective_auth_username,
                            localhost_auth_password=effective_auth_password,
                        )
                        source_label = "localhost OpenSearch index"
                    elif load_payload is None and _looks_like_pasted_sample_content(source_detection_input):
                        state.pending_localhost_index_options = []
                        load_payload = engine.load_sample(
                            source_type="paste",
                            source_value=source_detection_input,
                        )
                        source_label = "pasted sample content"
                    elif load_payload is None and (
                        looks_like_builtin_imdb_sample_request(source_detection_input)
                        or normalized_input in {"1", "option 1", "choice 1"}
                    ):
                        state.pending_localhost_index_options = []
                        load_payload = engine.load_sample(source_type="builtin_imdb")
                        source_label = f"built-in IMDb sample file ({BUILTIN_IMDB_SAMPLE_PATH})"
                    elif load_payload is None and looks_like_url_message(source_detection_input):
                        state.pending_localhost_index_options = []
                        load_payload = engine.load_sample(
                            source_type="url",
                            source_value=source_detection_input,
                        )
                        source_label = "URL"
                    elif load_payload is None and looks_like_local_path_message(source_detection_input):
                        state.pending_localhost_index_options = []
                        load_payload = engine.load_sample(
                            source_type="local_file",
                            source_value=source_detection_input,
                        )
                        source_label = "local file/folder path"
                    elif load_payload is None and option_4_selected:
                        state.pending_localhost_index_options = []
                        user_input = (
                            f"{safe_user_input}\n\n"
                            "System instruction: The user selected option 4 (paste sample content). "
                            "Ask the user to paste 1-3 representative JSON records now. "
                            "Provide this example format:\n"
                            '{"id":"1","title":"Example A","description":"Sample text A","category":"demo"}\n'
                            '{"id":"2","title":"Example B","description":"Sample text B","category":"demo"}\n'
                            "then call submit_sample_doc with that content."
                        )
                    elif load_payload is None and normalized_input in {"2", "option 2", "choice 2"}:
                        state.pending_localhost_index_options = []
                        load_payload = {
                            "error": (
                                "Error: option 2 selected, but no local path or URL was provided. "
                                f"Supported formats: {SUPPORTED_SAMPLE_FILE_FORMATS_COMMA}."
                            )
                        }
                        source_label = "path/URL option"

                    if isinstance(load_payload, dict) and "error" not in load_payload:
                        state.pending_localhost_index_options = []
                        status_msg = str(load_payload.get("status", "")).strip()
                        if status_msg:
                            print(f"Orchestrator: {status_msg}\n")
                        sample_payload = load_payload.get("sample_doc", {})
                        sample_json = json.dumps(sample_payload, ensure_ascii=False)
                        source_is_localhost_index = bool(load_payload.get("source_index_name"))

                        execution_policy_note = ""
                        if source_is_localhost_index:
                            policy_note = _build_localhost_execution_policy_note(state)
                            if policy_note:
                                execution_policy_note = f"{policy_note}\n"
                        doc_count_note = ""
                        if source_is_localhost_index:
                            localhost_count_note = _build_localhost_doc_count_note(state)
                            if localhost_count_note:
                                doc_count_note = f"{localhost_count_note}\n"
                        query_features_note = f"{_DEFAULT_QUERY_FEATURES_NOTE}\n"
                        text_search_use_case_note = (
                            f"{_build_text_search_use_case_note(state.inferred_text_search_required, state.inferred_semantic_text_fields)}\n"
                        )

                        phase = Phase.GATHER_INFO
                        user_input = (
                            "[SYSTEM PRE-PROCESSING RESULT]\n"
                            f"A sample document has already been loaded from {source_label}.\n"
                            f"Load result: {status_msg}\n"
                            f"{execution_policy_note}"
                            f"{doc_count_note}"
                            f"{query_features_note}"
                            f"{text_search_use_case_note}"
                            f"Sample document: {sample_json}\n"
                            "ACTION REQUIRED: Proceed to Phase 2 (requirements gathering). "
                            "DO NOT ask the user to paste sample content.\n\n"
                            "[USER MESSAGE]\n"
                            f"{safe_user_input}"
                        )
                    elif isinstance(load_payload, dict) and str(load_payload.get("error", "")).startswith("Error:"):
                        load_error = str(load_payload.get("error", ""))
                        source = source_label or "source"
                        localhost_empty_index_error = (
                            source_label == "localhost OpenSearch index"
                            and "has no documents" in load_error.lower()
                        )
                        localhost_index_selection_error = (
                            source_label == "localhost OpenSearch index"
                            and (
                                "no index name was provided" in load_error.lower()
                                or (
                                    "was not found on local opensearch" in load_error.lower()
                                    and "available non-system indices" in load_error.lower()
                                )
                            )
                        )
                        if localhost_empty_index_error:
                            state.pending_localhost_index_options = []
                            user_input = (
                                f"{safe_user_input}\n\n"
                                "System note: Automatic sample loading from localhost OpenSearch index failed.\n"
                                f"Failure reason: {load_error}\n"
                                "System instruction: The user already provided the index name. "
                                "Do NOT ask for the index name again. "
                                "Briefly explain that the index is empty, then ask the user to choose one of these: "
                                "ingest at least one document into the same index and reply retry, "
                                "provide a local path/URL in a supported format "
                                f"({SUPPORTED_SAMPLE_FILE_FORMATS_SLASH}) (option 2), "
                                "or use built-in IMDb sample (option 1), "
                                "or paste sample content directly (option 4)."
                            )
                        elif localhost_index_selection_error:
                            state.pending_localhost_index_options = (
                                _extract_localhost_index_options_from_error(load_error)
                            )
                            user_input = (
                                f"{safe_user_input}\n\n"
                                "System note: Automatic sample loading from localhost OpenSearch index failed.\n"
                                f"Failure reason: {load_error}\n"
                                "System instruction: Briefly tell the user this exact failure reason. "
                                "If the failure reason includes an index list, present that list and ask the user "
                                "to pick one index name from it for option 3 (they can reply with the list number "
                                "or exact index name). "
                                "Also mention alternatives: built-in IMDb sample (option 1), corrected path/URL "
                                f"(option 2, formats: {SUPPORTED_SAMPLE_FILE_FORMATS_SLASH}), "
                                "or pasted sample content (option 4)."
                            )
                        else:
                            state.pending_localhost_index_options = []
                            user_input = (
                                f"{safe_user_input}\n\n"
                                f"System note: Automatic sample loading from {source} failed.\n"
                                f"Failure reason: {load_error}\n"
                                "System instruction: Briefly tell the user this exact failure reason, "
                                "then ask them to provide one of these: "
                                "built-in IMDb sample (option 1), corrected path/URL (option 2), "
                                f"where supported formats are {SUPPORTED_SAMPLE_FILE_FORMATS_SLASH}, "
                                "a valid localhost index name (option 3), "
                                "or pasted sample content (option 4)."
                            )
                elif phase == Phase.COLLECT_SAMPLE:
                    phase = Phase.GATHER_INFO

            if phase == Phase.GATHER_INFO and state.sample_doc_json is not None:
                if state.inferred_text_search_required is None:
                    try:
                        parsed_sample_doc = json.loads(state.sample_doc_json)
                    except (json.JSONDecodeError, TypeError):
                        parsed_sample_doc = None
                    if parsed_sample_doc is not None:
                        state.inferred_semantic_text_fields = _infer_semantic_text_fields(parsed_sample_doc)
                        state.inferred_text_search_required = bool(state.inferred_semantic_text_fields)

                if state.budget_preference is None:
                    state.budget_preference = _infer_budget_preference_from_text(raw_user_input)
                if state.performance_priority is None:
                    state.performance_priority = _infer_performance_priority_from_text(raw_user_input)
                if state.prefix_wildcard_enabled is None:
                    state.prefix_wildcard_enabled = _infer_prefix_wildcard_preference_from_text(
                        raw_user_input
                    )
                if state.budget_preference is None:
                    state.budget_preference = read_single_choice_input(
                        title="Budget Preference",
                        prompt=(
                            "Choose budget/cost preference for this solution."
                        ),
                        options=[
                            (
                                _BUDGET_OPTION_FLEXIBLE,
                                "No strict budget constraints (flexible)",
                            ),
                            (
                                _BUDGET_OPTION_COST_SENSITIVE,
                                "Cost-sensitive (prioritize cost-effectiveness)",
                            ),
                        ],
                        default_value=_BUDGET_OPTION_FLEXIBLE,
                    )
                if state.performance_priority is None:
                    state.performance_priority = read_single_choice_input(
                        title="Performance Priority",
                        prompt=(
                            "Choose the primary speed-vs-accuracy priority."
                        ),
                        options=[
                            (
                                _PERFORMANCE_OPTION_SPEED,
                                "Speed-first",
                            ),
                            (
                                _PERFORMANCE_OPTION_BALANCED,
                                "Balanced",
                            ),
                            (
                                _PERFORMANCE_OPTION_ACCURACY,
                                "Accuracy-first",
                            ),
                        ],
                        default_value=_PERFORMANCE_OPTION_BALANCED,
                    )
                if (
                    state.prefix_wildcard_enabled is None
                    and state.inferred_text_search_required
                ):
                    state.prefix_wildcard_enabled = False
                if (
                    state.hybrid_weight_profile is None
                    and bool(state.inferred_text_search_required)
                ):
                    state.hybrid_weight_profile = _read_hybrid_weight_profile_choice(
                        state.inferred_semantic_text_fields
                    )
                if (
                    state.model_deployment_preference is None
                    and bool(state.inferred_text_search_required)
                    and _requires_model_deployment_preference(state.hybrid_weight_profile)
                ):
                    state.model_deployment_preference = _read_model_deployment_preference_choice(
                        state.inferred_semantic_text_fields
                    )

                hybrid_profile = str(state.hybrid_weight_profile or "").strip().lower()
                if hybrid_profile == _HYBRID_WEIGHT_OPTION_LEXICAL:
                    query_pattern = _QUERY_PATTERN_OPTION_MOSTLY_EXACT
                elif hybrid_profile == _HYBRID_WEIGHT_OPTION_SEMANTIC:
                    query_pattern = _QUERY_PATTERN_OPTION_MOSTLY_SEMANTIC
                else:
                    query_pattern = _QUERY_PATTERN_OPTION_BALANCED

                engine.set_preferences(
                    budget=str(state.budget_preference or _BUDGET_OPTION_FLEXIBLE),
                    performance=str(state.performance_priority or _PERFORMANCE_OPTION_BALANCED),
                    query_pattern=query_pattern,
                    deployment_preference=str(state.model_deployment_preference or ""),
                )
            if state.source_index_name:
                extra_notes: list[str] = []
                if "Execution policy: source is localhost OpenSearch index" not in user_input:
                    localhost_policy_note = _build_localhost_execution_policy_note(state)
                    if localhost_policy_note:
                        extra_notes.append(localhost_policy_note)
                if (
                    isinstance(state.source_index_doc_count, int)
                    and "Requirements note: exact current document count already measured from OpenSearch count API" not in user_input
                ):
                    localhost_count_note = _build_localhost_doc_count_note(state)
                    if localhost_count_note:
                        extra_notes.append(localhost_count_note)
                if extra_notes:
                    user_input = (
                        f"{user_input}\n\n"
                        "[SYSTEM EXECUTION POLICY]\n"
                        + "\n".join(extra_notes)
                    )

            if state.sample_doc_json is not None:
                parsed_sample_doc: dict | None = None
                if state.inferred_text_search_required is None:
                    try:
                        parsed_sample_doc = json.loads(state.sample_doc_json)
                    except (json.JSONDecodeError, TypeError):
                        parsed_sample_doc = None
                if parsed_sample_doc is not None:
                    state.inferred_semantic_text_fields = _infer_semantic_text_fields(parsed_sample_doc)
                    state.inferred_text_search_required = bool(state.inferred_semantic_text_fields)

                requirement_notes = [
                    note
                    for prefix, note in _iter_context_note_entries(state)
                    if prefix not in user_input
                ]
                if requirement_notes:
                    user_input = (
                        f"{user_input}\n\n"
                        "[SYSTEM REQUIREMENT DEFAULTS]\n"
                        + "\n".join(requirement_notes)
                    )

            # ── Agent turn ──────────────────────────────────────────

            print("Orchestrator: ", end="", flush=True)

            stream = agent.stream_async(user_input)
            async for event in stream:
                pass

            print()

            # ── Post-turn phase transitions ─────────────────────────

            if phase == Phase.COLLECT_SAMPLE and state.sample_doc_json is not None:
                phase = Phase.GATHER_INFO

            if check_and_clear_execution_flag():
                worker_state = get_last_worker_run_state()
                worker_status = str(worker_state.get("status", "")).lower()
                phase = Phase.DONE if worker_status == "success" else Phase.EXEC_FAILED

        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print(f"\nAn error occurred: {e}")


if __name__ == "__main__":
    asyncio.run(main())
