---
title: "AWS OpenSearch Serverless Deployment"
inclusion: manual
---

# AWS OpenSearch Serverless Deployment Guide

This guide covers deploying your local OpenSearch search strategy to AWS OpenSearch Serverless.

## When to Use OpenSearch Serverless

Use OpenSearch Serverless for:
- Semantic search applications
- Hybrid search (BM25 + vector)
- Standard vector search workloads
- Applications requiring automatic scaling
- Cost-sensitive workloads with variable traffic
- Quick proof-of-concept deployments

**Do NOT use for Agentic Search** - use OpenSearch Domain instead (see aws-opensearch-domain.md).

## Using Official AWS Documentation

If the user has the `awslabs.aws-documentation-mcp-server` MCP server configured, use it to look up the latest official AWS documentation when needed during deployment. This is especially useful for:
- Verifying current API parameters, collection types, and service limits for OpenSearch Serverless
- Looking up IAM policy formats, data access policy syntax, and Bedrock model availability
- Checking the latest details on automatic semantic enrichment, network policies, and encryption options
- Resolving errors or unexpected behavior during deployment steps

Search for relevant docs proactively (e.g. "OpenSearch Serverless create collection", "OpenSearch Serverless data access policy", "OpenSearch Serverless semantic enrichment") rather than relying solely on the instructions below, which may become outdated.

## Prerequisites

Before starting Phase 5 deployment:
1. AWS credentials configured (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION)
2. Appropriate IAM permissions for OpenSearch Serverless
3. Successful Phase 4 execution with local OpenSearch running
4. Search strategy manifest file created in Phase 3

## Deployment Steps

### Step 1: Create OpenSearch Serverless Collection

Use the AWS API MCP server to create a serverless collection:

```json
POST /opensearchserverless/CreateCollection
{
  "name": "<collection-name>",
  "type": "SEARCH",
  "description": "Search application deployed from local OpenSearch"
}
```

Choose collection type based on the search strategy:
- **VECTORSEARCH**: For dense vector search workloads (semantic search with dense embeddings)
- **SEARCH**: For all other search workloads (BM25, neural sparse, hybrid with neural sparse)

Note: Neural sparse (automatic semantic enrichment) requires SEARCH type, not VECTORSEARCH.

### Step 2: Configure Network Access

Create a network policy for the collection using AWS API MCP:

```json
POST /opensearchserverless/CreateAccessPolicy
{
  "name": "<collection-name>-network-policy",
  "type": "network",
  "policy": "[{\"Rules\":[{\"ResourceType\":\"collection\",\"Resource\":[\"collection/<collection-name>\"],\"AllowFromPublic\":true}],\"AllowFromPublic\":true}]"
}
```

Network policy options:
- **Public access (for development)**: Set `AllowFromPublic: true`
- **VPC endpoint access (for production)**: Specify VPC endpoint IDs in the policy

### Step 3: Configure Data Access

Create a data access policy with appropriate permissions using AWS API MCP:

```json
POST /opensearchserverless/CreateAccessPolicy
{
  "name": "<collection-name>-data-policy",
  "type": "data",
  "policy": "[{\"Rules\":[{\"ResourceType\":\"index\",\"Resource\":[\"index/<collection-name>/*\"],\"Permission\":[\"aoss:CreateIndex\",\"aoss:DescribeIndex\",\"aoss:UpdateIndex\",\"aoss:DeleteIndex\",\"aoss:ReadDocument\",\"aoss:WriteDocument\"]},{\"ResourceType\":\"collection\",\"Resource\":[\"collection/<collection-name>\"],\"Permission\":[\"aoss:CreateCollectionItems\",\"aoss:DescribeCollectionItems\"]},{\"ResourceType\":\"model\",\"Resource\":[\"model/<collection-name>/*\"],\"Permission\":[\"aoss:*\"]}],\"Principal\":[\"arn:aws:iam::<account-id>:role/<role-name>\"]}]"
}
```

This policy grants permissions for:
- **Index**: Create, update, describe, delete indices and read/write documents
- **Collection**: Create and describe collection items (required for pipelines)
- **Model**: Create ML resources (required for automatic semantic enrichment)

Replace `<account-id>` and `<role-name>` with the appropriate AWS principal.

**For private collections**, also configure network access to allow `aoss.amazonaws.com` service access.

### Step 4: Wait for Collection to be Active

Poll collection status until active using AWS API MCP:

```json
POST /opensearchserverless/BatchGetCollection
{
  "names": ["<collection-name>"]
}
```

Wait for status: "ACTIVE" (typically takes 1-3 minutes)

### Step 5: Create Index with Automatic Semantic Enrichment (Neural Sparse)

**For Neural Sparse search strategies**, use automatic semantic enrichment:

OpenSearch Serverless supports automatic semantic enrichment for Neural Sparse, which automatically manages models and pipelines. Use the AWS API MCP to create the index:

