# OpenSearch Architect Power

An intelligent assistant for designing and implementing OpenSearch semantic search solutions.

## Overview

This power helps you design, configure, and deploy OpenSearch search architectures including:
- Dense vector search (HNSW, IVF, diskANN)
- Sparse vector search (neural sparse)
- Hybrid search (combining multiple retrieval methods)
- BM25 keyword search
- Model deployment strategies (SageMaker, Bedrock, local nodes)

## Keywords

opensearch, search, semantic search, vector search, dense vector, sparse vector, hybrid search, elasticsearch, neural search, embeddings, knn, hnsw, bm25, retrieval

## Capabilities

### Knowledge Base Access
- Access comprehensive guides on OpenSearch semantic search methods
- Query dense and sparse vector model catalogs
- Search official OpenSearch documentation

### Sample Document Management
- Submit sample documents for analysis
- Load samples from local files or URLs
- Automatic schema inference and language detection

### Index & Pipeline Operations
- Create indexes with appropriate mappings
- Configure ingest and search pipelines
- Set up embedding models (Bedrock, local pretrained)
- Manage vector search configurations

### Verification & Testing
- Capability-driven verification document indexing
- Launch interactive search UI for testing
- Cleanup and management tools

## Getting Started

1. **Activate the power** to load tool definitions
2. **Provide a sample document** to analyze your data structure
3. **Describe your requirements** (scale, latency, budget, languages)
4. **Get recommendations** for the optimal search architecture
5. **Implement the solution** using the provided tools

## Common Workflows

### Dense Vector Search Setup
1. Submit sample document
2. Read dense vector models guide
3. Create index with knn_vector fields
4. Create embedding model
5. Create ingest pipeline
6. Verify with test documents

### Hybrid Search Setup
1. Submit sample document
2. Read knowledge base for hybrid strategies
3. Create index with both text and knn_vector fields
4. Create embedding model
5. Create ingest pipeline
6. Create search pipeline with normalization
7. Verify and launch UI

### Sparse Vector Search Setup
1. Submit sample document
2. Read sparse vector models guide
3. Create index with rank_features or sparse_vector fields
4. Create sparse encoding model
5. Create ingest pipeline
6. Verify and launch UI

## Tool Categories

### Read-Only Tools (Safe to call anytime)
- Knowledge base queries
- Model catalog lookups
- Documentation search
- Sample document retrieval

### Write Tools (Modify OpenSearch)
- Index creation
- Pipeline creation
- Model deployment
- Document indexing

### Management Tools
- Sample document submission
- Verification cleanup
- UI launcher

## Requirements

- OpenSearch cluster (local or remote)
- Python 3.8+
- Network access for Bedrock models (if using AWS)
- Docker (for local OpenSearch auto-start)

## Environment Variables

```bash
OPENSEARCH_HOST=localhost
OPENSEARCH_PORT=9200
OPENSEARCH_USER=admin
OPENSEARCH_PASSWORD=myStrongPassword123!
OPENSEARCH_DOCKER_IMAGE=opensearchproject/opensearch:latest
SEARCH_UI_PORT=8765
```

## Notes

- The power will auto-start a local OpenSearch container if none is detected
- Sample documents are stored in `.opensearch-architect/samples/` for persistence
- Verification documents use the prefix `verification-` by default
- The search UI runs on http://127.0.0.1:8765 by default

## Version

1.0.0 - Initial Kiro Power release
