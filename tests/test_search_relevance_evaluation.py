"""Tests for search relevance evaluation feature."""

import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from opensearch_orchestrator.scripts.opensearch_ops_tools import (
    set_search_relevance_scores,
    _search_ui,
    _search_ui_search,
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


@patch('opensearch_orchestrator.scripts.opensearch_ops_tools._evaluate_relevance_with_llm')
@patch('opensearch_orchestrator.scripts.opensearch_ops_tools._search_ui_search')
@patch('opensearch_orchestrator.scripts.opensearch_ops_tools._search_ui_suggestions')
def test_search_relevance_evaluation_basic(mock_suggestions, mock_search, mock_evaluate_llm):
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

    # Mock LLM evaluation - doc1 is relevant, doc2 is not
    async def mock_llm_eval(query, doc_source, ctx):
        if doc_source.get("title") == "Action Movie":
            return 1
        return 0

    mock_evaluate_llm.side_effect = mock_llm_eval

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


@patch('opensearch_orchestrator.scripts.opensearch_ops_tools._evaluate_relevance_with_llm')
@patch('opensearch_orchestrator.scripts.opensearch_ops_tools._search_ui_search')
@patch('opensearch_orchestrator.scripts.opensearch_ops_tools._search_ui_suggestions')
def test_search_relevance_evaluation_multiple_queries(mock_suggestions, mock_search, mock_evaluate_llm):
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

    # Mock LLM evaluation to return relevant
    async def mock_llm_eval(query, doc_source, ctx):
        return 1

    mock_evaluate_llm.side_effect = mock_llm_eval

    result = asyncio.run(search_relevance_evaluation(
        ctx=ctx,
        index_name="test-index",
        queries="query1, query2, query3"
    ))

    assert result["queries_evaluated"] == 3
    assert len(result["evaluations"]) == 3
    print("✓ Multiple queries test passed")


@patch('opensearch_orchestrator.scripts.opensearch_ops_tools._search_ui_suggestions')
def test_search_relevance_evaluation_missing_params(mock_suggestions):
    """Test evaluation with missing parameters."""
    print("Testing search_relevance_evaluation with missing parameters...")
    import asyncio
    from opensearch_orchestrator.mcp_server import search_relevance_evaluation
    
    # Mock MCP context
    class _DummyContext:
        def __init__(self):
            self.session = None
    
    ctx = _DummyContext()
    
    # Test missing index_name
    result = asyncio.run(search_relevance_evaluation(
        ctx=ctx,
        index_name="",
        queries="test"
    ))
    assert "error" in result
    assert "index_name is required" in result["error"]
    
    # Test empty queries with no suggestions available
    mock_suggestions.return_value = ([], [])  # No suggestions available
    result = asyncio.run(search_relevance_evaluation(
        ctx=ctx,
        index_name="test-index",
        queries=""
    ))
    assert "error" in result
    assert "No auto-generated suggestions found" in result["error"]
    print("✓ Missing parameters test passed")

@patch('opensearch_orchestrator.scripts.opensearch_ops_tools._evaluate_relevance_with_llm')
@patch('opensearch_orchestrator.scripts.opensearch_ops_tools._search_ui_search')
def test_evaluate_single_query(mock_search, mock_evaluate_llm):
    """Test on-demand evaluation of a single query."""
    print("Testing evaluate_single_query...")
    import asyncio
    from opensearch_orchestrator.mcp_server import search_relevance_evaluation
    from opensearch_orchestrator.scripts.opensearch_ops_tools import _search_ui
    
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
                "preview": "Space adventure movie",
                "source": {"title": "Star Wars"}
            },
            {
                "id": "doc2",
                "score": 1.5,
                "preview": "Another space movie",
                "source": {"title": "Star Trek"}
            }
        ],
        "capability": "semantic",
        "query_mode": "hybrid"
    }
    
    # Mock LLM evaluation - doc1 is relevant, doc2 is not
    async def mock_llm_eval(query, doc_source, ctx):
        if doc_source.get("title") == "Star Wars":
            return 1
        return 0
    
    mock_evaluate_llm.side_effect = mock_llm_eval
    
    # Clear any existing scores
    _search_ui.relevance_scores.clear()
    
    result = asyncio.run(search_relevance_evaluation(
        ctx=ctx,
        index_name="test-index",
        queries="space opera",  # Single query
        size=20
    ))
    
    assert result["queries"] == "space opera"
    assert result["index_name"] == "test-index"
    assert "queries_evaluated" in result
    assert result["queries_evaluated"] == 1
    
    # Verify scores were stored with composite keys
    assert "test-index" in _search_ui.relevance_scores
    scores = _search_ui.relevance_scores["test-index"]
    
    # Check that composite keys were used
    assert "space opera::doc1" in scores
    assert "space opera::doc2" in scores
    assert scores["space opera::doc1"] == 1
    assert scores["space opera::doc2"] == 0
    
    print("✓ evaluate_single_query test passed")


