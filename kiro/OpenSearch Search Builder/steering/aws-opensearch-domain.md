---
title: "AWS OpenSearch Domain Deployment for Agentic Search"
inclusion: manual
---

# AWS OpenSearch Domain Deployment Guide (Agentic Search)

This guide covers deploying your local OpenSearch search strategy to AWS OpenSearch Domain (managed cluster).

## When to Use OpenSearch Domain

Use OpenSearch Domain for:
- **Agentic Search applications** (required)
- Workloads requiring advanced plugins
- Applications needing fine-grained control over cluster configuration
- High-performance requirements with dedicated resources
- Custom plugin installations
- Advanced security configurations

## Why Domain for Agentic Search?

Agentic search requires:
- Advanced query capabilities and custom scoring
- Complex aggregations and analytics
- Plugin support for specialized functionality
- Predictable performance with dedicated resources
- Fine-grained cluster tuning

OpenSearch Serverless does not support these requirements.

## Using Official AWS Documentation

If the user has the `awslabs.aws-documentation-mcp-server` MCP server configured, use it to look up the latest official AWS documentation when needed during deployment. This is especially useful for:
- Verifying current API parameters, CLI syntax, and service limits for OpenSearch Service
- Looking up IAM policy formats, Bedrock model IDs, and regional availability
- Checking the latest best practices for domain configuration, security, and networking
- Resolving errors or unexpected behavior during deployment steps

Search for relevant docs proactively (e.g. "OpenSearch Service create domain", "OpenSearch ML connectors Bedrock") rather than relying solely on the instructions below, which may become outdated.

## Prerequisites

Before starting Phase 5 deployment:
1. AWS credentials configured (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION)
2. Appropriate IAM permissions for OpenSearch Service
3. Successful Phase 4 execution with local OpenSearch running
4. Search strategy manifest file created in Phase 3
5. VPC and subnet configuration (for VPC deployment)

## Deployment Steps

### Step 1: Create OpenSearch Domain

Use the AWS MCP server to create a domain:

```
aws opensearch create-domain
  --domain-name <domain-name>
  --engine-version OpenSearch_2.11 (or latest)
  --cluster-config <cluster-config>
  --ebs-options <ebs-options>
  --access-policies <access-policy>
  --node-to-node-encryption-options Enabled=true
  --encryption-at-rest-options Enabled=true
  --domain-endpoint-options EnforceHTTPS=true
```

### Step 2: Configure Cluster Topology

Choose instance types and cluster size based on workload:

**Development/Testing:**
- Instance type: t3.small.search or t3.medium.search
- Data nodes: 1-2
- Master nodes: Not required for small clusters

**Production:**
- Instance type: r6g.large.search or larger (memory-optimized for agentic workloads)
- Data nodes: 3+ (for high availability)
- Dedicated master nodes: 3 (recommended for production)
- UltraWarm nodes: Optional for cost optimization of older data

### Step 3: Configure Storage

Set up EBS volumes:

```json
{
  "EBSEnabled": true,
  "VolumeType": "gp3",
  "VolumeSize": 100,
  "Iops": 3000,
  "Throughput": 125
}
```

Size based on:
- Expected document count
- Index size from local testing
- Growth projections
- Replica requirements

### Step 4: Configure Network Access

Choose access configuration:

**Public Access (Development):**
- Set access policies with IP restrictions
- Use fine-grained access control

**VPC Access (Production - Recommended):**
- Deploy domain within VPC
- Configure security groups
- Set up VPC endpoints if needed
- Ensure proper subnet configuration

### Step 5: Enable Fine-Grained Access Control

Configure authentication and authorization:

```
aws opensearch update-domain-config
  --domain-name <domain-name>
  --advanced-security-options Enabled=true,InternalUserDatabaseEnabled=true,MasterUserOptions={...}
```

Set up:
- Master user credentials
- Role-based access control
- Backend roles mapping
- Index-level permissions

### Step 6: Wait for Domain to be Active

Poll domain status until active:

```
aws opensearch describe-domain
  --domain-name <domain-name>
```

Wait for:
- Processing: false
- DomainStatus: Active
- Endpoint available

This typically takes 10-15 minutes.

### Step 7: Migrate Index Configuration

Using the opensearch-mcp-server tools:

1. Get the local index configuration from the manifest
2. Create the index on the domain endpoint
3. Include all mappings, settings, and configurations
4. Configure replicas for high availability (typically 1-2 replicas)

### Step 8: Deploy ML Models

For agentic search with embeddings:

1. Deploy models to the OpenSearch cluster:
   - Use pretrained models from OpenSearch model repository
   - Or deploy custom models
2. Configure model settings (memory, inference threads)
3. Test model inference performance
4. Update pipeline configurations to use deployed models

### Step 9: Create Ingest Pipelines

Recreate ingest pipelines on the domain:

