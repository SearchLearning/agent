#!/usr/bin/env python3
"""
Test script for OpenSearch Operations Tools

Tests index creation, pipeline management, and model deployment.
Note: Requires OpenSearch to be running (will auto-start if local).
"""

import json
import sys
from pathlib import Path

# Add the server to the path
sys.path.insert(0, str(Path(__file__).parent))

from opensearch_mcp.tools.opensearch_ops import (
    create_index,
    create_and_attach_pipeline,
    delete_doc,
)
from opensearch_mcp.tools.opensearch_client import get_opensearch_client


def test_opensearch_connection():
    """Test OpenSearch connection."""
    print("=" * 80)
    print("Testing OpenSearch Connection")
    print("=" * 80)
    
    try:
        client = get_opensearch_client()
        info = client.info()
        version = info.get("version", {}).get("number", "unknown")
        print(f"✅ Connected to OpenSearch {version}")
        print(f"Cluster: {info.get('cluster_name', 'unknown')}")
        return True
    except Exception as e:
        print(f"❌ FAILED to connect: {e}")
        print("\nNote: OpenSearch must be running for these tests.")
        print("The tool will attempt to auto-start a local Docker container.")
        return False


def test_create_index():
    """Test index creation."""
    print("\n" + "=" * 80)
    print("Testing Index Creation")
    print("=" * 80)
    
    # Test index configuration
    test_index = "test-mcp-index"
    index_body = json.dumps({
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0
        },
        "mappings": {
            "properties": {
                "title": {"type": "text"},
                "description": {"type": "text"},
                "price": {"type": "integer"}
            }
        }
    })
    
    # Clean up if exists
    try:
        client = get_opensearch_client()
        if client.indices.exists(index=test_index):
            client.indices.delete(index=test_index)
            print(f"Cleaned up existing index '{test_index}'")
    except Exception:
        pass
    
    # Create index
    print(f"\n[TEST] Creating index '{test_index}'")
    print("-" * 80)
    result = create_index(test_index, index_body)
    
    if "✅" in result:
        print(result)
        
        # Verify it exists
        try:
            client = get_opensearch_client()
            if client.indices.exists(index=test_index):
                print("✅ Verified: Index exists")
                return test_index
            else:
                print("❌ FAILED: Index not found after creation")
                return None
        except Exception as e:
            print(f"❌ FAILED to verify: {e}")
            return None
    else:
        print(f"❌ FAILED: {result}")
        return None


def test_create_pipeline(index_name: str):
    """Test pipeline creation."""
    print("\n" + "=" * 80)
    print("Testing Pipeline Creation")
    print("=" * 80)
    
    if not index_name:
        print("⚠️  Skipping: No index available")
        return False
    
    # Simple ingest pipeline (no model required)
    pipeline_id = "test-mcp-pipeline"
    pipeline_body = json.dumps({
        "description": "Test pipeline",
        "processors": [
            {
                "set": {
                    "field": "processed",
                    "value": True
                }
            }
        ]
    })
    
    print(f"\n[TEST] Creating ingest pipeline '{pipeline_id}'")
    print("-" * 80)
    result = create_and_attach_pipeline(
        index_name=index_name,
        pipeline_type="ingest",
        pipeline_id=pipeline_id,
        pipeline_body=pipeline_body
    )
    
    if "✅" in result:
        print(result)
        return True
    else:
        print(f"❌ FAILED: {result}")
        return False


def test_delete_doc(index_name: str):
    """Test document deletion."""
    print("\n" + "=" * 80)
    print("Testing Document Deletion")
    print("=" * 80)
    
    if not index_name:
        print("⚠️  Skipping: No index available")
        return False
    
    # Index a test document first
    try:
        client = get_opensearch_client()
        doc_id = "test-doc-1"
        client.index(
            index=index_name,
            id=doc_id,
            body={"title": "Test", "price": 100}
        )
        client.indices.refresh(index=index_name)
        print(f"Indexed test document '{doc_id}'")
    except Exception as e:
        print(f"⚠️  Could not index test document: {e}")
        return False
    
    # Delete it
    print(f"\n[TEST] Deleting document '{doc_id}'")
    print("-" * 80)
    result = delete_doc(index_name, doc_id)
    
    if "✅" in result:
        print(result)
        return True
    else:
        print(f"❌ FAILED: {result}")
        return False


def cleanup(index_name: str):
    """Clean up test resources."""
    print("\n" + "=" * 80)
    print("Cleanup")
    print("=" * 80)
    
    if not index_name:
        print("Nothing to clean up")
        return
    
    try:
        client = get_opensearch_client()
        if client.indices.exists(index=index_name):
            client.indices.delete(index=index_name)
            print(f"✅ Deleted test index '{index_name}'")
    except Exception as e:
        print(f"⚠️  Cleanup warning: {e}")


def main():
    """Run all tests."""
    print("=" * 80)
    print("OpenSearch Operations Tools Test Suite")
    print("=" * 80)
    
    # Test 1: Connection
    if not test_opensearch_connection():
        print("\n❌ Cannot proceed without OpenSearch connection")
        return False
    
    # Test 2: Create Index
    test_index = test_create_index()
    if not test_index:
        print("\n❌ Index creation failed, skipping remaining tests")
        return False
    
    # Test 3: Create Pipeline
    pipeline_ok = test_create_pipeline(test_index)
    
    # Test 4: Delete Document
    delete_ok = test_delete_doc(test_index)
    
    # Cleanup
    cleanup(test_index)
    
    # Summary
    print("\n" + "=" * 80)
    if pipeline_ok and delete_ok:
        print("✅ ALL TESTS PASSED")
        print("=" * 80)
        return True
    else:
        print("⚠️  SOME TESTS FAILED")
        print("=" * 80)
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
