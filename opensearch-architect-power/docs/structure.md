# OpenSearch Architect Power - Directory Structure

## Recommended Organization

```
opensearch-architect-power/
│
├── POWER.md                    # Main power documentation (required by Kiro)
├── README.md                   # Quick start and overview
├── mcp.json                    # MCP server configuration (required by Kiro)
├── TOOLS_REFERENCE.md          # Complete tool documentation
├── STRUCTURE.md                # This file
│
├── steering/                   # Workflow guides (optional, used by Kiro)
│   ├── getting-started.md
│   ├── dense-vector-workflow.md
│   ├── hybrid-search-workflow.md
│   ├── sparse-vector-workflow.md
│   └── troubleshooting.md
│
├── docs/                       # Development/build documentation
│   ├── STEP1_COMPLETE.md       # Build step 1 notes
│   ├── STEP2_READY.md          # Build step 2 notes
│   ├── STEP3_STATUS.md         # Build step 3 notes
│   ├── STEP4_COMPLETE.md       # Build step 4 notes
│   ├── STEP5_PLAN.md           # Build step 5 notes
│   └── VALIDATION.md           # Validation checklist
│
└── servers/                    # MCP servers (required)
    └── opensearch-mcp/         # Main MCP server
        │
        ├── opensearch_mcp/     # Python package
        │   ├── __init__.py
        │   ├── server.py       # MCP server entry point
        │   └── tools/          # Tool implementations
        │       ├── __init__.py
        │       ├── knowledge_base.py
        │       ├── sample_docs.py
        │       ├── opensearch_client.py
        │       ├── opensearch_ops.py
        │       ├── verification.py
        │       ├── search_ui.py
        │       └── web_search.py
        │
        ├── knowledge/          # Documentation files
        │   ├── opensearch_semantic_search_guide.md
        │   ├── dense_vector_models.md
        │   └── sparse_vector_models.md
        │
        ├── ui/                 # Search UI (optional)
        │   └── search_builder/ # React UI files
        │
        ├── pyproject.toml      # Package configuration
        ├── README.md           # Server documentation
        ├── test_server.py      # Knowledge base tests
        ├── test_sample_docs.py # Sample doc tests
        ├── test_opensearch_ops.py  # OpenSearch tests
        └── test_step4_tools.py # Verification tests
```

## File Purposes

### Root Level (Power Definition)

- **POWER.md** - Main documentation shown in Kiro Powers panel
- **mcp.json** - MCP server configuration (tells Kiro how to start the server)
- **README.md** - Quick start guide for developers
- **TOOLS_REFERENCE.md** - Complete tool documentation

### steering/ (Workflow Guides)

Optional directory for workflow guides. Kiro can use these to provide structured guidance.

- **getting-started.md** - First-time setup walkthrough
- **dense-vector-workflow.md** - Step-by-step dense vector setup
- **hybrid-search-workflow.md** - Step-by-step hybrid search setup
- **sparse-vector-workflow.md** - Step-by-step sparse vector setup
- **troubleshooting.md** - Common issues and solutions

### docs/ (Development Documentation)

Internal documentation about the build process. Not used by Kiro at runtime.

- **STEPX_*.md** - Build step documentation
- **VALIDATION.md** - Testing checklist

### servers/ (MCP Server Implementation)

The actual MCP server code.

- **opensearch_mcp/** - Python package with all tools
- **knowledge/** - Documentation files read by tools
- **ui/** - Optional search UI
- **test_*.py** - Test scripts

## What Kiro Uses

When you activate this power in Kiro, it:

1. Reads **POWER.md** for documentation
2. Uses **mcp.json** to start the MCP server
3. Optionally reads **steering/** files for workflow guidance
4. Calls tools via the MCP server in **servers/opensearch-mcp/**

## What to Clean Up (Optional)

If you want a cleaner distribution, you can move to docs/:

- STEP1_COMPLETE.md → docs/
- STEP2_READY.md → docs/
- STEP3_READY.md → docs/
- STEP3_STATUS.md → docs/
- STEP4_PLAN.md → docs/
- STEP4_COMPLETE.md → docs/
- STEP5_PLAN.md → docs/
- VALIDATION.md → docs/

These are build artifacts, not needed for runtime.

## Minimal Distribution

For end users, you only need:

```
opensearch-architect-power/
├── POWER.md
├── README.md
├── mcp.json
├── TOOLS_REFERENCE.md
├── steering/
└── servers/
    └── opensearch-mcp/
```

The `docs/` directory is optional and can be omitted from distribution.

## Storage Locations (Runtime)

The power creates these directories at runtime:

```
~/.opensearch-architect/
├── samples/
│   ├── current_sample.json
│   └── metadata.json
└── verification/
    └── doc_tracker.json
```

These are NOT part of the power distribution - they're created when tools run.
