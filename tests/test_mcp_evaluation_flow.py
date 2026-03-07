"""Tests for MCP evaluation tools: start_evaluation, set_evaluation_from_evaluation_complete."""

import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import opensearch_orchestrator.mcp_server as mcp_server


class _DummyContext:
    def __init__(self, session) -> None:
        self.session = session


class _EvaluationSession:
    """Session that returns a well-formed <evaluation_complete> block."""

    def __init__(self, response_text: str) -> None:
        self._response_text = response_text

    async def create_message(self, *, messages, max_tokens, system_prompt):
        _ = messages, max_tokens, system_prompt
        from mcp import types as mcp_types
        return type(
            "SamplingResult",
            (),
            {"content": mcp_types.TextContent(type="text", text=self._response_text)},
        )()


_VALID_EVALUATION_RESPONSE = """\
<evaluation_complete>
<search_quality_summary>Hybrid search performs well for exact and semantic queries.</search_quality_summary>
<issues>
- Semantic recall drops on very long queries
</issues>
<suggested_preferences>{"query_pattern": "mostly-semantic"}</suggested_preferences>
</evaluation_complete>
"""


class _RecordingEngine:
    def __init__(self) -> None:
        self.plan_result = {"solution": "Hybrid Search", "search_capabilities": "", "keynote": ""}
        self.captured: dict = {}

    def set_evaluation(self, *, search_quality_summary, issues="", suggested_preferences=None):
        self.captured["summary"] = search_quality_summary
        self.captured["issues"] = issues
        self.captured["prefs"] = suggested_preferences
        return {"status": "Evaluation stored.", "result": {"search_quality_summary": search_quality_summary}}


def test_start_evaluation_returns_manual_fallback_without_ctx(monkeypatch) -> None:
    engine = _RecordingEngine()
    monkeypatch.setattr(mcp_server, "_engine", engine)

    result = asyncio.run(mcp_server.start_evaluation(ctx=None))

    assert result["error"] == "Evaluation failed in client mode."
    assert result["manual_evaluation_required"] is True
    assert "evaluation_prompt" in result


def test_start_evaluation_requires_plan(monkeypatch) -> None:
    class _NoPlanEngine:
        plan_result = None

    monkeypatch.setattr(mcp_server, "_engine", _NoPlanEngine())

    result = asyncio.run(mcp_server.start_evaluation(ctx=None))
    assert "error" in result
    assert "plan" in result["error"].lower()


def test_start_evaluation_with_client_sampling_stores_result(monkeypatch) -> None:
    engine = _RecordingEngine()
    monkeypatch.setattr(mcp_server, "_engine", engine)
    monkeypatch.setattr(mcp_server, "_persist_engine_state", lambda *a, **kw: None)

    ctx = _DummyContext(_EvaluationSession(_VALID_EVALUATION_RESPONSE))
    result = asyncio.run(mcp_server.start_evaluation(ctx=ctx))

    assert "error" not in result
    assert result["evaluation_backend"] == "client_sampling"
    assert engine.captured["summary"] == "Hybrid search performs well for exact and semantic queries."
    assert engine.captured["prefs"] == {"query_pattern": "mostly-semantic"}


def test_start_evaluation_returns_manual_fallback_on_method_not_found(monkeypatch) -> None:
    engine = _RecordingEngine()
    monkeypatch.setattr(mcp_server, "_engine", engine)

    class _FailSession:
        async def create_message(self, **kwargs):
            raise Exception("Method not found")

    result = asyncio.run(mcp_server.start_evaluation(ctx=_DummyContext(_FailSession())))

    assert result["error"] == "Evaluation failed in client mode."
    assert result["manual_evaluation_required"] is True
    assert "evaluation_prompt" in result


def test_set_evaluation_from_evaluation_complete_parses_and_stores(monkeypatch) -> None:
    engine = _RecordingEngine()
    monkeypatch.setattr(mcp_server, "_engine", engine)
    monkeypatch.setattr(mcp_server, "_persist_engine_state", lambda *a, **kw: None)

    result = mcp_server.set_evaluation_from_evaluation_complete(_VALID_EVALUATION_RESPONSE)

    assert "error" not in result
    assert engine.captured["summary"] == "Hybrid search performs well for exact and semantic queries."


def test_set_evaluation_from_evaluation_complete_rejects_missing_block(monkeypatch) -> None:
    engine = _RecordingEngine()
    monkeypatch.setattr(mcp_server, "_engine", engine)

    result = mcp_server.set_evaluation_from_evaluation_complete("No block here.")
    assert "error" in result
    assert "<evaluation_complete>" in result["error"] or "evaluation_complete" in result["error"]


