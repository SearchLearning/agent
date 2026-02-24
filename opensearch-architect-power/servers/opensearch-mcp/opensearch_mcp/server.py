"""
OpenSearch Architect MCP Server

Provides tools for designing and implementing OpenSearch semantic search solutions.
"""

import asyncio
import logging
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("opensearch-mcp")

# Create server instance with version and instructions
app = Server(
    name="opensearch-architect",
    version="1.0.0",
    instructions=(
        "OpenSearch Architect Power - Tools for designing and implementing "
        "OpenSearch semantic search solutions. Provides 18 tools across 4 categories: "
        "Knowledge Base (3), Sample Documents (5), OpenSearch Operations (6), "
        "and Verification & UI (4)."
    )
)


# ============================================================================
# Knowledge Base Tools
# ============================================================================

@app.list_tools()
async def list_tools() -> list[Tool]:
    """List all available tools."""
    return [
        # Knowledge Base Tools
        Tool(
            name="read_knowledge_base",
            description=(
                "Read the OpenSearch Semantic Search Guide to retrieve detailed "
                "information about search methods including BM25, Dense Vector, "
                "Sparse Vector, Hybrid search, algorithms (HNSW, IVF, etc.), "
                "cost profiles, and deployment options."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="read_dense_vector_models",
            description=(
                "Read the Dense Vector Models Guide to retrieve available models "
                "for Dense Vector Search, including models for OpenSearch Node, "
                "SageMaker GPU, and External API services."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="read_sparse_vector_models",
            description=(
                "Read the Sparse Vector Models Guide to retrieve available models "
                "for Sparse Vector Search, covering both Doc-Only and Bi-Encoder modes."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        
        # Sample Document Management Tools
        Tool(
            name="get_sample_doc",
            description=(
                "Get the stored sample document. Returns the document as JSON "
                "along with metadata about its source (manual input, local file, or URL)."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="submit_sample_doc",
            description=(
                "Store a sample document provided as JSON text. The document should "
                "be a JSON object representing a typical record from your dataset."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "doc": {
                        "type": "string",
                        "description": "Sample document as JSON string"
                    }
                },
                "required": ["doc"]
            }
        ),
        Tool(
            name="submit_sample_doc_from_local_file",
            description=(
                "Load a sample document from a local file. Supports CSV, TSV, JSON, "
                "JSONL, and plain text files. Automatically detects format and parses "
                "the first record."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path_or_text": {
                        "type": "string",
                        "description": "Local file path (e.g., ~/data/sample.csv)"
                    }
                },
                "required": ["path_or_text"]
            }
        ),
        Tool(
            name="submit_sample_doc_from_url",
            description=(
                "Download and load a sample document from a URL. Supports JSON, JSONL, "
                "CSV, TSV, and plain text formats. Downloads the first 1MB and parses "
                "the first record."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url_or_text": {
                        "type": "string",
                        "description": "HTTP/HTTPS URL to download from"
                    }
                },
                "required": ["url_or_text"]
            }
        ),
        Tool(
            name="clear_sample_doc",
            description=(
                "Clear the stored sample document and its metadata. Use this to "
                "start fresh with a new sample."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        
        # OpenSearch Operations Tools
        Tool(
            name="create_index",
            description=(
                "Create an OpenSearch index with specified settings and mappings. "
                "The body should include index configuration like number of shards, "
                "replicas, and field mappings (text, keyword, knn_vector, etc.)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "index_name": {
                        "type": "string",
                        "description": "Name of the index to create"
                    },
                    "body": {
                        "type": "string",
                        "description": "Index configuration as JSON string (settings and mappings)"
                    }
                },
                "required": ["index_name", "body"]
            }
        ),
        Tool(
            name="create_and_attach_pipeline",
            description=(
                "Create an ingest or search pipeline and attach it to an index. "
                "For ingest pipelines, use processors like text_embedding. "
                "For search pipelines with hybrid search, optionally specify weights."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "index_name": {
                        "type": "string",
                        "description": "Target index name"
                    },
                    "pipeline_type": {
                        "type": "string",
                        "description": "Pipeline type: 'ingest' or 'search'",
                        "enum": ["ingest", "search"]
                    },
                    "pipeline_id": {
                        "type": "string",
                        "description": "Pipeline identifier"
                    },
                    "pipeline_body": {
                        "type": "string",
                        "description": "Pipeline configuration as JSON string"
                    },
                    "is_hybrid_search": {
                        "type": "boolean",
                        "description": "Whether this is a hybrid search pipeline (adds normalization)"
                    },
                    "hybrid_weights": {
                        "type": "string",
                        "description": "Hybrid weights as JSON array string (e.g., '[0.5, 0.5]')"
                    }
                },
                "required": ["index_name", "pipeline_type", "pipeline_id", "pipeline_body"]
            }
        ),
        Tool(
            name="create_bedrock_embedding_model",
            description=(
                "Create a Bedrock embedding model connector in OpenSearch. "
                "Supports models like amazon.titan-embed-text-v2. "
                "Requires AWS credentials configured in the environment."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model_name": {
                        "type": "string",
                        "description": "Name for the model in OpenSearch"
                    },
                    "model_id": {
                        "type": "string",
                        "description": "Bedrock model ID (e.g., 'amazon.titan-embed-text-v2')"
                    },
                    "dimensions": {
                        "type": "integer",
                        "description": "Embedding dimensions"
                    },
                    "region": {
                        "type": "string",
                        "description": "AWS region (default: us-east-1)"
                    }
                },
                "required": ["model_name", "model_id", "dimensions"]
            }
        ),
        Tool(
            name="create_local_pretrained_model",
            description=(
                "Create a local pretrained model in OpenSearch. "
                "Supports sparse and dense models from the OpenSearch model repository. "
                "Models run on OpenSearch nodes (CPU or GPU)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model_name": {
                        "type": "string",
                        "description": "Name for the model in OpenSearch"
                    },
                    "model_id": {
                        "type": "string",
                        "description": "Pretrained model ID (e.g., 'amazon/neural-sparse/...')"
                    },
                    "model_format": {
                        "type": "string",
                        "description": "Model format: 'TORCH_SCRIPT' or 'ONNX'",
                        "enum": ["TORCH_SCRIPT", "ONNX"]
                    },
                    "embedding_dimension": {
                        "type": "integer",
                        "description": "Embedding dimensions (for dense models)"
                    }
                },
                "required": ["model_name", "model_id"]
            }
        ),
        Tool(
            name="delete_doc",
            description=(
                "Delete a document from an index by its ID. "
                "Useful for cleanup or removing test documents."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "index_name": {
                        "type": "string",
                        "description": "Index name"
                    },
                    "doc_id": {
                        "type": "string",
                        "description": "Document ID to delete"
                    }
                },
                "required": ["index_name", "doc_id"]
            }
        ),
        Tool(
            name="index_doc",
            description=(
                "Index a single document into an OpenSearch index. "
                "The document will be processed by any attached ingest pipeline. "
                "Returns the document after pipeline processing to verify embeddings were generated."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "index_name": {
                        "type": "string",
                        "description": "Name of the index"
                    },
                    "doc": {
                        "type": "string",
                        "description": "Document as JSON string"
                    },
                    "doc_id": {
                        "type": "string",
                        "description": "Document ID"
                    }
                },
                "required": ["index_name", "doc", "doc_id"]
            }
        ),
        
        # Verification and UI Tools
        Tool(
            name="apply_capability_driven_verification",
            description=(
                "Index verification documents for testing search capabilities. "
                "Uses sample documents to populate the index with test data. "
                "Tracks document IDs for later cleanup."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "worker_output": {
                        "type": "string",
                        "description": "Worker output containing search capabilities"
                    },
                    "index_name": {
                        "type": "string",
                        "description": "Target index name"
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of documents to index (1-100, default 10)"
                    },
                    "id_prefix": {
                        "type": "string",
                        "description": "Prefix for document IDs (default 'verification')"
                    }
                },
                "required": ["worker_output", "index_name"]
            }
        ),
        Tool(
            name="cleanup_verification_docs",
            description=(
                "Remove verification documents from an index. "
                "If index_name is provided, cleans only that index. "
                "Otherwise, cleans all tracked verification documents."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "index_name": {
                        "type": "string",
                        "description": "Index name to clean (optional, cleans all if omitted)"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="launch_search_ui",
            description=(
                "Launch or get instructions for the search UI. "
                "Provides ways to test queries against the created index."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "index_name": {
                        "type": "string",
                        "description": "Index name to search against"
                    },
                    "port": {
                        "type": "integer",
                        "description": "Port to run UI on (optional)"
                    }
                },
                "required": ["index_name"]
            }
        ),
        Tool(
            name="search_opensearch_org",
            description=(
                "Search the official OpenSearch documentation at opensearch.org. "
                "Returns relevant documentation pages with titles, URLs, and snippets."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query text"
                    },
                    "number_of_results": {
                        "type": "integer",
                        "description": "Maximum number of results (1-10, default 5)"
                    }
                },
                "required": ["query"]
            }
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Handle tool calls."""
    
    # Knowledge Base Tools
    if name == "read_knowledge_base":
        from .tools.knowledge_base import read_knowledge_base
        content = read_knowledge_base()
        return [TextContent(type="text", text=content)]
    
    elif name == "read_dense_vector_models":
        from .tools.knowledge_base import read_dense_vector_models
        content = read_dense_vector_models()
        return [TextContent(type="text", text=content)]
    
    elif name == "read_sparse_vector_models":
        from .tools.knowledge_base import read_sparse_vector_models
        content = read_sparse_vector_models()
        return [TextContent(type="text", text=content)]
    
    # Sample Document Management Tools
    elif name == "get_sample_doc":
        from .tools.sample_docs import get_sample_doc
        content = get_sample_doc()
        return [TextContent(type="text", text=content)]
    
    elif name == "submit_sample_doc":
        from .tools.sample_docs import submit_sample_doc
        doc = arguments.get("doc", "")
        content = submit_sample_doc(doc)
        return [TextContent(type="text", text=content)]
    
    elif name == "submit_sample_doc_from_local_file":
        from .tools.sample_docs import submit_sample_doc_from_local_file
        path_or_text = arguments.get("path_or_text", "")
        content = submit_sample_doc_from_local_file(path_or_text)
        return [TextContent(type="text", text=content)]
    
    elif name == "submit_sample_doc_from_url":
        from .tools.sample_docs import submit_sample_doc_from_url
        url_or_text = arguments.get("url_or_text", "")
        content = submit_sample_doc_from_url(url_or_text)
        return [TextContent(type="text", text=content)]
    
    elif name == "clear_sample_doc":
        from .tools.sample_docs import clear_sample_doc
        content = clear_sample_doc()
        return [TextContent(type="text", text=content)]
    
    # OpenSearch Operations Tools
    elif name == "create_index":
        from .tools.opensearch_ops import create_index
        index_name = arguments.get("index_name", "")
        body = arguments.get("body", "")
        content = create_index(index_name, body)
        return [TextContent(type="text", text=content)]
    
    elif name == "create_and_attach_pipeline":
        from .tools.opensearch_ops import create_and_attach_pipeline
        index_name = arguments.get("index_name", "")
        pipeline_type = arguments.get("pipeline_type", "")
        pipeline_id = arguments.get("pipeline_id", "")
        pipeline_body = arguments.get("pipeline_body", "")
        is_hybrid_search = arguments.get("is_hybrid_search", False)
        hybrid_weights = arguments.get("hybrid_weights")
        content = create_and_attach_pipeline(
            index_name, pipeline_type, pipeline_id, pipeline_body,
            is_hybrid_search, hybrid_weights
        )
        return [TextContent(type="text", text=content)]
    
    elif name == "create_bedrock_embedding_model":
        from .tools.opensearch_ops import create_bedrock_embedding_model
        model_name = arguments.get("model_name", "")
        model_id = arguments.get("model_id", "")
        dimensions = arguments.get("dimensions", 0)
        region = arguments.get("region", "us-east-1")
        content = create_bedrock_embedding_model(model_name, model_id, dimensions, region)
        return [TextContent(type="text", text=content)]
    
    elif name == "create_local_pretrained_model":
        from .tools.opensearch_ops import create_local_pretrained_model
        model_name = arguments.get("model_name", "")
        model_id = arguments.get("model_id", "")
        model_format = arguments.get("model_format", "TORCH_SCRIPT")
        embedding_dimension = arguments.get("embedding_dimension")
        content = create_local_pretrained_model(
            model_name, model_id, model_format, embedding_dimension
        )
        return [TextContent(type="text", text=content)]
    
    elif name == "delete_doc":
        from .tools.opensearch_ops import delete_doc
        index_name = arguments.get("index_name", "")
        doc_id = arguments.get("doc_id", "")
        content = delete_doc(index_name, doc_id)
        return [TextContent(type="text", text=content)]
    
    elif name == "index_doc":
        from .tools.opensearch_ops import index_doc
        index_name = arguments.get("index_name", "")
        doc = arguments.get("doc", "")
        doc_id = arguments.get("doc_id", "")
        content = index_doc(index_name, doc, doc_id)
        return [TextContent(type="text", text=content)]
    
    # Verification and UI Tools
    elif name == "apply_capability_driven_verification":
        from .tools.verification import apply_capability_driven_verification
        worker_output = arguments.get("worker_output", "")
        index_name = arguments.get("index_name", "")
        count = arguments.get("count", 10)
        id_prefix = arguments.get("id_prefix", "verification")
        content = apply_capability_driven_verification(
            worker_output, index_name, count, id_prefix
        )
        return [TextContent(type="text", text=content)]
    
    elif name == "cleanup_verification_docs":
        from .tools.verification import cleanup_verification_docs
        index_name = arguments.get("index_name")
        content = cleanup_verification_docs(index_name)
        return [TextContent(type="text", text=content)]
    
    elif name == "launch_search_ui":
        from .tools.search_ui import launch_search_ui
        index_name = arguments.get("index_name", "")
        port = arguments.get("port")
        content = launch_search_ui(index_name, port)
        return [TextContent(type="text", text=content)]
    
    elif name == "search_opensearch_org":
        from .tools.web_search import search_opensearch_org
        query = arguments.get("query", "")
        number_of_results = arguments.get("number_of_results", 5)
        content = search_opensearch_org(query, number_of_results)
        return [TextContent(type="text", text=content)]
    
    else:
        raise ValueError(f"Unknown tool: {name}")


async def main():
    """Run the MCP server."""
    logger.info("Starting OpenSearch Architect MCP Server")
    
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