1. Get pipeline definitions from local setup
2. Create pipelines using opensearch-mcp-server
3. Attach pipelines to the index
4. Configure processors for agentic search requirements

### Step 10: Configure Search Pipelines (if applicable)

For advanced agentic search features:

1. Create search pipelines for query processing
2. Configure query rewriting and expansion
3. Set up custom scoring and ranking
4. Enable search relevance tuning

### Step 11: Configure Agentic Search (Required for Agentic Search Strategy)

**For non-agentic search strategies**: Skip this step.

**For Agentic Search**: Configure conversational agents with QueryPlanningTool to enable natural language search.

Agentic search allows users to ask questions in natural language and have OpenSearch automatically plan and execute the retrieval. This requires OpenSearch 3.3+ and uses Bedrock Claude as the reasoning model.

#### Step 11.1: Create IAM Role for Bedrock Access

Create an IAM role for OpenSearch to invoke Bedrock Claude models:

```json
POST /iam/CreateRole
{
  "RoleName": "opensearch-bedrock-agent-role",
  "AssumeRolePolicyDocument": {
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": {
        "Service": "opensearchservice.amazonaws.com"
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
  "RoleName": "opensearch-bedrock-agent-role",
  "PolicyName": "BedrockClaudeInvokePolicy",
  "PolicyDocument": {
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Action": "bedrock:InvokeModel",
      "Resource": "arn:aws:bedrock:*::foundation-model/anthropic.claude-3-5-sonnet-*"
    }]
  }
}
```

#### Step 11.2: Map ML Role (if using fine-grained access control)

If your domain uses fine-grained access control:

1. Log in to OpenSearch Dashboards
2. Navigate to Security > Roles
3. Select the `ml_full_access` role
4. Choose Mapped users > Manage mapping
5. Add the IAM role ARN under Backend roles:
   ```
   arn:aws:iam::<account-id>:role/opensearch-bedrock-agent-role
   ```
6. Click Map

#### Step 11.3: Create Bedrock Claude Connector

Create a connector to Bedrock Claude 3.5 Sonnet using the Converse API:

```
POST <domain-endpoint>/_plugins/_ml/connectors/_create
{
  "name": "Amazon Bedrock Claude 3.5 Sonnet",
  "description": "Connector for Bedrock Claude 3.5 Sonnet for agentic search",
  "version": 1,
  "protocol": "aws_sigv4",
  "credential": {
    "roleArn": "<opensearch-bedrock-agent-role-arn>"
  },
  "parameters": {
    "region": "<aws-region>",
    "service_name": "bedrock",
    "model": "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "system_prompt": "You are a helpful assistant that plans and executes search queries.",
    "temperature": 0.0,
    "top_p": 0.9,
    "max_tokens": 2000
  },
  "actions": [{
    "action_type": "predict",
    "method": "POST",
    "headers": {
      "content-type": "application/json"
    },
    "url": "https://bedrock-runtime.${parameters.region}.amazonaws.com/model/${parameters.model}/converse",
    "request_body": "{ \"system\": [{\"text\": \"${parameters.system_prompt}\"}], \"messages\": ${parameters.messages}, \"inferenceConfig\": {\"temperature\": ${parameters.temperature}, \"topP\": ${parameters.top_p}, \"maxTokens\": ${parameters.max_tokens}} }"
  }]
}
```

Note the `connector_id` from the response.

#### Step 11.4: Register and Deploy the Model

Register the model:

```
POST <domain-endpoint>/_plugins/_ml/models/_register?deploy=true
{
  "name": "Bedrock Claude 3.5 Sonnet for Agentic Search",
  "function_name": "remote",
  "description": "Claude 3.5 Sonnet model for query planning and reasoning",
  "connector_id": "<connector-id>"
}
```

Note the `model_id` from the response.

Test the model:

```
POST <domain-endpoint>/_plugins/_ml/models/<model-id>/_predict
{
  "parameters": {
    "messages": [{
      "role": "user",
      "content": [{
        "text": "hello"
      }]
    }]
  }
}
```

Verify the response contains Claude's generated text.

#### Step 11.5: Create Conversational Agent with QueryPlanningTool

Create a conversational agent that uses the QueryPlanningTool:

```
POST <domain-endpoint>/_plugins/_ml/agents/_register
{
  "name": "Agentic Search Agent",
  "type": "conversational",
  "description": "Agent for natural language search with automatic query planning",
  "llm": {
    "model_id": "<model-id>",
    "parameters": {
      "max_iteration": 15
    }
  },
  "memory": {
    "type": "conversation_index"
  },
  "parameters": {
    "_llm_interface": "bedrock/converse"
  },
  "tools": [{
    "type": "QueryPlanningTool"
  }],
  "app_type": "os_chat"
}
```

Key configuration:
- `type`: "conversational" for full agent capabilities (use "flow" for simpler, faster queries)
- `max_iteration`: Maximum reasoning steps (15 recommended)
- `embedding_model_id`: Required if using neural/semantic search
- `QueryPlanningTool`: Enables automatic query DSL generation from natural language

