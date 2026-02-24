---
title: Dense Vector Search Setup
description: Step-by-step guide for implementing dense vector semantic search
---

# Dense Vector Search Workflow

Complete workflow for setting up dense vector semantic search in OpenSearch.

## When to Use Dense Vector Search

✅ **Good for:**
- Semantic similarity search
- Finding conceptually related content
- Cross-lingual search
- Question answering
- Recommendation systems

❌ **Not ideal for:**
- Exact keyword matching
- Prefix/wildcard search
- Very large scale with tight latency requirements (consider sparse vectors)

## Prerequisites

- Sample document with text fields
- OpenSearch cluster running
- AWS credentials (if using Bedrock) OR local model deployment

## Step-by-Step Workflow

### Step 1: Provide Sample Document

```
Load a sample from ~/data/documents.json
```

Or paste directly:
```
Submit this sample: {"title": "Article Title", "content": "Article text content here..."}
```

### Step 2: Review Dense Vector Models

```
Read the dense vector models guide
```

This shows available models by deployment type:
- **Bedrock API**: Titan, Cohere (easiest, pay-per-use)
- **SageMaker GPU**: Custom models (more control, dedicated resources)
- **OpenSearch Nodes**: Local models (cost-effective for high volume)

### Step 3: Choose Your Model

Based on your requirements:

**For English text (best accuracy):**
- Bedrock: `amazon.titan-embed-text-v2` (1024 dimensions)
- Local: `intfloat/e5-large-v2` (1024 dimensions)

**For Multilingual:**
- Bedrock: `cohere.embed-multilingual-v3` (1024 dimensions)
- Local: `sentence-transformers/paraphrase-multilingual-mpnet-base-v2` (768 dimensions)

**For Cost-Effective:**
- Local: `all-MiniLM-L6-v2` (384 dimensions)

### Step 4: Create Index with knn_vector Field

```
Create an index called "my-documents" with these settings:
{
  "settings": {
    "number_of_shards": 2,
    "number_of_replicas": 1,
    "index.knn": true
  },
  "mappings": {
    "properties": {
      "title": {"type": "text"},
      "content": {"type": "text"},
      "title_embedding": {
        "type": "knn_vector",
        "dimension": 1024,
        "method": {
          "name": "hnsw",
          "engine": "lucene",
          "parameters": {
            "ef_construction": 512,
            "m": 16
          }
        }
      },
      "content_embedding": {
        "type": "knn_vector",
        "dimension": 1024,
        "method": {
          "name": "hnsw",
          "engine": "lucene",
          "parameters": {
            "ef_construction": 512,
            "m": 16
          }
        }
      }
    }
  }
}
```

**Key Parameters:**
- `dimension`: Must match your model (1024 for Titan, 768 for some others)
- `engine`: Use `lucene` for HNSW (recommended), `faiss` for IVF
- `ef_construction`: Higher = better accuracy, slower indexing (128-512)
- `m`: Number of connections (8-64, higher = better recall)

### Step 5: Deploy Embedding Model

**Option A: Bedrock Model**
```
Create a Bedrock model called "my-titan-model" using amazon.titan-embed-text-v2 with 1024 dimensions in region us-east-1
```

**Option B: Local Pretrained Model**
```
Create a local pretrained model called "my-e5-model" using intfloat/e5-large-v2 with 1024 dimensions
```

### Step 6: Create Ingest Pipeline

```
Create an ingest pipeline "my-embedding-pipeline" for index "my-documents" with this configuration:
{
  "description": "Generate embeddings for title and content",
  "processors": [
    {
      "text_embedding": {
        "model_id": "<model-id-from-step-5>",
        "field_map": {
          "title": "title_embedding",
          "content": "content_embedding"
        }
      }
    }
  ]
}
```

Replace `<model-id-from-step-5>` with the actual model ID returned in Step 5.

### Step 7: Index Verification Documents

```
Index 10 verification documents in "my-documents" index
```

This will:
- Use your sample document as a template
- Create test documents
- Process them through the pipeline
- Generate embeddings automatically

### Step 8: Test Search

```
Launch search UI for "my-documents" index
```

Or test with a query:
```json
{
  "query": {
    "knn": {
      "content_embedding": {
        "vector": [0.1, 0.2, ...],  // Your query embedding
        "k": 10
      }
    }
  }
}
```

### Step 9: Verify Results

Check that:
- Documents are indexed with embeddings
- Search returns relevant results
- Latency is acceptable

### Step 10: Clean Up (Optional)

```
Clean up verification documents from "my-documents" index
```

## Advanced Configurations

### High Accuracy Setup

For maximum search quality:
```json
{
  "method": {
    "name": "hnsw",
    "engine": "lucene",
    "parameters": {
      "ef_construction": 512,
      "m": 32,
      "ef_search": 200
    }
  }
}
```

### High Performance Setup

For faster search with good accuracy:
```json
{
  "method": {
    "name": "hnsw",
    "engine": "lucene",
    "parameters": {
      "ef_construction": 256,
      "m": 16,
      "ef_search": 100
    }
  }
}
```

### Large Scale Setup (IVF)

For 100M+ documents:
```json
{
  "method": {
    "name": "ivf",
    "engine": "faiss",
    "parameters": {
      "nlist": 1024,
      "nprobes": 16
    }
  }
}
```

## Common Issues

### Issue: "Model not found"
**Solution**: Wait a few seconds after model deployment, then retry

### Issue: "Dimension mismatch"
**Solution**: Ensure knn_vector dimension matches model output dimension

### Issue: "Slow indexing"
**Solution**: Reduce `ef_construction` or increase cluster resources

### Issue: "Poor search quality"
**Solution**: Increase `m` and `ef_search` parameters

## Next Steps

- Combine with BM25: See [hybrid-search-workflow.md](hybrid-search-workflow.md)
- Optimize performance: Adjust HNSW parameters
- Scale up: Add more nodes or use IVF for large datasets
- Monitor: Track search latency and recall metrics

## Example Prompts

```
"Create a dense vector search index for my product catalog"
"Use Titan embeddings with 1024 dimensions"
"Set up HNSW with high accuracy parameters"
"Index 20 test documents and show me the results"
```