def test_parse_evaluation_complete_extracts_all_fields() -> None:
    parsed = mcp_server._parse_evaluation_complete_response(_VALID_EVALUATION_RESPONSE)
    assert "error" not in parsed
    assert "Hybrid search" in parsed["search_quality_summary"]
    assert "Semantic recall" in parsed["issues"]
    assert parsed["suggested_preferences"] == {"query_pattern": "mostly-semantic"}


def test_parse_evaluation_complete_returns_error_on_missing_summary() -> None:
    bad_response = "<evaluation_complete><issues>- some issue</issues></evaluation_complete>"
    parsed = mcp_server._parse_evaluation_complete_response(bad_response)
    assert "error" in parsed


def test_build_evaluation_prompt_covers_key_dimensions(monkeypatch) -> None:
    engine = _RecordingEngine()
    monkeypatch.setattr(mcp_server, "_engine", engine)
    monkeypatch.setattr(mcp_server, "_last_verification_suggestion_meta", [])

    prompt = mcp_server._build_evaluation_prompt()

    assert "relevance" in prompt.lower()
    assert "query coverage" in prompt.lower()
    assert "ranking quality" in prompt.lower()
    assert "capability gap" in prompt.lower()
    # latency and recall are not the focus
    assert "1" in prompt and "5" in prompt
    assert "<evaluation_complete>" in prompt
    assert "<relevance>" in prompt
    assert "<query_coverage>" in prompt
    assert "<ranking_quality>" in prompt
    assert "<capability_gap>" in prompt
    assert "<issues>" in prompt
    assert "<suggested_preferences>" in prompt


def test_build_evaluation_prompt_includes_suggestion_meta_when_available(monkeypatch) -> None:
    engine = _RecordingEngine()
    monkeypatch.setattr(mcp_server, "_engine", engine)
    monkeypatch.setattr(mcp_server, "_last_verification_suggestion_meta", [
        {"capability": "semantic", "text": "films about loss"},
        {"capability": "exact", "text": "Carmencita 1894"},
    ])

    prompt = mcp_server._build_evaluation_prompt()

    assert "films about loss" in prompt
    assert "Carmencita 1894" in prompt
    assert "Verification Queries" in prompt
    assert "observed" in prompt.lower() or "verification" in prompt.lower()


def test_build_evaluation_prompt_notes_missing_evidence(monkeypatch) -> None:
    engine = _RecordingEngine()
    monkeypatch.setattr(mcp_server, "_engine", engine)
    monkeypatch.setattr(mcp_server, "_last_verification_suggestion_meta", [])

    prompt = mcp_server._build_evaluation_prompt()

    assert "architectural estimates" in prompt.lower() or "no verification" in prompt.lower()


def test_apply_capability_driven_verification_stores_suggestion_meta(monkeypatch) -> None:
    import asyncio
    monkeypatch.setattr(mcp_server, "_last_verification_suggestion_meta", [])

    def _fake_impl(**kwargs):
        return {
            "applied": True,
            "index_name": "my-index",
            "suggestion_meta": [
                {"capability": "exact", "text": "Carmencita 1894"},
                {"capability": "semantic", "text": "early silent films"},
            ],
        }

    monkeypatch.setattr(mcp_server, "apply_capability_driven_verification_impl", _fake_impl)
    async def _noop_rewrite(*, result, ctx):
        return result

    monkeypatch.setattr(mcp_server, "_rewrite_semantic_suggestion_entries_with_client_llm", _noop_rewrite)

    asyncio.run(mcp_server.apply_capability_driven_verification(
        worker_output="some plan", index_name="my-index", ctx=None
    ))

    assert len(mcp_server._last_verification_suggestion_meta) == 2
    assert mcp_server._last_verification_suggestion_meta[0]["capability"] == "exact"


def test_start_evaluation_uses_stored_suggestion_meta(monkeypatch) -> None:
    import asyncio
    engine = _RecordingEngine()
    monkeypatch.setattr(mcp_server, "_engine", engine)
    monkeypatch.setattr(mcp_server, "_last_verification_suggestion_meta", [
        {"capability": "semantic", "text": "action movies from the 90s"},
    ])

    result = asyncio.run(mcp_server.start_evaluation(ctx=None))

    assert result["manual_evaluation_required"] is True
    assert "action movies from the 90s" in result["evaluation_prompt"]
