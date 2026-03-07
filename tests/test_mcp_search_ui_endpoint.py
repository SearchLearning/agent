"""Tests for connect_search_ui_to_endpoint and disconnect_search_ui_from_endpoint MCP tools."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import opensearch_orchestrator.mcp_server as mcp_server


def test_connect_search_ui_to_endpoint_delegates_to_impl(monkeypatch) -> None:
    captured: dict = {}

    def _fake_impl(*, endpoint, port, use_ssl, username, password, aws_region, aws_service, index_name):
        captured.update(
            endpoint=endpoint, port=port, use_ssl=use_ssl,
            username=username, aws_region=aws_region, aws_service=aws_service,
            index_name=index_name,
        )
        return "Connected to endpoint."

    monkeypatch.setattr(mcp_server, "connect_search_ui_to_endpoint_impl", _fake_impl)

    result = mcp_server.connect_search_ui_to_endpoint(
        endpoint="search-my-domain.us-east-1.es.amazonaws.com",
        port=443,
        use_ssl=True,
        aws_region="us-east-1",
        aws_service="es",
        index_name="my-index",
    )

    assert result == "Connected to endpoint."
    assert captured["endpoint"] == "search-my-domain.us-east-1.es.amazonaws.com"
    assert captured["port"] == 443
    assert captured["aws_region"] == "us-east-1"
    assert captured["index_name"] == "my-index"


def test_disconnect_search_ui_from_endpoint_delegates_to_impl(monkeypatch) -> None:
    called = {"count": 0}

    def _fake_impl():
        called["count"] += 1
        return "Disconnected. Reverted to local OpenSearch."

    monkeypatch.setattr(mcp_server, "disconnect_search_ui_from_endpoint_impl", _fake_impl)

    result = mcp_server.disconnect_search_ui_from_endpoint()

    assert result == "Disconnected. Reverted to local OpenSearch."
    assert called["count"] == 1


def test_connect_and_disconnect_tools_in_default_surface() -> None:
    import asyncio
    tool_names = {tool.name for tool in asyncio.run(mcp_server.mcp.list_tools())}
    assert "connect_search_ui_to_endpoint" in tool_names
    assert "disconnect_search_ui_from_endpoint" in tool_names
