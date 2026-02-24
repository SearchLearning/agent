---
title: Getting Started with OpenSearch Architect
description: First-time setup and basic usage guide
---

# Getting Started with OpenSearch Architect

Welcome! This guide will help you set up your first OpenSearch semantic search solution.

## Prerequisites

Before starting, ensure you have:
- OpenSearch cluster running (local or remote)
- Sample data to analyze
- Basic understanding of your search requirements

## Quick Start Workflow

### Step 1: Provide a Sample Document

First, I need to understand your data structure. You can provide a sample in three ways:

**Option A: Paste JSON directly**
```
Submit this sample document: {"title": "Product Name", "description": "Product details", "price": 100}
```

**Option B: Load from local file**
```
Load a sample from ~/data/products.csv
```

**Option C: Load from URL**
```
Load a sample from https://example.com/data.json
```

### Step 2: Describe Your Requirements

Tell me about your use case:
- How many documents? (e.g., "10 million documents")
- What language? (e.g., "English", "Japanese", "multilingual")
- What's your priority? (e.g., "best accuracy", "low latency", "cost-effective")
- Any special needs? (e.g., "prefix search", "fuzzy matching")

### Step 3: Get Recommendations

I'll analyze your requirements and recommend:
- Search strategy (BM25, dense vector, sparse vector, or hybrid)
- Index configuration
- Model selection
- Deployment approach

### Step 4: Implement the Solution

Once you approve the plan, I'll help you:
- Create the index with proper mappings
- Deploy embedding models (if needed)
- Set up ingest and search pipelines
- Index test documents
- Verify everything works

## Example Conversation

```
You: Load a sample from ~/data/products.csv


Me: Sample document loaded from '/Users/you/data/products.csv'. 
    Detected fields: product_id, name, description, price, category

You: I have 5 million products, mostly English text. I want the best search relevance.

Me: Based on your requirements, I recommend:
    - Hybrid search (BM25 + Dense Vector)
    - Amazon Titan embeddings via Bedrock
    - HNSW algorithm for vector search
    - Balanced weights (0.5 lexical, 0.5 semantic)

You: Sounds good, let's implement it.

Me: I'll create the index and set everything up...
    [Creates index, models, pipelines, and verification documents]
    
    ✅ Setup complete! Your search index is ready.
```

## Common Use Cases

### Use Case 1: E-commerce Product Search
- **Data**: Product catalog with titles, descriptions, prices
- **Recommendation**: Hybrid search (BM25 + dense vector)
- **Why**: Combines exact matching (SKUs, brands) with semantic understanding

### Use Case 2: Document Repository
- **Data**: Large collection of text documents
- **Recommendation**: Dense vector search with good embeddings
- **Why**: Semantic understanding for finding similar documents

### Use Case 3: Log Search
- **Data**: Application logs, timestamps, error messages
- **Recommendation**: BM25 with structured filters
- **Why**: Fast exact matching with time-range filtering

### Use Case 4: Multilingual Content
- **Data**: Content in multiple languages
- **Recommendation**: Multilingual dense vector model
- **Why**: Cross-lingual semantic search capabilities

## Available Tools

I have 18 tools to help you (was 17, added `index_doc`):

**Knowledge & Research** (4 tools)
- Read knowledge base about search methods
- Browse dense vector model catalog
- Browse sparse vector model catalog
- Search OpenSearch documentation

**Data Management** (5 tools)
- Submit sample documents
- Load from files or URLs
- View current sample
- Clear sample data

**OpenSearch Operations** (5 tools)
- Create indexes
- Create pipelines
- Deploy models
- Manage documents

**Testing & Verification** (3 tools)
- Index test documents
- Launch search UI
- Clean up test data

## Tips for Success

1. **Start with a good sample** - The more representative your sample, the better my recommendations
2. **Be specific about requirements** - Tell me about scale, latency needs, and budget constraints
3. **Ask questions** - I can explain trade-offs and alternatives
4. **Test before production** - Use verification documents to test your setup
5. **Iterate** - We can adjust the configuration based on test results

## Next Steps

Ready to get started? Just say:
```
I want to build a semantic search solution
```

Or jump to a specific workflow:
- [Dense Vector Search Setup](dense-vector-workflow.md)
- [Hybrid Search Setup](hybrid-search-workflow.md)
- [Sparse Vector Search Setup](sparse-vector-workflow.md)

## Need Help?

- Ask me to explain any concept: "What is HNSW?"
- Request alternatives: "What other models are available?"
- Get documentation: "Search OpenSearch docs for knn_vector"
- Troubleshoot: See [troubleshooting.md](troubleshooting.md)
