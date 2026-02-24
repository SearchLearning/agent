#!/bin/bash
# Clean up and organize opensearch-architect-power directory structure

echo "Cleaning up OpenSearch Architect Power directory structure..."

# Remove empty tools directory at wrong level
if [ -d "servers/opensearch-mcp/tools" ]; then
    echo "Removing empty tools/ directory..."
    rmdir servers/opensearch-mcp/tools 2>/dev/null && echo "  ✅ Removed empty tools/" || echo "  ⚠️  Could not remove (may not be empty)"
fi

# Remove docs directory (all STEP files consolidated into DEVELOPMENT.md)
if [ -d "docs" ]; then
    echo "Removing docs/ directory (consolidated into DEVELOPMENT.md)..."
    rm -rf docs
    echo "  ✅ Removed docs/"
fi

# Create steering directory if it doesn't exist
if [ ! -d "steering" ]; then
    echo "Creating steering/ directory..."
    mkdir -p steering
    echo "  ✅ Created steering/"
fi

echo ""
echo "✅ Cleanup complete!"
echo ""
echo "Current structure:"
echo "  opensearch-architect-power/"
echo "  ├── POWER.md                    # Main documentation"
echo "  ├── README.md                   # Quick start"
echo "  ├── mcp.json                    # MCP configuration"
echo "  ├── TOOLS_REFERENCE.md          # Tool documentation"
echo "  ├── DEVELOPMENT.md              # Development history"
echo "  ├── STRUCTURE.md                # Structure explanation"
echo "  ├── steering/                   # Workflow guides (to be created)"
echo "  └── servers/                    # MCP server"
echo "      └── opensearch-mcp/"
echo "          ├── opensearch_mcp/     # Python package"
echo "          │   ├── server.py"
echo "          │   └── tools/          # 7 tool modules"
echo "          ├── knowledge/          # Documentation files"
echo "          └── test_*.py           # Test scripts"
echo ""
echo "Next steps:"
echo "  1. Create steering files: cd steering && ..."
echo "  2. Test with Kiro"
echo "  3. Ready for distribution!"

