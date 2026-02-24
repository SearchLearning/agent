#!/usr/bin/env python3
"""
Test script for OpenSearch Architect MCP Server

This script tests the MCP server tools locally before integration with Kiro.
"""

import asyncio
import sys
from pathlib import Path

# Add the server to the path
sys.path.insert(0, str(Path(__file__).parent))

from opensearch_mcp.tools.knowledge_base import (
    read_knowledge_base,
    read_dense_vector_models,
    read_sparse_vector_models,
)


def test_knowledge_base_tools():
    """Test all knowledge base tools."""
    print("=" * 80)
    print("Testing OpenSearch Architect MCP Server - Knowledge Base Tools")
    print("=" * 80)
    
    # Test 1: Read Knowledge Base
    print("\n[TEST 1] read_knowledge_base")
    print("-" * 80)
    result = read_knowledge_base()
    if result.startswith("Error:"):
        print(f"❌ FAILED: {result}")
        return False
    else:
        lines = result.split('\n')
        print(f"✅ SUCCESS: Loaded {len(lines)} lines")
        print(f"Preview (first 200 chars):\n{result[:200]}...")
    
    # Test 2: Read Dense Vector Models
    print("\n[TEST 2] read_dense_vector_models")
    print("-" * 80)
    result = read_dense_vector_models()
    if result.startswith("Error:"):
        print(f"❌ FAILED: {result}")
        return False
    else:
        lines = result.split('\n')
        print(f"✅ SUCCESS: Loaded {len(lines)} lines")
        print(f"Preview (first 200 chars):\n{result[:200]}...")
    
    # Test 3: Read Sparse Vector Models
    print("\n[TEST 3] read_sparse_vector_models")
    print("-" * 80)
    result = read_sparse_vector_models()
    if result.startswith("Error:"):
        print(f"❌ FAILED: {result}")
        return False
    else:
        lines = result.split('\n')
        print(f"✅ SUCCESS: Loaded {len(lines)} lines")
        print(f"Preview (first 200 chars):\n{result[:200]}...")
    
    print("\n" + "=" * 80)
    print("✅ ALL TESTS PASSED")
    print("=" * 80)
    return True


if __name__ == "__main__":
    success = test_knowledge_base_tools()
    sys.exit(0 if success else 1)
