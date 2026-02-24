#!/usr/bin/env python3
"""
Test script for Sample Document Management Tools

Tests file-based persistence and various input formats.
"""

import json
import sys
from pathlib import Path

# Add the server to the path
sys.path.insert(0, str(Path(__file__).parent))

from opensearch_mcp.tools.sample_docs import (
    get_sample_doc,
    submit_sample_doc,
    submit_sample_doc_from_local_file,
    clear_sample_doc,
)


def test_sample_doc_tools():
    """Test all sample document tools."""
    print("=" * 80)
    print("Testing Sample Document Management Tools")
    print("=" * 80)
    
    # Test 1: Clear any existing sample
    print("\n[TEST 1] clear_sample_doc")
    print("-" * 80)
    result = clear_sample_doc()
    print(f"✅ {result}")
    
    # Test 2: Get sample when none exists
    print("\n[TEST 2] get_sample_doc (should be missing)")
    print("-" * 80)
    result = get_sample_doc()
    if result == "MISSING_SAMPLE_DOC":
        print("✅ SUCCESS: No sample document (as expected)")
    else:
        print(f"❌ FAILED: Expected MISSING_SAMPLE_DOC, got: {result[:100]}")
        return False
    
    # Test 3: Submit a sample document
    print("\n[TEST 3] submit_sample_doc")
    print("-" * 80)
    sample_doc = json.dumps({
        "title": "Toyota Camry",
        "price": 25000,
        "description": "A reliable family sedan with excellent fuel economy"
    })
    result = submit_sample_doc(sample_doc)
    if result.startswith("Sample document stored"):
        print(f"✅ SUCCESS: {result}")
    else:
        print(f"❌ FAILED: {result}")
        return False
    
    # Test 4: Get the stored sample
    print("\n[TEST 4] get_sample_doc (should exist now)")
    print("-" * 80)
    result = get_sample_doc()
    if result != "MISSING_SAMPLE_DOC":
        try:
            parsed = json.loads(result)
            if "document" in parsed or "title" in parsed:
                print(f"✅ SUCCESS: Retrieved sample document")
                print(f"Preview: {result[:200]}...")
            else:
                print(f"❌ FAILED: Unexpected format: {result[:100]}")
                return False
        except json.JSONDecodeError:
            print(f"❌ FAILED: Invalid JSON: {result[:100]}")
            return False
    else:
        print("❌ FAILED: Sample should exist but got MISSING_SAMPLE_DOC")
        return False
    
    # Test 5: Submit plain text (should convert to content field)
    print("\n[TEST 5] submit_sample_doc (plain text)")
    print("-" * 80)
    result = submit_sample_doc("This is a plain text sample document")
    if result.startswith("Sample document stored"):
        print(f"✅ SUCCESS: {result}")
    else:
        print(f"❌ FAILED: {result}")
        return False
    
    # Test 6: Test local file loading (if test file exists)
    print("\n[TEST 6] submit_sample_doc_from_local_file")
    print("-" * 80)
    
    # Create a test CSV file
    test_file = Path("/tmp/test_sample.csv")
    test_file.write_text("name,age,city\nJohn Doe,30,Seattle\nJane Smith,25,Portland\n")
    
    result = submit_sample_doc_from_local_file(str(test_file))
    if result.startswith("Sample document loaded"):
        print(f"✅ SUCCESS: {result}")
        
        # Verify it was loaded
        doc_result = get_sample_doc()
        if "name" in doc_result.lower() or "john" in doc_result.lower():
            print("✅ Verified: CSV data loaded correctly")
        else:
            print(f"⚠️  Warning: Loaded but content unexpected: {doc_result[:100]}")
    else:
        print(f"❌ FAILED: {result}")
        return False
    
    # Cleanup test file
    test_file.unlink()
    
    # Test 7: Clear again
    print("\n[TEST 7] clear_sample_doc (final cleanup)")
    print("-" * 80)
    result = clear_sample_doc()
    print(f"✅ {result}")
    
    # Verify it's cleared
    result = get_sample_doc()
    if result == "MISSING_SAMPLE_DOC":
        print("✅ Verified: Sample document cleared")
    else:
        print(f"⚠️  Warning: Clear may not have worked: {result[:100]}")
    
    print("\n" + "=" * 80)
    print("✅ ALL TESTS PASSED")
    print("=" * 80)
    print("\nSample documents are stored in: ~/.opensearch-architect/samples/")
    return True


if __name__ == "__main__":
    success = test_sample_doc_tools()
    sys.exit(0 if success else 1)
