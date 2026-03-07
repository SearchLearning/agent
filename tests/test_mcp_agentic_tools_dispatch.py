"""Tests for MCP agentic tool dispatch: create_bedrock_agentic_model_with_creds,
create_agentic_search_flow_agent, create_agentic_search_pipeline."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import opensearch_orchestrator.mcp_server as mcp_server


def test_create_bedrock_agentic_model_with_creds_delegates_to_impl(monkeypatch) -> None:
    captured: dict = {}

    def _fake_impl(*, access_key, secret_key, region, session_token, model_name):
        captured.update(
            access_key=access_key, secret_key=secret_key,
            region=region, session_token=session_token, model_name=model_name,
        )
        return "model-id-abc123"

    monkeypatch.setattr(mcp_server, "create_bedrock_agentic_model_with_creds_impl", _fake_impl)

    result = mcp_server.create_bedrock_agentic_model_with_creds(
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
        session_token="token-xyz",
        model_name="us.anthropic.claude-sonnet-4-20250514-v1:0",
    )

    assert result == "model-id-abc123"
    assert captured["access_key"] == "AKIAIOSFODNN7EXAMPLE"
    assert captured["region"] == "us-east-1"
    assert captured["model_name"] == "us.anthropic.claude-sonnet-4-20250514-v1:0"


def test_create_agentic_search_flow_agent_delegates_to_impl(monkeypatch) -> None:
    captured: dict = {}

    def _fake_impl(*, agent_name, model_id):
        captured.update(agent_name=agent_name, model_id=model_id)
        return "Flow agent 'my-agent' (ID: agent-id-xyz) created successfully."

    monkeypatch.setattr(mcp_server, "create_agentic_search_flow_agent_impl", _fake_impl)

    result = mcp_server.create_agentic_search_flow_agent(
        agent_name="my-agent",
        model_id="model-id-abc123",
    )

    assert "agent-id-xyz" in result
    assert captured["agent_name"] == "my-agent"
    assert captured["model_id"] == "model-id-abc123"


def test_create_agentic_search_pipeline_delegates_to_impl(monkeypatch) -> None:
    captured: dict = {}

    def _fake_impl(*, pipeline_name, agent_id, index_name, replace_if_exists):
        captured.update(
            pipeline_name=pipeline_name, agent_id=agent_id,
            index_name=index_name, replace_if_exists=replace_if_exists,
        )
        return "Pipeline 'my-pipeline' created and attached to 'my-index'."

    monkeypatch.setattr(mcp_server, "create_agentic_search_pipeline_impl", _fake_impl)

    result = mcp_server.create_agentic_search_pipeline(
        pipeline_name="my-pipeline",
        agent_id="agent-id-xyz",
        index_name="my-index",
        replace_if_exists=True,
    )

    assert "my-pipeline" in result
    assert captured["agent_id"] == "agent-id-xyz"
    assert captured["index_name"] == "my-index"
    assert captured["replace_if_exists"] is True


def test_create_agentic_search_pipeline_default_replace_if_exists(monkeypatch) -> None:
    captured: dict = {}

    def _fake_impl(*, pipeline_name, agent_id, index_name, replace_if_exists):
        captured["replace_if_exists"] = replace_if_exists
        return "ok"

    monkeypatch.setattr(mcp_server, "create_agentic_search_pipeline_impl", _fake_impl)
    mcp_server.create_agentic_search_pipeline(
        pipeline_name="p", agent_id="a", index_name="i"
    )
    assert captured["replace_if_exists"] is True