# ============================================================================
# Composite Key and Query-Doc Isolation Tests
# ============================================================================

def test_query_doc_pair_isolation():
    """Test that the same doc can have different relevance scores for different queries."""
    print("Testing query-doc pair isolation...")
    
    index_name = "test-index"
    
    # Simulate evaluation results where doc1 is relevant for "action movies" but not for "romance"
    relevance_data = {
        "action movies::doc1": 1,  # Relevant for action movies
        "action movies::doc2": 0,
        "romance::doc1": 0,        # NOT relevant for romance
        "romance::doc3": 1,
    }
    
    result = set_search_relevance_scores(
        index_name=index_name,
        relevance_data_json=json.dumps(relevance_data)
    )
    
    assert "Set relevance scores for 4 documents" in result
    assert index_name in _search_ui.relevance_scores
    
    # Verify all scores are stored correctly
    stored_scores = _search_ui.relevance_scores[index_name]
    assert stored_scores["action movies::doc1"] == 1
    assert stored_scores["action movies::doc2"] == 0
    assert stored_scores["romance::doc1"] == 0
    assert stored_scores["romance::doc3"] == 1
    
    print("✓ Query-doc pair isolation test passed")


@patch('opensearch_orchestrator.scripts.opensearch_ops_tools._create_client')
def test_search_ui_composite_key_lookup(mock_client):
    """Test that search UI correctly looks up relevance scores using composite keys."""
    print("Testing search UI composite key lookup...")
    
    index_name = "test-index"
    
    # Set up relevance scores with composite keys
    relevance_data = {
        "action movies::doc1": 1,
        "action movies::doc2": 0,
        "romance::doc1": 0,
    }
    set_search_relevance_scores(
        index_name=index_name,
        relevance_data_json=json.dumps(relevance_data)
    )
    
    # Mock OpenSearch response
    mock_os_client = Mock()
    mock_client.return_value = mock_os_client
    mock_os_client.search.return_value = {
        "hits": {
            "hits": [
                {
                    "_id": "doc1",
                    "_score": 2.5,
                    "_source": {"title": "Action Movie", "description": "Explosions and car chases"}
                },
                {
                    "_id": "doc2",
                    "_score": 1.5,
                    "_source": {"title": "Another Action Movie", "description": "More action"}
                }
            ],
            "total": {"value": 2}
        },
        "took": 10
    }
    
    # Search with "action movies" query
    result = _search_ui_search(
        index_name=index_name,
        query_text="action movies",
        size=10,
        debug=False
    )
    
    # Verify doc1 has relevance_score=1 for "action movies"
    doc1_hit = next(h for h in result["hits"] if h["id"] == "doc1")
    assert doc1_hit["relevance_score"] == 1
    assert doc1_hit["relevance_color"] == "green"
    
    # Verify doc2 has relevance_score=0 for "action movies"
    doc2_hit = next(h for h in result["hits"] if h["id"] == "doc2")
    assert doc2_hit["relevance_score"] == 0
    assert doc2_hit["relevance_color"] == "red"
    
    # Now search with "romance" query - doc1 should have different relevance
    mock_os_client.search.return_value = {
        "hits": {
            "hits": [
                {
                    "_id": "doc1",
                    "_score": 1.0,
                    "_source": {"title": "Action Movie", "description": "Explosions and car chases"}
                }
            ],
            "total": {"value": 1}
        },
        "took": 8
    }
    
    result = _search_ui_search(
        index_name=index_name,
        query_text="romance",
        size=10,
        debug=False
    )
    
    # Verify doc1 has relevance_score=0 for "romance" (different from action movies)
    doc1_hit = result["hits"][0]
    assert doc1_hit["id"] == "doc1"
    assert doc1_hit["relevance_score"] == 0
    assert doc1_hit["relevance_color"] == "red"
    
    print("✓ Search UI composite key lookup test passed")




