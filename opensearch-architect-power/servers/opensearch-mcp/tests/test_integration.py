#!/usr/bin/env python3
"""
Integration Test - Full Workflow

Tests a complete end-to-end workflow:
1. Submit sample document
2. Create index
3. Create pipeline
4. Index verification documents
5. Test search
6. Cleanup
"""

import json
import sys
from pathlib import Path

# Add the server to the path
sys.path.insert(0, str(Path(__file__).parent))

from opensearch_mcp.tools.sample_docs import submit_sample_doc, get_sample_doc, clear_sample_doc
from opensearch_mcp.tools.opensearch_ops import create_index, create_and_attach_pipeline, delete_doc
from opensearch_mcp.tools.verification import apply_capability_driven_verification, cleanup_verification_docs
from opensearch_mcp.tools.opensearch_client import get_opensearch_client


def test_full_workflow():
    """Test complete workflow from sample to search."""
    print("=" * 80)
    print("INTEGRATION TEST - Full Workflow")
    print("=" * 80)
    
    test_index = "integration-test-index"
    
    try:
        # Step 1: Submit Sample Document
        print("\n[STEP 1] Submit Sample Document")
        print("-" * 80)
        sample_doc = json.dumps({
            "title": "Toyota Camry 2024",
            "description": "A reliable family sedan with excellent fuel economy and advanced safety features",
            "price": 28000,
            "category": "sedan",
            "brand": "Toyota"
        })
        result = submit_sample_doc(sample_doc)
        print(result)
        if not result.startswith("Sample document stored"):
            print("❌ FAILED: Could not store sample document")
            return False
        
        # Verify sample was stored
        stored = get_sample_doc()
        if stored == "MISSING_SAMPLE_DOC":
            print("❌ FAILED: Sample document not found after storage")
            return False
        print("✅ Sample document stored and verified")
        
        # Step 2: Create Index
        print("\n[STEP 2] Create Index")
        print("-" * 80)
        
        # Clean up if exists
        try:
            client = get_opensearch_client()
            if client.indices.exists(index=test_index):
                client.indices.delete(index=test_index)
                print(f"Cleaned up existing index '{test_index}'")
        except Exception as e:
            print(f"⚠️  Cleanup warning: {e}")
        
        index_body = json.dumps({
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
                "index.knn": True
            },
            "mappings": {
                "properties": {
                    "title": {"type": "text"},
                    "description": {"type": "text"},
                    "price": {"type": "integer"},
                    "category": {"type": "keyword"},
                    "brand": {"type": "keyword"}
                }
            }
        })
        
        result = create_index(test_index, index_body)
        print(result)
        if "✅" not in result:
            print("❌ FAILED: Could not create index")
            return False
        print("✅ Index created successfully")
        
        # Step 3: Create Pipeline
        print("\n[STEP 3] Create Ingest Pipeline")
        print("-" * 80)
        
        pipeline_body = json.dumps({
            "description": "Test pipeline for integration test",
            "processors": [
                {
                    "set": {
                        "field": "processed",
                        "value": True
                    }
                },
                {
                    "set": {
                        "field": "indexed_at",
                        "value": "{{_ingest.timestamp}}"
                    }
                }
            ]
        })
        
        result = create_and_attach_pipeline(
            index_name=test_index,
            pipeline_type="ingest",
            pipeline_id=f"{test_index}-pipeline",
            pipeline_body=pipeline_body
        )
        print(result)
        if "✅" not in result:
            print("❌ FAILED: Could not create pipeline")
            return False
        print("✅ Pipeline created and attached")
        
        # Step 4: Index Verification Documents
        print("\n[STEP 4] Index Verification Documents")
        print("-" * 80)
        
        worker_output = """
        Search Capabilities:
        - Exact: Toyota Camry
        - Semantic: reliable family sedan
        - Structured: price range
        """
        
        result = apply_capability_driven_verification(
            worker_output=worker_output,
            index_name=test_index,
            count=5
        )
        print(result)
        
        try:
            result_json = json.loads(result)
            if not result_json.get("applied"):
                print("❌ FAILED: Verification documents not indexed")
                return False
            indexed_count = result_json.get("indexed_count", 0)
            print(f"✅ Indexed {indexed_count} verification documents")
        except json.JSONDecodeError:
            print(f"⚠️  Could not parse verification result: {result[:200]}")
            return False
        
        # Step 5: Test Search
        print("\n[STEP 5] Test Search")
        print("-" * 80)
        
        try:
            client = get_opensearch_client()
            
            # Search for documents
            search_result = client.search(
                index=test_index,
                body={
                    "query": {
                        "match_all": {}
                    }
                }
            )
            
            hits = search_result.get("hits", {}).get("total", {}).get("value", 0)
            print(f"Found {hits} documents in index")
            
            if hits == 0:
                print("❌ FAILED: No documents found in index")
                return False
            
            # Show first document
            if search_result.get("hits", {}).get("hits"):
                first_doc = search_result["hits"]["hits"][0]
                print(f"\nFirst document:")
                print(f"  ID: {first_doc['_id']}")
                print(f"  Source: {json.dumps(first_doc['_source'], indent=2)[:200]}...")
            
            print("✅ Search successful")
            
        except Exception as e:
            print(f"❌ FAILED: Search error: {e}")
            return False
        
        # Step 6: Cleanup
        print("\n[STEP 6] Cleanup")
        print("-" * 80)
        
        # Clean verification docs
        result = cleanup_verification_docs(test_index)
        print(f"Verification cleanup: {result}")
        
        # Delete index
        try:
            client = get_opensearch_client()
            if client.indices.exists(index=test_index):
                client.indices.delete(index=test_index)
                print(f"✅ Deleted test index '{test_index}'")
        except Exception as e:
            print(f"⚠️  Cleanup warning: {e}")
        
        # Clear sample doc
        clear_sample_doc()
        print("✅ Cleared sample document")
        
        print("\n" + "=" * 80)
        print("🎉 INTEGRATION TEST PASSED - Full workflow completed successfully!")
        print("=" * 80)
        return True
        
    except Exception as e:
        print(f"\n❌ INTEGRATION TEST FAILED: {e}")
        
        # Cleanup on failure
        try:
            client = get_opensearch_client()
            if client.indices.exists(index=test_index):
                client.indices.delete(index=test_index)
                print(f"Cleaned up test index '{test_index}'")
        except Exception:
            pass
        
        return False


if __name__ == "__main__":
    success = test_full_workflow()
    sys.exit(0 if success else 1)
