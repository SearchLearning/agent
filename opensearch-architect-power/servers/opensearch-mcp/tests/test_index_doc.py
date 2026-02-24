"""
Test script for index_doc tool.

This script tests the index_doc tool which indexes a single document
into an OpenSearch index.
"""

import sys
import json
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from opensearch_mcp.tools.opensearch_ops import index_doc
from opensearch_mcp.tools.opensearch_client import get_opensearch_client


def test_index_doc():
    """Test indexing a single document."""
    print("=" * 80)
    print("Testing index_doc Tool")
    print("=" * 80)
    
    # Test document
    test_doc = {
        "title": "Test Product",
        "description": "This is a test product for the index_doc tool",
        "price": 99.99,
        "category": "Electronics"
    }
    
    index_name = "test-index-doc"
    doc_id = "test-doc-1"
    
    print(f"\n[TEST] Index document '{doc_id}' into '{index_name}'")
    print("-" * 80)
    print(f"Document: {json.dumps(test_doc, indent=2)}")
    print()
    
    # Index the document
    result = index_doc(index_name, json.dumps(test_doc), doc_id)
    
    print("Result:")
    print(result)
    print()
    
    # Check if successful
    if "Error" in result:
        print("❌ FAILED: Error indexing document")
        return False
    
    # Try to parse the result as JSON (should be the retrieved document)
    try:
        retrieved_doc = json.loads(result)
        if "_source" in retrieved_doc:
            print("✅ SUCCESS: Document indexed and retrieved")
            print(f"Document ID: {retrieved_doc.get('_id')}")
            print(f"Index: {retrieved_doc.get('_index')}")
            print(f"Source fields: {list(retrieved_doc['_source'].keys())}")
            
            # Cleanup: delete the test index
            try:
                client = get_opensearch_client()
                client.indices.delete(index=index_name, ignore=[404])
                print(f"\n✅ Cleaned up test index '{index_name}'")
            except Exception as e:
                print(f"\n⚠️  Could not clean up test index: {e}")
            
            return True
        else:
            print("⚠️  WARNING: Unexpected response format")
            return False
    except json.JSONDecodeError:
        # Result might be a success message instead of JSON
        if "✅" in result or "successfully" in result.lower():
            print("✅ SUCCESS: Document indexed")
            
            # Cleanup
            try:
                client = get_opensearch_client()
                client.indices.delete(index=index_name, ignore=[404])
                print(f"\n✅ Cleaned up test index '{index_name}'")
            except Exception as e:
                print(f"\n⚠️  Could not clean up test index: {e}")
            
            return True
        else:
            print("❌ FAILED: Could not parse result")
            return False


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("index_doc Tool Test Suite")
    print("=" * 80)
    print("\nNote: This test requires OpenSearch to be running.")
    print("The tool will attempt to auto-start a local Docker container if needed.")
    print()
    
    try:
        success = test_index_doc()
        
        print("\n" + "=" * 80)
        if success:
            print("🎉 TEST PASSED")
        else:
            print("❌ TEST FAILED")
        print("=" * 80)
        
        sys.exit(0 if success else 1)
    
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
