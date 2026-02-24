# OpenSearch Architect Power

An intelligent Kiro Power for designing and implementing OpenSearch semantic search solutions.

## Quick Start

### 1. Installation

Copy this power to your Kiro powers directory or add the MCP configuration:

```bash
# Copy mcp.json to your workspace
cp mcp.json ~/.kiro/settings/mcp.json

# Or merge with existing config
```

### 2. Install Dependencies

```bash
cd servers/opensearch-mcp
pip install -e .
```

### 3. Activate in Kiro

```
Activate the opensearch-architect power
```

## What This Power Does

- **Analyzes your data** - Submit sample documents to understand your schema
- **Recommends architectures** - Get expert advice on search strategies
- **Implements solutions** - Create indexes, pipelines, and models
- **Tests configurations** - Index verification documents and launch search UI

## Tools Included

**18 tools across 4 categories:**

1. **Knowledge Base** (3 tools) - Access OpenSearch documentation and model catalogs
2. **Sample Documents** (5 tools) - Manage sample data for analysis
3. **OpenSearch Operations** (6 tools) - Create indexes, pipelines, models, and manage documents
4. **Verification & UI** (4 tools) - Test and validate your setup

See [TOOLS_REFERENCE.md](TOOLS_REFERENCE.md) for complete documentation.

## Example Workflows

### Dense Vector Search Setup
```
1. "Load a sample from ~/data/products.csv"
2. "Read the dense vector models guide"
3. "Create an index for dense vector search with 1024 dimensions"
4. "Create a Bedrock Titan embedding model"
5. "Create an ingest pipeline with text_embedding processor"
6. "Index 10 verification documents"
```

### Hybrid Search Setup
```
1. "Submit this sample: {title: 'Product', price: 100}"
2. "Read the knowledge base about hybrid search"
3. "Create a hybrid search index with BM25 and kNN"
4. "Create a search pipeline with normalization weights [0.5, 0.5]"
```

## Directory Structure

```
opensearch-architect-power/
├── POWER.md                    # Power documentation
├── README.md                   # This file
├── mcp.json                    # MCP server configuration
├── TOOLS_REFERENCE.md          # Complete tool documentation
│
├── steering/                   # Workflow guides
│   ├── getting-started.md
│   └── dense-vector-workflow.md
│
├── docs/                       # Development documentation
│   ├── development.md          # Development history
│   ├── completion-summary.md   # Project status
│   ├── structure.md            # Directory structure
│   └── end-to-end-demo.md      # Usage example
│
├── scripts/                    # Utility scripts
│   └── organize.sh
│
└── servers/                    # MCP servers
    └── opensearch-mcp/         # Main MCP server
        ├── opensearch_mcp/     # Python package
        │   ├── server.py       # MCP server entry point
        │   └── tools/          # Tool implementations
        ├── knowledge/          # Documentation files
        ├── tests/              # Test suite
        │   ├── test_knowledge_base.py
        │   ├── test_sample_docs.py
        │   ├── test_opensearch_ops.py
        │   ├── test_verification_and_ui.py
        │   ├── test_integration.py
        │   └── test_mcp_protocol.py
        ├── pyproject.toml      # Package configuration
        └── README.md           # Server documentation
```

## Requirements

- Python 3.8+
- OpenSearch cluster (local or remote)
- Docker (for local OpenSearch auto-start)
- AWS credentials (for Bedrock models, optional)

## Environment Variables

```bash
# OpenSearch Connection
OPENSEARCH_HOST=localhost
OPENSEARCH_PORT=9200
OPENSEARCH_USER=admin
OPENSEARCH_PASSWORD=myStrongPassword123!

# Docker (for auto-start)
OPENSEARCH_DOCKER_IMAGE=opensearchproject/opensearch:latest
OPENSEARCH_DOCKER_CONTAINER=opensearch-local

# Search UI
SEARCH_UI_PORT=8765
```

## Testing

```bash
cd servers/opensearch-mcp

# Run all tests with pytest
python -m pytest tests/

# Or run individual test files
python -m pytest tests/test_knowledge_base.py
python -m pytest tests/test_sample_docs.py
python -m pytest tests/test_opensearch_ops.py      # Requires OpenSearch
python -m pytest tests/test_verification_and_ui.py
python -m pytest tests/test_integration.py          # Requires OpenSearch

# Run with verbose output
python -m pytest tests/ -v
```

## Troubleshooting

### OpenSearch Connection Issues
- Ensure OpenSearch is running: `curl http://localhost:9200`
- Check Docker Desktop is running (for auto-start)
- Verify environment variables are set correctly

### MCP Server Not Found
- Check mcp.json is in `.kiro/settings/`
- Restart Kiro or reconnect MCP servers
- Check server logs for errors

### Tool Errors
- Verify dependencies are installed: `pip install -e .`
- Check Python version: `python --version` (need 3.8+)
- Review tool parameters in TOOLS_REFERENCE.md

## Development

### Adding New Tools

1. Implement tool in `servers/opensearch-mcp/opensearch_mcp/tools/`
2. Add tool definition to `server.py` `list_tools()`
3. Add tool handler to `server.py` `call_tool()`
4. Add tests to appropriate test file
5. Update TOOLS_REFERENCE.md

### Running Tests

```bash
cd servers/opensearch-mcp

# Run all tests
python -m pytest tests/

# Run specific test categories
python -m pytest tests/test_knowledge_base.py -v
python -m pytest tests/test_sample_docs.py -v
python -m pytest tests/test_verification_and_ui.py -v

# Run tests requiring OpenSearch
python -m pytest tests/test_opensearch_ops.py -v
python -m pytest tests/test_integration.py -v
```

## Version History

- **1.0.0** - Initial release
  - 18 tools implemented (was 17, added `index_doc`)
  - Knowledge base access
  - Sample document management
  - OpenSearch operations
  - Verification and testing

## License

See LICENSE file for details.

## Support

For issues or questions:
1. Check TOOLS_REFERENCE.md for tool documentation
2. Review steering files for workflow guidance
3. Check troubleshooting section above
4. Review test scripts for usage examples
