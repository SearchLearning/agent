"""Tests for OrchestratorEngine.prepare_aws_deployment strategy detection."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import opensearch_orchestrator.orchestrator as orchestrator
from opensearch_orchestrator.shared import Phase


def _engine_at_done(solution: str) -> orchestrator.OrchestratorEngine:
    engine = orchestrator.create_transport_agnostic_engine(orchestrator.SessionState())
    engine.load_sample("builtin_imdb")
    engine.set_plan(solution=solution, search_capabilities="- Exact: match", keynote="k")
    engine.phase = Phase.DONE
    return engine


def test_prepare_aws_deployment_requires_done_phase():
    engine = orchestrator.create_transport_agnostic_engine(orchestrator.SessionState())
    result = engine.prepare_aws_deployment()
    assert "error" in result
    assert "Phase 4" in result["error"]


def test_prepare_aws_deployment_requires_plan():
    engine = orchestrator.create_transport_agnostic_engine(orchestrator.SessionState())
    engine.phase = Phase.DONE
    result = engine.prepare_aws_deployment()
    assert "error" in result
    assert "plan" in result["error"].lower()


def test_prepare_aws_deployment_hybrid_strategy():
    engine = _engine_at_done("Hybrid Search (BM25 + Dense Vector)")
    result = engine.prepare_aws_deployment()
    assert result["search_strategy"] == "hybrid"
    assert result["deployment_target"] == "serverless"
    assert "serverless-01-provision.md" in result["steering_files"][0]


def test_prepare_aws_deployment_agentic_strategy_uses_domain():
    engine = _engine_at_done("Agentic search with flow agent")
    result = engine.prepare_aws_deployment()
    assert result["search_strategy"] == "agentic"
    assert result["deployment_target"] == "domain"
    assert "domain-01-provision.md" in result["steering_files"][0]
    assert "domain-03-agentic-setup.md" in result["steering_files"][2]


def test_prepare_aws_deployment_dense_vector_strategy():
    engine = _engine_at_done("Dense vector HNSW with text_embedding model")
    result = engine.prepare_aws_deployment()
    assert result["search_strategy"] == "dense_vector"
    assert result["deployment_target"] == "serverless"


def test_prepare_aws_deployment_neural_sparse_strategy():
    engine = _engine_at_done("Neural sparse encoding retrieval")
    result = engine.prepare_aws_deployment()
    assert result["search_strategy"] == "neural_sparse"
    assert result["deployment_target"] == "serverless"


def test_prepare_aws_deployment_bm25_fallback():
    engine = _engine_at_done("Lexical BM25 keyword search only")
    result = engine.prepare_aws_deployment()
    assert result["search_strategy"] == "bm25"
    assert result["deployment_target"] == "serverless"


def test_prepare_aws_deployment_includes_required_mcp_servers():
    engine = _engine_at_done("Hybrid Search")
    result = engine.prepare_aws_deployment()
    assert "awslabs.aws-api-mcp-server" in result["required_mcp_servers"]
    assert "opensearch-mcp-server" in result["required_mcp_servers"]


def test_prepare_aws_deployment_state_file_template_keys():
    engine = _engine_at_done("Hybrid Search")
    result = engine.prepare_aws_deployment()
    template = result["state_file_template"]
    for key in ("deployment_target", "search_strategy", "step_completed", "index_name"):
        assert key in template


def test_prepare_aws_deployment_local_config_includes_text_fields():
    engine = _engine_at_done("Hybrid Search")
    result = engine.prepare_aws_deployment()
    assert "text_fields" in result["local_config"]
    assert isinstance(result["local_config"]["text_fields"], list)
