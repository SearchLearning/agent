"""Tests for search relevance evaluation feature."""

import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from opensearch_orchestrator.scripts.opensearch_ops_tools import (
    set_search_relevance_scores,
    _search_ui,
)


def test_set_search_relevance_scores_valid():
    """Test setting valid relevance scores."""
    print("Testing set_search_relevance_scores with valid data...")
    index_name = "test-index"
    relevance_data = {"doc1": 1, "doc2": 0, "doc3": 1}
    
    result = set_search_relevance_scores(
        index_name=index_name,
        relevance_data_json=json.dumps(relevance_data)
    )
    
    assert "Set relevance scores for 3 documents" in result
    assert index_name in _search_ui.relevance_scores
    assert _search_ui.relevance_scores[index_name] == relevance_data
    print("✓ Valid data test passed")


def test_set_search_relevance_scores_invalid_json():
    """Test setting relevance scores with invalid JSON."""
    print("Testing set_search_relevance_scores with invalid JSON...")
    result = set_search_relevance_scores(
        index_name="test-index",
        relevance_data_json="not valid json"
    )
    
    assert "Invalid relevance_data_json" in result
    print("✓ Invalid JSON test passed")


def test_set_search_relevance_scores_invalid_score():
    """Test setting relevance scores with invalid score values."""
    print("Testing set_search_relevance_scores with invalid score...")
    result = set_search_relevance_scores(
        index_name="test-index",
        relevance_data_json='{"doc1": 2}'  # Invalid score (must be 0 or 1)
    )
    
    assert "Invalid relevance score" in result
    assert "Must be 0 or 1" in result
    print("✓ Invalid score test passed")


def test_set_search_relevance_scores_missing_index():
    """Test setting relevance scores without index name."""
    print("Testing set_search_relevance_scores without index name...")
    result = set_search_relevance_scores(
        index_name="",
        relevance_data_json='{"doc1": 1}'
    )
    
    assert "index_name is required" in result
    print("✓ Missing index test passed")


def test_set_search_relevance_scores_not_dict():
    """Test setting relevance scores with non-dict JSON."""
    print("Testing set_search_relevance_scores with non-dict JSON...")
    result = set_search_relevance_scores(
        index_name="test-index",
        relevance_data_json='["doc1", "doc2"]'  # Array instead of object
    )
    
    assert "must be a JSON object" in result
    print("✓ Non-dict JSON test passed")


@patch('opensearch_orchestrator.scripts.opensearch_ops_tools._search_ui_search')
@patch('opensearch_orchestrator.scripts.opensearch_ops_tools._search_ui_suggestions')
def test_search_relevance_evaluation_basic(mock_suggestions, mock_search):
    """Test basic search relevance evaluation."""
    print("Testing search_relevance_evaluation basic functionality...")
    import asyncio
    from opensearch_orchestrator.mcp_server import search_relevance_evaluation
    
    # Mock MCP context
    class _DummyContext:
        def __init__(self):
            self.session = None
    
    ctx = _DummyContext()
    
    # Mock search results
    mock_search.return_value = {
        "error": "",
        "hits": [
            {
                "id": "doc1",
                "score": 2.5,
                "preview": "This is an action movie with explosions",
                "source": {"title": "Action Movie"}
            },
            {
                "id": "doc2",
                "score": 0.5,
                "preview": "A romantic comedy about love",
                "source": {"title": "Rom Com"}
            }
        ],
        "capability": "semantic",
        "query_mode": "hybrid"
    }
    
    mock_suggestions.return_value = {"suggestions": []}
    
    result = asyncio.run(search_relevance_evaluation(
        ctx=ctx,
        index_name="test-index",
        queries="action movies"
    ))
    
    assert result["index_name"] == "test-index"
    assert result["queries_evaluated"] == 1
    assert len(result["evaluations"]) == 1
    
    evaluation = result["evaluations"][0]
    assert evaluation["query"] == "action movies"
    assert evaluation["total_results"] == 2
    assert len(evaluation["results"]) == 2
    print("✓ Basic evaluation test passed")


@patch('opensearch_orchestrator.scripts.opensearch_ops_tools._search_ui_search')
@patch('opensearch_orchestrator.scripts.opensearch_ops_tools._search_ui_suggestions')
def test_search_relevance_evaluation_multiple_queries(mock_suggestions, mock_search):
    """Test evaluation with multiple queries."""
    print("Testing search_relevance_evaluation with multiple queries...")
    import asyncio
    from opensearch_orchestrator.mcp_server import search_relevance_evaluation
    
    # Mock MCP context
    class _DummyContext:
        def __init__(self):
            self.session = None
    
    ctx = _DummyContext()
    
    mock_search.return_value = {
        "error": "",
        "hits": [{"id": "doc1", "score": 1.5, "preview": "test", "source": {}}],
        "capability": "semantic",
        "query_mode": "hybrid"
    }
    
    mock_suggestions.return_value = {"suggestions": []}
    
    result = asyncio.run(search_relevance_evaluation(
        ctx=ctx,
        index_name="test-index",
        queries="query1, query2, query3"
    ))
    
    assert result["queries_evaluated"] == 3
    assert len(result["evaluations"]) == 3
    print("✓ Multiple queries test passed")


def test_search_relevance_evaluation_missing_params():
    """Test evaluation with missing parameters."""
    print("Testing search_relevance_evaluation with missing parameters...")
    import asyncio
    from opensearch_orchestrator.mcp_server import search_relevance_evaluation
    
    # Mock MCP context
    class _DummyContext:
        def __init__(self):
            self.session = None
    
    ctx = _DummyContext()
    
    result = asyncio.run(search_relevance_evaluation(
        ctx=ctx,
        index_name="",
        queries="test"
    ))
    assert "error" in result
    assert "index_name is required" in result["error"]
    
    result = asyncio.run(search_relevance_evaluation(
        ctx=ctx,
        index_name="test-index",
        queries=""
    ))
    assert "error" in result
    assert "queries parameter is required" in result["error"]
    print("✓ Missing parameters test passed")


if __name__ == "__main__":
    print("\n=== Running Search Relevance Evaluation Tests ===\n")
    
    try:
        test_set_search_relevance_scores_valid()
        test_set_search_relevance_scores_invalid_json()
        test_set_search_relevance_scores_invalid_score()
        test_set_search_relevance_scores_missing_index()
        test_set_search_relevance_scores_not_dict()
        test_search_relevance_evaluation_basic()
        test_search_relevance_evaluation_multiple_queries()
        test_search_relevance_evaluation_missing_params()
        
        print("\n=== All tests passed! ✓ ===\n")
    except AssertionError as e:
        print(f"\n✗ Test failed: {e}\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}\n")
        sys.exit(1)
