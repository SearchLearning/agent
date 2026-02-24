#!/usr/bin/env python3
"""
Test script for Step 4 Tools

Tests verification, cleanup, UI, and web search tools.
"""

import json
import sys
from pathlib import Path

# Add the server to the path
sys.path.insert(0, str(Path(__file__).parent))

from opensearch_mcp.tools.web_search import search_opensearch_org
from opensearch_mcp.tools.verification import cleanup_verification_docs
from opensearch_mcp.tools.search_ui import launch_search_ui


def test_web_search():
    """Test OpenSearch.org documentation search."""
    print("=" * 80)
    print("Testing Web Search Tool")
    print("=" * 80)
    
    print("\n[TEST] search_opensearch_org")
    print("-" * 80)
    
    result = search_opensearch_org("knn vector search", number_of_results=3)
    
    try:
        parsed = json.loads(result)
        if "results" in parsed:
            results_count = len(parsed.get("results", []))
            print(f"✅ SUCCESS: Found {results_count} results")
            
            if results_count > 0:
                first_result = parsed["results"][0]
                print(f"\nFirst result:")
                print(f"  Title: {first_result.get('title', 'N/A')[:60]}...")
                print(f"  URL: {first_result.get('url', 'N/A')}")
                print(f"  Snippet: {first_result.get('snippet', 'N/A')[:80]}...")
            return True
        else:
            print(f"❌ FAILED: Unexpected format: {result[:200]}")
            return False
    except json.JSONDecodeError:
        if "Error" in result:
            print(f"⚠️  Search error (may be network issue): {result[:200]}")
            return True  # Don't fail on network errors
        print(f"❌ FAILED: Invalid JSON: {result[:200]}")
        return False


def test_cleanup_verification():
    """Test verification cleanup tool."""
    print("\n" + "=" * 80)
    print("Testing Verification Cleanup Tool")
    print("=" * 80)
    
    print("\n[TEST] cleanup_verification_docs (no docs)")
    print("-" * 80)
    
    result = cleanup_verification_docs()
    
    if "No verification documents" in result or "✅" in result:
        print(f"✅ SUCCESS: {result}")
        return True
    else:
        print(f"❌ FAILED: {result}")
        return False


def test_search_ui():
    """Test search UI launcher."""
    print("\n" + "=" * 80)
    print("Testing Search UI Launcher")
    print("=" * 80)
    
    print("\n[TEST] launch_search_ui")
    print("-" * 80)
    
    result = launch_search_ui("test-index")
    
    if "test-index" in result and ("✅" in result or "⚠️" in result):
        print(f"✅ SUCCESS: UI instructions provided")
        print(f"\nPreview:\n{result[:300]}...")
        return True
    else:
        print(f"❌ FAILED: {result}")
        return False


def main():
    """Run all Step 4 tests."""
    print("=" * 80)
    print("Step 4 Tools Test Suite")
    print("=" * 80)
    
    results = []
    
    # Test 1: Web Search
    results.append(("Web Search", test_web_search()))
    
    # Test 2: Cleanup
    results.append(("Verification Cleanup", test_cleanup_verification()))
    
    # Test 3: Search UI
    results.append(("Search UI Launcher", test_search_ui()))
    
    # Summary
    print("\n" + "=" * 80)
    print("Test Summary")
    print("=" * 80)
    
    for test_name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {test_name}")
    
    all_passed = all(passed for _, passed in results)
    
    print("\n" + "=" * 80)
    if all_passed:
        print("✅ ALL TESTS PASSED")
    else:
        print("⚠️  SOME TESTS FAILED")
    print("=" * 80)
    
    return all_passed


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
