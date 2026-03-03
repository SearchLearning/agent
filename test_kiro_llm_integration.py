#!/usr/bin/env python3
"""Quick test to verify Kiro LLM integration for relevance evaluation."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from mcp import types as mcp_types


@pytest.mark.asyncio
async def test_evaluate_relevance_with_llm():
    """Test the _evaluate_relevance_with_llm function."""
    # Import the function
    from opensearch_orchestrator.scripts.opensearch_ops_tools import _evaluate_relevance_with_llm
    
    # Mock MCP context
    ctx = MagicMock()
    ctx.session = MagicMock()
    
    # Mock the create_message response
    mock_response = MagicMock()
    mock_response.content = mcp_types.TextContent(type="text", text="1")
    ctx.session.create_message = AsyncMock(return_value=mock_response)
    
    # Test data
    query = "action movies"
    doc_source = {
        "title": "Die Hard",
        "genres": "Action, Thriller",
        "year": 1988,
        "title_embedding": [0.1, 0.2, 0.3]  # Should be excluded
    }
    
    # Call the function
    result = await _evaluate_relevance_with_llm(query, doc_source, ctx)
    
    # Verify
    assert result == 1, f"Expected 1, got {result}"
    assert ctx.session.create_message.called, "create_message should be called"
    
    # Verify the prompt was constructed correctly
    call_args = ctx.session.create_message.call_args
    messages = call_args.kwargs['messages']
    assert len(messages) == 1
    assert messages[0].role == "user"
    assert "action movies" in messages[0].content.text
    assert "Die Hard" in messages[0].content.text
    assert "title_embedding" not in messages[0].content.text  # Embedding should be excluded
    
    print("✅ Test passed: _evaluate_relevance_with_llm works correctly")


@pytest.mark.asyncio
async def test_evaluate_relevance_fallback():
    """Test fallback when LLM fails."""
    from opensearch_orchestrator.scripts.opensearch_ops_tools import _evaluate_relevance_with_llm
    
    # Mock MCP context that raises an exception
    ctx = MagicMock()
    ctx.session = MagicMock()
    ctx.session.create_message = AsyncMock(side_effect=Exception("MCP unavailable"))
    
    # Test data
    query = "test query"
    doc_source = {"title": "Test Doc"}
    
    # Call the function
    result = await _evaluate_relevance_with_llm(query, doc_source, ctx)
    
    # Should return -1 to signal fallback needed
    assert result == -1, f"Expected -1 for fallback, got {result}"
    
    print("✅ Test passed: Fallback works when LLM fails")


if __name__ == "__main__":
    print("Testing Kiro LLM integration for relevance evaluation...\n")
    asyncio.run(test_evaluate_relevance_with_llm())
    asyncio.run(test_evaluate_relevance_fallback())
    print("\n✅ All tests passed!")