```json
POST /opensearchserverless/CreateIndex
{
  "id": "<collection-id>",
  "indexName": "<index-name>",
  "indexSchema": {
    "mappings": {
      "properties": {
        "<text-field>": {
          "type": "text",
          "semantic_enrichment": {
            "status": "ENABLED",
            "language_options": "english"
          }
        }
      }
    }
  }
}
```

Key points about automatic semantic enrichment:
- Set `semantic_enrichment.status` to "ENABLED" on text fields that should use neural sparse
- Specify `language_options`: "english" or "multi-lingual" (supports 15 languages including Arabic, Bengali, Chinese, Finnish, French, Hindi, Indonesian, Japanese, Korean, Persian, Russian, Spanish, Swahili, Telugu)
- You can have both semantic and non-semantic text fields in the same index
- The system automatically:
  - Deploys the service-managed sparse model
  - Creates ingest pipelines for document enrichment
  - Creates search pipelines for query enrichment
  - Rewrites "match" queries to neural sparse queries (no query changes needed)
- No manual model or pipeline management required
- Best for small-to-medium sized fields with natural language content (product descriptions, reviews, summaries)
- Token limits: 8,192 tokens for English, 512 tokens for multilingual
- Improves relevance by ~20% for English, ~105% for multilingual over BM25
- Charged based on OCU consumption during indexing only (monitor with SemanticSearchOCU CloudWatch metric)

**For other search strategies** (BM25, dense vector, hybrid with dense vectors):

Use the opensearch-mcp-server tools to create the index on the collection endpoint:

1. Get the local index configuration from the manifest
2. Create the index on the serverless collection endpoint
3. Include all mappings, settings, and configurations from local setup

### Step 6: Deploy ML Models and Pipelines for Dense Vector Search

**For Neural Sparse**: Skip this step - automatic semantic enrichment handles everything.

**For Dense Vector embeddings** (semantic/hybrid search with dense vectors):

Dense vector search requires setting up ML Commons connector, model, ingest pipeline, and search pipeline. This guide uses Amazon Bedrock Titan Text Embeddings V2 as the default.

#### Step 6.1: Create IAM Role for Bedrock Access

Create an IAM role that allows OpenSearch Serverless to invoke Bedrock models:

```json
POST /iam/CreateRole
{
  "RoleName": "opensearch-bedrock-role",
  "AssumeRolePolicyDocument": {
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": {
        "Service": "ml.opensearchservice.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }]
  }
}
```

Attach permissions policy:

```json
POST /iam/PutRolePolicy
{
  "RoleName": "opensearch-bedrock-role",
  "PolicyName": "BedrockInvokePolicy",
  "PolicyDocument": {
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Action": "bedrock:InvokeModel",
      "Resource": "arn:aws:bedrock:*::foundation-model/amazon.titan-embed-text-v2:0"
    }]
  }
}
```

Note the role ARN for use in the connector.

#### Step 6.2: Create ML Commons Connector

Use the opensearch-mcp-server to create a connector to Bedrock Titan:

```
POST <collection-endpoint>/_plugins/_ml/connectors/_create
{
  "name": "Amazon Bedrock Titan Embedding V2",
  "description": "Connector to Bedrock Titan embedding model",
  "version": 1,
  "protocol": "aws_sigv4",
  "parameters": {
    "region": "<aws-region>",
    "service_name": "bedrock"
  },
  "credential": {
    "roleArn": "<opensearch-bedrock-role-arn>"
  },
  "actions": [{
    "action_type": "predict",
    "method": "POST",
    "url": "https://bedrock-runtime.<aws-region>.amazonaws.com/model/amazon.titan-embed-text-v2:0/invoke",
    "headers": {
      "content-type": "application/json",
      "x-amz-content-sha256": "required"
    },
    "request_body": "{ \"inputText\": \"${parameters.inputText}\" }",
    "pre_process_function": "\n    StringBuilder builder = new StringBuilder();\n    builder.append(\"\\\"\");\n    String first = params.text_docs[0];\n    builder.append(first);\n    builder.append(\"\\\"\");\n    def parameters = \"{\" +\"\\\"inputText\\\":\" + builder + \"}\";\n    return  \"{\" +\"\\\"parameters\\\":\" + parameters + \"}\";",
    "post_process_function": "\n      def name = \"sentence_embedding\";\n      def dataType = \"FLOAT32\";\n      if (params.embedding == null || params.embedding.length == 0) {\n        return params.message;\n      }\n      def shape = [params.embedding.length];\n      def json = \"{\" +\n                 \"\\\"name\\\":\\\"\" + name + \"\\\",\" +\n                 \"\\\"data_type\\\":\\\"\" + dataType + \"\\\",\" +\n                 \"\\\"shape\\\":\" + shape + \",\" +\n                 \"\\\"data\\\":\" + params.embedding +\n                 \"}\";\n      return json;\n    "
  }]
}
```

Note the `connector_id` from the response.

