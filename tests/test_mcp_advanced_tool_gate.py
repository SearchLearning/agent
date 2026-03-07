import asyncio

import pytest

import opensearch_orchestrator.mcp_server as mcp_server


def test_advanced_tools_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv(mcp_server.ADVANCED_TOOLS_ENV, raising=False)
    assert not mcp_server._advanced_tools_enabled()


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", " Yes "])
def test_advanced_tools_enabled_values(monkeypatch, value: str) -> None:
    monkeypatch.setenv(mcp_server.ADVANCED_TOOLS_ENV, value)
    assert mcp_server._advanced_tools_enabled()


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "random"])
def test_advanced_tools_disabled_values(monkeypatch, value: str) -> None:
    monkeypatch.setenv(mcp_server.ADVANCED_TOOLS_ENV, value)
    assert not mcp_server._advanced_tools_enabled()


def test_default_tool_surface_is_workflow_only() -> None:
    tool_names = {tool.name for tool in asyncio.run(mcp_server.mcp.list_tools())}
    assert tool_names == {
        "apply_capability_driven_verification",
        "cleanup",
        "connect_search_ui_to_endpoint",
        "create_and_attach_pipeline",
        "create_agentic_search_flow_agent",
        "create_agentic_search_pipeline",
        "create_bedrock_agentic_model_with_creds",
        "create_bedrock_embedding_model",
        "create_index",
        "create_local_pretrained_model",
        "execute_plan",
        "finalize_plan",
        "launch_search_ui",
        "load_sample",
        "prepare_aws_deployment",
        "read_agentic_search_guide",
        "read_dense_vector_models",
        "read_knowledge_base",
        "read_sparse_vector_models",
        "refine_plan",
        "retry_execution",
        "search_opensearch_org",
        "set_evaluation_from_evaluation_complete",
        "set_execution_from_execution_report",
        "set_plan_from_planning_complete",
        "set_preferences",
        "set_search_ui_suggestions",
        "start_evaluation",
        "start_planning",
        "talk_to_client_llm",
    }
