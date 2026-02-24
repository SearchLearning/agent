#!/usr/bin/env python3
"""
Test MCP Protocol

Verifies that the MCP server responds correctly to protocol messages.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from opensearch_mcp.server import app


async def test_list_tools():
    """Test that list_tools returns the expected tools."""
    print("Testing MCP Protocol - list_tools")
    print("=" * 80)
    
    try:
        # Get the list_tools handler
        tools = await app._tool_manager.list_tools()
        
        print(f"\n✅ Found {len(tools)} tools:")
        for i, tool in enumerate(tools, 1):
            print(f"  {i}. {tool.name}")
            print(f"     Description: {tool.description[:80]}...")
        
        # Verify we have all 17 tools
        expected_tools = [
            "read_knowledge_base",
            "read_dense_vector_models",
            "read_sparse_vector_models",
            "get_sample_doc",
            "submit_sample_doc",
            "submit_sample_doc_from_local_file",
            "submit_sample_doc_from_url",
            "clear_sample_doc",
            "create_index",
            "create_and_attach_pipeline",
            "create_bedrock_embedding_model",
            "create_local_pretrained_model",
            "delete_doc",
            "apply_capability_driven_verification",
            "cleanup_verification_docs",
            "launch_search_ui",
            "search_opensearch_org",
        ]
        
        tool_names = [tool.name for tool in tools]
        
        print(f"\n{'=' * 80}")
        print("Verification:")
        print(f"  Expected: {len(expected_tools)} tools")
        print(f"  Found: {len(tools)} tools")
        
        missing = set(expected_tools) - set(tool_names)
        extra = set(tool_names) - set(expected_tools)
        
        if missing:
            print(f"  ❌ Missing tools: {missing}")
        if extra:
            print(f"  ⚠️  Extra tools: {extra}")
        
        if not missing and not extra:
            print(f"  ✅ All tools present and accounted for!")
            return True
        else:
            return False
            
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_call_tool():
    """Test calling a simple tool."""
    print("\n" + "=" * 80)
    print("Testing MCP Protocol - call_tool")
    print("=" * 80)
    
    try:
        # Test read_knowledge_base (simple, no args)
        result = await app._tool_manager.call_tool(
            "read_knowledge_base",
            {}
        )
        
        if result and len(result) > 0:
            content = result[0].text
            print(f"\n✅ Tool call successful!")
            print(f"   Returned {len(content)} characters")
            print(f"   Preview: {content[:100]}...")
            return True
        else:
            print(f"❌ Tool call returned empty result")
            return False
            
    except Exception as e:
        print(f"❌ Error calling tool: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all tests."""
    print("MCP Protocol Test Suite")
    print("=" * 80)
    
    test1 = await test_list_tools()
    test2 = await test_call_tool()
    
    print("\n" + "=" * 80)
    if test1 and test2:
        print("✅ ALL MCP PROTOCOL TESTS PASSED")
        print("=" * 80)
        print("\nThe server is working correctly.")
        print("If Kiro can't see the tools, the issue is with:")
        print("  1. MCP version mismatch between Kiro and server")
        print("  2. Communication protocol (stdio) not working")
        print("  3. Kiro not calling list_tools during initialization")
        return True
    else:
        print("❌ SOME TESTS FAILED")
        print("=" * 80)
        return False


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