#### Step 6.3: Register and Deploy the Model

Create a model group:

```
POST <collection-endpoint>/_plugins/_ml/model_groups/_register
{
  "name": "bedrock_embedding_models",
  "description": "Model group for Bedrock embedding models"
}
```

Register the model:

```
POST <collection-endpoint>/_plugins/_ml/models/_register
{
  "name": "bedrock-titan-embed-v2",
  "function_name": "remote",
  "description": "Bedrock Titan Text Embeddings V2",
  "model_group_id": "<model-group-id>",
  "connector_id": "<connector-id>"
}
```

Deploy the model:

```
POST <collection-endpoint>/_plugins/_ml/models/<model-id>/_deploy
```

Test the model:

```
POST <collection-endpoint>/_plugins/_ml/models/<model-id>/_predict
{
  "parameters": {
    "inputText": "hello world"
  }
}
```

Verify the response contains 1024-dimensional embeddings (Titan V2 default).

#### Step 6.4: Create Ingest Pipeline

Create an ingest pipeline that uses the model to generate embeddings:

```
PUT <collection-endpoint>/_ingest/pipeline/bedrock-embedding-pipeline
{
  "description": "Bedrock Titan embedding pipeline",
  "processors": [{
    "text_embedding": {
      "model_id": "<model-id>",
      "field_map": {
        "<text-field>": "<vector-field>"
      }
    }
  }]
}
```

Replace `<text-field>` with the source text field name and `<vector-field>` with the target vector field name from your local configuration.

#### Step 6.5: Create Index with Vector Mappings

Create the index with knn_vector mappings:

```
PUT <collection-endpoint>/<index-name>
{
  "settings": {
    "index": {
      "knn": true,
      "knn.space_type": "cosinesimil",
      "default_pipeline": "bedrock-embedding-pipeline"
    }
  },
  "mappings": {
    "properties": {
      "<text-field>": {
        "type": "text"
      },
      "<vector-field>": {
        "type": "knn_vector",
        "dimension": 1024,
        "method": {
          "name": "hnsw",
          "space_type": "cosinesimil",
          "engine": "faiss"
        }
      }
    }
  }
}
```

Key configuration:
- `dimension`: 1024 for Titan V2 (default), 512 or 256 if configured
- `space_type`: "cosinesimil" for cosine similarity (recommended for text)
- `method`: HNSW algorithm with FAISS engine for efficient search

#### Step 6.6: Create Search Pipeline (for hybrid search)

If using hybrid search (BM25 + vector), create a search pipeline with normalization:

```
PUT <collection-endpoint>/_search/pipeline/hybrid-search-pipeline
{
  "description": "Hybrid search with normalization",
  "phase_results_processors": [{
    "normalization-processor": {
      "normalization": {
        "technique": "min_max"
      },
      "combination": {
        "technique": "arithmetic_mean",
        "parameters": {
          "weights": [0.3, 0.7]
        }
      }
    }
  }]
}
```

Adjust weights based on your preference (first weight for BM25, second for vector).

**For pure vector search**: Skip this step - no search pipeline needed.

### Step 7: Index Sample Documents

Index test documents to verify the setup:

1. Use the same sample documents from Phase 1
2. For Neural Sparse with automatic enrichment:
   - Documents are automatically enriched during ingestion
   - Sparse vectors are generated and stored
   - No additional configuration needed
3. For Dense Vector search:
   - Documents are processed through the ingest pipeline
   - Bedrock Titan generates embeddings automatically
   - Embeddings are stored in the vector field
4. Test search queries to confirm functionality:
   - Neural Sparse: Use standard "match" queries (automatically rewritten)
   - Dense Vector: Use "neural" query with the model_id

### Step 8: Provide Access Information

Give the user:
- Collection endpoint URL
- Collection ARN
- Dashboard URL (if applicable)
- Sample search queries to test
- Cost estimation based on collection type and expected usage

## Cost Considerations

OpenSearch Serverless pricing:
- Charged for OCU (OpenSearch Compute Units) hours
- Minimum: 2 OCUs for indexing, 2 OCUs for search
- Scales automatically based on workload
- Storage charged separately per GB

Recommend monitoring costs in AWS Cost Explorer.

## Security Best Practices

1. Use IAM roles instead of access keys when possible
2. Enable encryption at rest (enabled by default)
3. Use VPC endpoints for production workloads
4. Implement least-privilege access policies
5. Enable CloudWatch logging for audit trails

## Troubleshooting

Common issues:
- **Access denied**: Check data access policy and IAM permissions
- **Collection creation fails**: Verify service quotas and region availability
- **Model deployment fails**: Ensure Bedrock models are available in the region
- **Search returns no results**: Verify index mappings and pipeline configurations

## Next Steps

After successful deployment:
1. Update application code to use the serverless endpoint
2. Set up monitoring and alerting in CloudWatch
3. Configure backup strategies if needed
4. Plan for production scaling and optimization