def test_ui_state_concurrent_updates():
    """Test that concurrent updates to suggestions and relevance scores don't overwrite each other."""
    print("Testing UI state concurrent updates...")
    
    from opensearch_orchestrator.scripts.opensearch_ops_tools import set_search_ui_suggestions
    
    index_name = "test-index"
    
    # First, set suggestions
    suggestions = [
        {"text": "action movies", "capability": "semantic"},
        {"text": "comedy films", "capability": "semantic"}
    ]
    result1 = set_search_ui_suggestions(
        index_name=index_name,
        suggestion_meta_json=json.dumps(suggestions)
    )
    assert "Set 2 suggestions" in result1
    
    # Then, set relevance scores
    relevance_data = {
        "action movies::doc1": 1,
        "action movies::doc2": 0,
    }
    result2 = set_search_relevance_scores(
        index_name=index_name,
        relevance_data_json=json.dumps(relevance_data)
    )
    assert "Set relevance scores for 2 documents" in result2
    
    # Verify both are still present (not overwritten)
    assert index_name in _search_ui.suggestion_meta_by_index
    assert len(_search_ui.suggestion_meta_by_index[index_name]) == 2
    
    assert index_name in _search_ui.relevance_scores
    assert len(_search_ui.relevance_scores[index_name]) == 2
    
    # Now set suggestions again - relevance scores should still be there
    new_suggestions = [
        {"text": "thriller movies", "capability": "semantic"}
    ]
    result3 = set_search_ui_suggestions(
        index_name=index_name,
        suggestion_meta_json=json.dumps(new_suggestions)
    )
    assert "Set 1 suggestions" in result3
    
    # Verify relevance scores weren't lost
    assert index_name in _search_ui.relevance_scores
    assert len(_search_ui.relevance_scores[index_name]) == 2
    assert _search_ui.relevance_scores[index_name]["action movies::doc1"] == 1
    
    print("✓ UI state concurrent updates test passed")


# ============================================================================
# No Heuristic Scoring Tests
# ============================================================================

@patch('opensearch_orchestrator.scripts.opensearch_ops_tools._create_client')
def test_no_heuristic_for_unevaluated_query(mock_client):
    """Test that queries without pre-computed scores show NO relevance badges."""
    print("Testing no heuristic scores for unevaluated queries...")
    
    index_name = "test-index"
    
    # Set up scores for "action movies" only
    relevance_data = {
        "action movies::doc1": 1,
        "action movies::doc2": 0,
    }
    set_search_relevance_scores(
        index_name=index_name,
        relevance_data_json=json.dumps(relevance_data)
    )
    
    # Mock OpenSearch response
    mock_os_client = Mock()
    mock_client.return_value = mock_os_client
    mock_os_client.search.return_value = {
        "hits": {
            "hits": [
                {
                    "_id": "doc1",
                    "_score": 2.5,
                    "_source": {"title": "Space Movie"}
                },
                {
                    "_id": "doc3",
                    "_score": 1.8,
                    "_source": {"title": "Another Space Movie"}
                }
            ],
            "total": {"value": 2}
        },
        "took": 10
    }
    
    # Search with a DIFFERENT query that has no pre-computed scores
    result = _search_ui_search(
        index_name=index_name,
        query_text="space opera",  # Different from "action movies"
        size=10,
        debug=False
    )
    
    # Verify NO relevance scores are shown (no heuristic)
    for hit in result["hits"]:
        # Should NOT have relevance_score or relevance_color keys
        assert "relevance_score" not in hit, f"Found unexpected relevance_score in {hit}"
        assert "relevance_color" not in hit, f"Found unexpected relevance_color in {hit}"
    
    print("✓ No heuristic scores test passed - unevaluated queries show no badges")