Note the `agent_id` from the response.

#### Step 11.6: Create Agentic Search Pipeline

Create a search pipeline with the agentic query translator:

```
PUT <domain-endpoint>/_search/pipeline/agentic-search-pipeline
{
  "request_processors": [{
    "agentic_query_translator": {
      "agent_id": "<agent-id>"
    }
  }]
}
```

#### Step 11.7: Test Agentic Search

Test the agentic search with a natural language query:

```
GET <domain-endpoint>/<index-name>/_search?search_pipeline=agentic-search-pipeline
{
  "query": {
    "agentic": {
      "query_text": "Find all documents about machine learning published in the last year",
      "query_fields": ["title", "content", "publish_date"]
    }
  }
}
```

The agent will:
1. Analyze the natural language question
2. Examine the index mapping
3. Generate appropriate OpenSearch DSL query
4. Execute the query and return results

#### Step 11.8: Enable Conversation Memory (Optional)

To enable multi-turn conversations:

Create a memory:

```
POST <domain-endpoint>/_plugins/_ml/memory/
{
  "name": "User conversation about search results"
}
```

Note the `memory_id` and include it in subsequent searches to maintain context across queries.

### Step 12: Index Sample Documents

Index test documents to verify the setup:

1. Use the same sample documents from Phase 1
2. For Agentic Search:
   - Test with natural language queries
   - Verify the agent generates appropriate DSL
   - Check that results match the intent
3. For other strategies: Verify embeddings and search functionality
4. Monitor query performance and agent reasoning traces

### Step 12: Configure Monitoring and Alerting

Set up observability:

1. Enable CloudWatch logs:
   - Index slow logs
   - Search slow logs
   - Error logs
   - Audit logs
2. Create CloudWatch alarms for:
   - Cluster health
   - CPU and memory utilization
   - Storage space
   - JVM pressure
3. Set up SNS notifications

### Step 13: Provide Access Information

Give the user:
- Domain endpoint URL
- Domain ARN
- OpenSearch Dashboards URL
- Master user credentials (securely)
- Sample agentic search queries to test
- Cost estimation based on instance types and configuration

## Cost Considerations

OpenSearch Domain pricing:
- Instance hours (varies by instance type)
- EBS storage (GB-month)
- Data transfer
- Snapshot storage (if enabled)

**Cost optimization tips:**
- Use reserved instances for production (up to 30% savings)
- Right-size instances based on actual usage
- Use UltraWarm for infrequently accessed data
- Enable automated snapshots to S3

Typical monthly cost for small production cluster:
- 3x r6g.large.search: ~$400-500/month
- 300GB EBS storage: ~$30/month
- Total: ~$450-550/month

## Security Best Practices

1. **Network Security:**
   - Deploy in VPC for production
   - Use security groups to restrict access
   - Enable VPC Flow Logs

2. **Access Control:**
   - Enable fine-grained access control
   - Use IAM roles for application access
   - Implement least-privilege policies
   - Rotate credentials regularly

3. **Encryption:**
   - Enable encryption at rest
   - Enable node-to-node encryption
   - Enforce HTTPS for all connections

4. **Monitoring:**
   - Enable all CloudWatch logs
   - Set up alerting for security events
   - Regular security audits

## High Availability and Disaster Recovery

1. **Multi-AZ Deployment:**
   - Enable zone awareness
   - Distribute nodes across 3 AZs
   - Configure standby replicas

2. **Backup Strategy:**
   - Enable automated snapshots
   - Configure snapshot repository in S3
   - Test restore procedures
   - Retain snapshots based on compliance requirements

3. **Disaster Recovery:**
   - Document recovery procedures
   - Set up cross-region replication if needed
   - Define RTO and RPO targets

## Performance Tuning for Agentic Search

1. **Index Settings:**
   - Optimize refresh interval
   - Configure appropriate shard count
   - Tune merge policies

2. **Query Optimization:**
   - Use query caching
   - Optimize aggregations
   - Implement request caching

3. **Resource Allocation:**
   - Monitor JVM heap usage
   - Adjust circuit breakers if needed
   - Configure thread pools for workload

## Troubleshooting

Common issues:

- **Domain creation fails**: Check service quotas, VPC configuration, IAM permissions
- **Cluster health yellow/red**: Check shard allocation, storage space, node health
- **Slow queries**: Review slow logs, optimize queries, check resource utilization
- **Model deployment fails**: Verify ML plugin enabled, check memory allocation
- **Access denied**: Verify fine-grained access control settings, IAM policies

## Next Steps

After successful deployment:

1. Update application code to use the domain endpoint
2. Implement connection pooling and retry logic
3. Set up comprehensive monitoring dashboards
4. Configure automated backups
5. Plan for capacity scaling
6. Document operational procedures
7. Train team on OpenSearch Dashboards
8. Implement performance testing and optimization
