"""Tests for OrchestratorEngine.set_evaluation and set_plan methods."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import opensearch_orchestrator.orchestrator as orchestrator


def _make_engine_with_plan() -> orchestrator.OrchestratorEngine:
    """Return an engine with a loaded sample and finalized plan."""
    engine = orchestrator.create_transport_agnostic_engine(orchestrator.SessionState())
    engine.load_sample("builtin_imdb")
    engine.set_plan(
        solution="Hybrid Search (BM25 + Dense Vector)",
        search_capabilities="- Exact: title match\n- Semantic: concept retrieval",
        keynote="Balanced profile for IMDb.",
    )
    return engine


def test_set_evaluation_stores_result():
    engine = _make_engine_with_plan()
    result = engine.set_evaluation(
        search_quality_summary="Good relevance overall.",
        issues="- Semantic recall drops on long queries",
        suggested_preferences={"query_pattern": "mostly-semantic"},
    )
    assert result["status"] == "Evaluation stored."
    assert result["result"]["search_quality_summary"] == "Good relevance overall."
    assert result["result"]["issues"] == "- Semantic recall drops on long queries"
    assert result["result"]["suggested_preferences"] == {"query_pattern": "mostly-semantic"}


def test_set_evaluation_requires_plan():
    engine = orchestrator.create_transport_agnostic_engine(orchestrator.SessionState())
    result = engine.set_evaluation(search_quality_summary="Good.")
    assert "error" in result
    assert "plan" in result["error"].lower()


def test_set_evaluation_requires_non_empty_summary():
    engine = _make_engine_with_plan()
    result = engine.set_evaluation(search_quality_summary="   ")
    assert "error" in result
    assert "search_quality_summary" in result["error"]


def test_set_evaluation_defaults_empty_issues_and_prefs():
    engine = _make_engine_with_plan()
    result = engine.set_evaluation(search_quality_summary="Looks good.")
    assert result["result"]["issues"] == ""
    assert result["result"]["suggested_preferences"] == {}


def test_set_evaluation_ignores_non_dict_suggested_preferences():
    engine = _make_engine_with_plan()
    result = engine.set_evaluation(
        search_quality_summary="OK.",
        suggested_preferences="not-a-dict",  # type: ignore[arg-type]
    )
    assert result["result"]["suggested_preferences"] == {}


def test_set_plan_requires_sample():
    engine = orchestrator.create_transport_agnostic_engine(orchestrator.SessionState())
    result = engine.set_plan(solution="Hybrid Search")
    assert "error" in result
    assert "load_sample" in result["error"]


def test_set_plan_requires_non_empty_solution():
    engine = orchestrator.create_transport_agnostic_engine(orchestrator.SessionState())
    engine.load_sample("builtin_imdb")
    result = engine.set_plan(solution="   ")
    assert "error" in result
    assert "solution" in result["error"]


def test_set_plan_stores_all_fields():
    engine = orchestrator.create_transport_agnostic_engine(orchestrator.SessionState())
    engine.load_sample("builtin_imdb")
    result = engine.set_plan(
        solution="BM25 lexical search",
        search_capabilities="- Exact: keyword match",
        keynote="Cost-sensitive setup.",
    )
    assert result["status"] == "Plan stored."
    assert result["result"]["solution"] == "BM25 lexical search"
    assert result["result"]["search_capabilities"] == "- Exact: keyword match"
    assert result["result"]["keynote"] == "Cost-sensitive setup."
