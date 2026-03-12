"""Tests that evaluation findings are injected into planning context on restart."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import opensearch_orchestrator.orchestrator as orchestrator


def _make_engine_with_evaluation() -> orchestrator.OrchestratorEngine:
    """Return an engine with a loaded sample, finalized plan, and stored evaluation."""
    engine = orchestrator.create_transport_agnostic_engine(orchestrator.SessionState())
    engine.load_sample("builtin_imdb")
    engine.set_plan(
        solution="Hybrid Search (BM25 + Dense Vector)",
        search_capabilities="- Exact: title match\n- Semantic: concept retrieval",
        keynote="Balanced profile for IMDb.",
    )
    engine.set_evaluation(
        search_quality_summary="Semantic queries: 15% precision. Exact term queries: 0% precision.",
        issues="[Exact] keyword subfield mapping missing → add .keyword subfields\n"
               "[Semantic] dense vector index empty after reindex → ensure reindex completes",
        suggested_preferences={"query_pattern": "mostly-exact"},
    )
    return engine


def test_build_evaluation_context_includes_summary_and_issues():
    engine = _make_engine_with_evaluation()
    ctx = engine._build_evaluation_context()
    assert "Previous evaluation findings" in ctx
    assert "Semantic queries: 15% precision" in ctx
    assert "keyword subfield mapping missing" in ctx
    assert "query_pattern=mostly-exact" in ctx


def test_build_evaluation_context_empty_when_no_evaluation():
    engine = orchestrator.create_transport_agnostic_engine(orchestrator.SessionState())
    assert engine._build_evaluation_context() == ""


def test_build_evaluation_context_empty_when_evaluation_is_none():
    engine = orchestrator.create_transport_agnostic_engine(orchestrator.SessionState())
    engine.evaluation_result = None
    assert engine._build_evaluation_context() == ""


def test_start_planning_injects_evaluation_context(monkeypatch):
    """Verify that start_planning passes evaluation findings into the planning context."""
    engine = _make_engine_with_evaluation()
    # Reset plan so start_planning can proceed
    engine.plan_result = None
    # Update preferences for the restart
    engine.set_preferences(query_pattern="mostly-exact")

    captured_context = {}

    class FakePlanning:
        def start(self, context):
            captured_context["value"] = context
            return {"response": "proposal", "is_complete": False}

    monkeypatch.setattr(engine, "_planning_session_factory", lambda **kw: FakePlanning())

    import asyncio
    asyncio.run(engine.start_planning())

    ctx = captured_context["value"]
    assert "Previous evaluation findings" in ctx
    assert "keyword subfield mapping missing" in ctx
    assert "Semantic queries: 15% precision" in ctx


def test_start_planning_preserves_explicit_additional_context(monkeypatch):
    """Verify that explicit additional_context is merged with evaluation context."""
    engine = _make_engine_with_evaluation()
    engine.plan_result = None
    engine.set_preferences(query_pattern="mostly-exact")

    captured_context = {}

    class FakePlanning:
        def start(self, context):
            captured_context["value"] = context
            return {"response": "proposal", "is_complete": False}

    monkeypatch.setattr(engine, "_planning_session_factory", lambda **kw: FakePlanning())

    import asyncio
    asyncio.run(
        engine.start_planning(additional_context="User wants faster queries")
    )

    ctx = captured_context["value"]
    # Both evaluation context and explicit additional context should be present
    assert "Previous evaluation findings" in ctx
    assert "User wants faster queries" in ctx
