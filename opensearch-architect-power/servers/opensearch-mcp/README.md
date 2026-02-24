# OpenSearch Architect MCP Server

MCP server providing tools for OpenSearch semantic search architecture design and implementation.

## Installation

```bash
cd opensearch-architect-power/servers/opensearch-mcp
pip install -e .
```

## Testing

### Local Testing (without MCP)
```bash
python test_server.py
```

### Testing with MCP Inspector
```bash
# Install MCP inspector if not already installed
npm install -g @modelcontextprotocol/inspector

# Run the inspector
mcp-inspector python -m opensearch_mcp.server
```

### Testing with Kiro

1. Copy `opensearch-architect-power/mcp.json` to your workspace `.kiro/settings/mcp.json`
2. Restart Kiro or reconnect the MCP server
3. Use Kiro Powers interface to activate the power
4. Test with: "Read the knowledge base about dense vector search"

## Development

### Project Structure
```
opensearch-mcp/
├── opensearch_mcp/
│   ├── __init__.py
│   ├── server.py          # Main MCP server
│   └── tools/
│       ├── __init__.py
│       └── knowledge_base.py  # Knowledge base tools
├── knowledge/             # Documentation files
│   ├── opensearch_semantic_search_guide.md
│   ├── dense_vector_models.md
│   └── sparse_vector_models.md
├── pyproject.toml
├── test_server.py
└── README.md
```

### Adding New Tools

1. Create tool implementation in `opensearch_mcp/tools/`
2. Add tool definition to `list_tools()` in `server.py`
3. Add tool handler to `call_tool()` in `server.py`
4. Add test cases to `test_server.py`

## Current Tools

### Knowledge Base Tools (Read-Only)
- `read_knowledge_base` - Main semantic search guide
- `read_dense_vector_models` - Dense vector model catalog
- `read_sparse_vector_models` - Sparse vector model catalog

## Environment Variables

```bash
OPENSEARCH_HOST=localhost
OPENSEARCH_PORT=9200
OPENSEARCH_USER=admin
OPENSEARCH_PASSWORD=myStrongPassword123!
SEARCH_UI_PORT=8765
```

## Next Steps

- [ ] Add sample document management tools
- [ ] Add OpenSearch operations tools
- [ ] Add verification and UI tools
- [ ] Add search_opensearch_org tool
