# OpenSearch Architect Power - Tools Reference

Complete reference for all 18 tools in the OpenSearch Architect Power.

## Tool Categories

- [Knowledge Base Tools](#knowledge-base-tools) (3 tools)
- [Sample Document Tools](#sample-document-tools) (5 tools)
- [OpenSearch Operations Tools](#opensearch-operations-tools) (6 tools)
- [Verification & UI Tools](#verification--ui-tools) (4 tools)

**Total: 18 tools**

---

## Knowledge Base Tools

### read_knowledge_base
Read the comprehensive OpenSearch Semantic Search Guide.

**Parameters**: None

**Returns**: Full guide covering BM25, Dense Vector, Sparse Vector, Hybrid search, algorithms (HNSW, IVF, diskANN), cost profiles, and deployment options.

**Example**:
```
Read the knowledge base about hybrid search strategies
```

---

### read_dense_vector_models
Read the Dense Vector Models catalog.

**Parameters**: None

**Returns**: Available models for OpenSearch Node, SageMaker GPU, and External API services with dimensions and recommendations.

**Example**:
```
What dense vector models are available for English text?
```

---

### read_sparse_vector_models
Read the Sparse Vector Models catalog.

**Parameters**: None

**Returns**: Available models for Doc-Only and Bi-Encoder modes with deployment options.

**Example**:
```
Show me sparse vector models for cost-effective search
```

---

## Sample Document Tools

### get_sample_doc
Retrieve the stored sample document with metadata.

**Parameters**: None

**Returns**: JSON with document and metadata (source, fields)

**Storage**: `~/.opensearch-architect/samples/current_sample.json`

**Example**:
```
Show me the current sample document
```

---

### submit_sample_doc
Store a sample document from JSON text.

**Parameters**:
- `doc` (string, required): Sample document as JSON string

**Returns**: Confirmation with field count

**Example**:
```
Submit this sample document: {"title": "Product A", "price": 100}
```

---

### submit_sample_doc_from_local_file
Load a sample document from a local file.

**Parameters**:
- `path_or_text` (string, required): Local file path

**Supported Formats**: CSV, TSV, JSON, JSONL, TXT

**Returns**: Confirmation with detected fields and file info

**Example**:
```
Load a sample from ~/data/products.csv
```

---

### submit_sample_doc_from_url
Download and load a sample document from a URL.

**Parameters**:
- `url_or_text` (string, required): HTTP/HTTPS URL

**Supported Formats**: JSON, JSONL, CSV, TSV, TXT

**Returns**: Confirmation with detected fields

**Example**:
```
Load a sample from https://example.com/data.json
```

---

### clear_sample_doc
Clear the stored sample document.

**Parameters**: None

**Returns**: Confirmation message

**Example**:
```
Clear the sample document
```

---

## OpenSearch Operations Tools

### create_index
Create an OpenSearch index with settings and mappings.

**Parameters**:
- `index_name` (string, required): Index name
- `body` (string, required): Index configuration as JSON (settings + mappings)

**Returns**: Success message with acknowledgment status

**Example**:
```json
Create index "products" with this configuration:
{
  "settings": {"number_of_shards": 1},
  "mappings": {
    "properties": {
      "title": {"type": "text"},
      "embedding": {"type": "knn_vector", "dimension": 1024}
    }
  }
}
```

---

### create_and_attach_pipeline
Create and attach an ingest or search pipeline to an index.

**Parameters**:
- `index_name` (string, required): Target index
- `pipeline_type` (string, required): "ingest" or "search"
- `pipeline_id` (string, required): Pipeline identifier
- `pipeline_body` (string, required): Pipeline configuration as JSON
- `is_hybrid_search` (boolean, optional): Add normalization for hybrid search
- `hybrid_weights` (string, optional): Weights as JSON array "[0.5, 0.5]"

**Returns**: Success message with attachment confirmation

**Example**:
```
Create an ingest pipeline "my-pipeline" for index "products" with text_embedding processor
```

---

### create_bedrock_embedding_model
Create a Bedrock embedding model connector.

**Parameters**:
- `model_name` (string, required): Model name in OpenSearch
- `model_id` (string, required): Bedrock model ID (e.g., "amazon.titan-embed-text-v2")
- `dimensions` (integer, required): Embedding dimensions
- `region` (string, optional): AWS region (default: "us-east-1")

**Returns**: Model ID, connector ID, and deployment status

**Requires**: AWS credentials configured

**Example**:
```
Create a Bedrock model "my-titan-model" using amazon.titan-embed-text-v2 with 1024 dimensions
```

---

### create_local_pretrained_model
Deploy a local pretrained model on OpenSearch nodes.

**Parameters**:
- `model_name` (string, required): Model name in OpenSearch
- `model_id` (string, required): Pretrained model ID
- `model_format` (string, optional): "TORCH_SCRIPT" or "ONNX" (default: "TORCH_SCRIPT")
- `embedding_dimension` (integer, optional): Dimensions for dense models

**Returns**: Model ID and deployment status

**Example**:
```
Create a local sparse model using amazon/neural-sparse/opensearch-neural-sparse-encoding-doc-v2-mini
```

---

### delete_doc
Delete a document from an index by ID.

**Parameters**:
- `index_name` (string, required): Index name
- `doc_id` (string, required): Document ID

**Returns**: Deletion confirmation

**Example**:
```
Delete document "test-doc-1" from index "products"
```

---

### index_doc
Index a single document into an OpenSearch index.

**Parameters**:
- `index_name` (string, required): Name of the index
- `doc` (string, required): Document as JSON string
- `doc_id` (string, required): Document ID

**Returns**: Document after ingest pipeline processing (JSON) or error details

**Example**:
```json
Index this document into "products" with ID "prod-123":
{
  "title": "Wireless Mouse",
  "description": "Ergonomic wireless mouse with USB receiver",
  "price": 29.99,
  "category": "Electronics"
}
```

**Note**: The document will be processed by any attached ingest pipeline. The response shows the document after pipeline processing, which is useful for verifying that embeddings were generated correctly.

---

## Verification & UI Tools

### apply_capability_driven_verification
Index verification documents for testing search capabilities.

**Parameters**:
- `worker_output` (string, required): Worker output with search capabilities
- `index_name` (string, required): Target index
- `count` (integer, optional): Number of documents (1-100, default: 10)
- `id_prefix` (string, optional): Document ID prefix (default: "verification")

**Returns**: JSON with indexed document IDs and results

**Storage**: Tracks IDs in `~/.opensearch-architect/verification/doc_tracker.json`

**Example**:
```
Index 10 verification documents in "products" index
```

---

### cleanup_verification_docs
Remove verification documents from an index.

**Parameters**:
- `index_name` (string, optional): Index to clean (cleans all if omitted)

**Returns**: Cleanup summary with deleted count

**Example**:
```
Clean up verification documents from "products" index
```

---

### launch_search_ui
Get instructions for launching the search UI or testing queries.

**Parameters**:
- `index_name` (string, required): Index to search
- `port` (integer, optional): UI port (default: 8765)

**Returns**: Launch instructions or query examples

**Example**:
```
Launch search UI for "products" index
```

---

### search_opensearch_org
Search official OpenSearch documentation.

**Parameters**:
- `query` (string, required): Search query
- `number_of_results` (integer, optional): Max results (1-10, default: 5)

**Returns**: JSON with titles, URLs, and snippets from opensearch.org

**Example**:
```
Search OpenSearch docs for "knn vector field parameters"
```

---

## Tool Usage Patterns

### Pattern 1: Complete Setup Workflow
```
1. submit_sample_doc_from_local_file("~/data/products.csv")
2. read_knowledge_base()
3. read_dense_vector_models()
4. create_index("products", {...})
5. create_bedrock_embedding_model(...)
6. create_and_attach_pipeline(...)
7. apply_capability_driven_verification(...)
8. launch_search_ui("products")
```

### Pattern 2: Quick Testing
```
1. submit_sample_doc('{"title": "Test"}')
2. create_index("test", {...})
3. apply_capability_driven_verification(...)
4. cleanup_verification_docs("test")
```

### Pattern 3: Research & Planning
```
1. read_knowledge_base()
2. search_opensearch_org("hybrid search normalization")
3. read_dense_vector_models()
4. read_sparse_vector_models()
```

---

## Error Handling

All tools return descriptive error messages:
- Connection errors: "Error: Could not connect to OpenSearch: ..."
- Validation errors: "Error: Invalid JSON in body: ..."
- Not found errors: "Error: Index 'xyz' does not exist"
- Success messages: Start with "✅"
- Warnings: Start with "⚠️"

---

## Environment Variables

```bash
# OpenSearch Connection
OPENSEARCH_HOST=localhost
OPENSEARCH_PORT=9200
OPENSEARCH_USER=admin
OPENSEARCH_PASSWORD=myStrongPassword123!

# Docker Configuration
OPENSEARCH_DOCKER_IMAGE=opensearchproject/opensearch:latest
OPENSEARCH_DOCKER_CONTAINER=opensearch-local
OPENSEARCH_DOCKER_START_TIMEOUT=120

# Search UI
SEARCH_UI_HOST=127.0.0.1
SEARCH_UI_PORT=8765
```

---

## Storage Locations

```
~/.opensearch-architect/
├── samples/
│   ├── current_sample.json     # Current sample document
│   └── metadata.json           # Sample source metadata
└── verification/
    └── doc_tracker.json        # Verification document tracking
```