@patch('opensearch_orchestrator.scripts.opensearch_ops_tools._create_client')
def test_llm_scores_are_shown(mock_client):
    """Test that queries WITH pre-computed scores DO show relevance badges."""
    print("Testing LLM scores are shown for evaluated queries...")
    
    index_name = "test-index"
    
    # Set up scores for "action movies"
    relevance_data = {
        "action movies::doc1": 1,
        "action movies::doc2": 0,
    }
    set_search_relevance_scores(
        index_name=index_name,
        relevance_data_json=json.dumps(relevance_data)
    )
    
    # Mock OpenSearch response
    mock_os_client = Mock()
    mock_client.return_value = mock_os_client
    mock_os_client.search.return_value = {
        "hits": {
            "hits": [
                {
                    "_id": "doc1",
                    "_score": 2.5,
                    "_source": {"title": "Action Movie"}
                },
                {
                    "_id": "doc2",
                    "_score": 1.5,
                    "_source": {"title": "Another Action Movie"}
                }
            ],
            "total": {"value": 2}
        },
        "took": 10
    }
    
    # Search with the query that HAS pre-computed scores
    result = _search_ui_search(
        index_name=index_name,
        query_text="action movies",
        size=10,
        debug=False
    )
    
    # Verify relevance scores ARE shown
    doc1_hit = next(h for h in result["hits"] if h["id"] == "doc1")
    assert doc1_hit["relevance_score"] == 1
    assert doc1_hit["relevance_color"] == "green"
    
    doc2_hit = next(h for h in result["hits"] if h["id"] == "doc2")
    assert doc2_hit["relevance_score"] == 0
    assert doc2_hit["relevance_color"] == "red"
    
    print("✓ LLM scores shown test passed - evaluated queries show badges")


@patch('opensearch_orchestrator.scripts.opensearch_ops_tools._create_client')
def test_mixed_evaluated_and_unevaluated_docs(mock_client):
    """Test that some docs have badges (evaluated) and some don't (not evaluated)."""
    print("Testing mixed evaluated and unevaluated documents...")
    
    index_name = "test-index"
    
    # Set up scores for only doc1 and doc2
    relevance_data = {
        "action movies::doc1": 1,
        "action movies::doc2": 0,
        # doc3 has no score
    }
    set_search_relevance_scores(
        index_name=index_name,
        relevance_data_json=json.dumps(relevance_data)
    )
    
    # Mock OpenSearch response with 3 docs
    mock_os_client = Mock()
    mock_client.return_value = mock_os_client
    mock_os_client.search.return_value = {
        "hits": {
            "hits": [
                {"_id": "doc1", "_score": 2.5, "_source": {"title": "Movie 1"}},
                {"_id": "doc2", "_score": 1.5, "_source": {"title": "Movie 2"}},
                {"_id": "doc3", "_score": 1.0, "_source": {"title": "Movie 3"}},
            ],
            "total": {"value": 3}
        },
        "took": 10
    }
    
    result = _search_ui_search(
        index_name=index_name,
        query_text="action movies",
        size=10,
        debug=False
    )
    
    # doc1 should have green badge
    doc1_hit = next(h for h in result["hits"] if h["id"] == "doc1")
    assert doc1_hit["relevance_score"] == 1
    assert doc1_hit["relevance_color"] == "green"
    
    # doc2 should have red badge
    doc2_hit = next(h for h in result["hits"] if h["id"] == "doc2")
    assert doc2_hit["relevance_score"] == 0
    assert doc2_hit["relevance_color"] == "red"
    
    # doc3 should have NO badge
    doc3_hit = next(h for h in result["hits"] if h["id"] == "doc3")
    assert "relevance_score" not in doc3_hit
    assert "relevance_color" not in doc3_hit
    
    print("✓ Mixed evaluated/unevaluated test passed")


if __name__ == "__main__":
    print("\n=== Running Search Relevance Evaluation Tests ===\n")
    
    try:
        # Basic functionality tests
        test_set_search_relevance_scores_valid()
        test_set_search_relevance_scores_invalid_json()
        test_set_search_relevance_scores_invalid_score()
        test_set_search_relevance_scores_missing_index()
        test_set_search_relevance_scores_not_dict()
        test_search_relevance_evaluation_basic()
        test_search_relevance_evaluation_multiple_queries()
        test_search_relevance_evaluation_missing_params()
        test_evaluate_single_query()
        
        # Composite key and isolation tests
        test_query_doc_pair_isolation()
        test_search_ui_composite_key_lookup()
        test_ui_state_concurrent_updates()
        
        # No heuristic scoring tests
        test_no_heuristic_for_unevaluated_query()
        test_llm_scores_are_shown()
        test_mixed_evaluated_and_unevaluated_docs()
        
        print("\n=== All tests passed! ✓ ===\n")
    except AssertionError as e:
        print(f"\n✗ Test failed: {e}\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}\n")
        sys.exit(1)

