"""
OpenSearch Operations Tools

Provides tools for creating indexes, pipelines, and models.
"""

import json
from typing import Any, Dict, Optional
from .opensearch_client import get_opensearch_client


def create_index(index_name: str, body: str) -> str:
    """
    Create an OpenSearch index with the specified configuration.
    
    Args:
        index_name: Name of the index to create
        body: Index configuration as JSON string (settings and mappings)
    
    Returns:
        str: Success message or error details
    """
    try:
        # Parse the body
        try:
            index_body = json.loads(body)
        except json.JSONDecodeError as e:
            return f"Error: Invalid JSON in body: {e}"
        
        # Get OpenSearch client
        try:
            client = get_opensearch_client()
        except Exception as e:
            return f"Error: Could not connect to OpenSearch: {e}"
        
        # Check if index already exists
        if client.indices.exists(index=index_name):
            return f"Error: Index '{index_name}' already exists. Delete it first or use a different name."
        
        # Create the index
        response = client.indices.create(index=index_name, body=index_body)
        
        # Extract key info from response
        acknowledged = response.get("acknowledged", False)
        shards_ack = response.get("shards_acknowledged", False)
        
        if acknowledged:
            return (
                f"✅ Index '{index_name}' created successfully. "
                f"Shards acknowledged: {shards_ack}"
            )
        else:
            return f"⚠️  Index '{index_name}' creation returned: {response}"
    
    except Exception as e:
        return f"Error creating index '{index_name}': {e}"


def create_and_attach_pipeline(
    index_name: str,
    pipeline_type: str,
    pipeline_id: str,
    pipeline_body: str,
    is_hybrid_search: bool = False,
    hybrid_weights: Optional[str] = None
) -> str:
    """
    Create an ingest or search pipeline and attach it to an index.
    
    Args:
        index_name: Target index name
        pipeline_type: "ingest" or "search"
        pipeline_id: Pipeline identifier
        pipeline_body: Pipeline configuration as JSON string
        is_hybrid_search: Whether this is a hybrid search pipeline
        hybrid_weights: Hybrid weights as JSON array string (e.g., "[0.5, 0.5]")
    
    Returns:
        str: Success message or error details
    """
    try:
        # Parse pipeline body
        try:
            pipeline_config = json.loads(pipeline_body)
        except json.JSONDecodeError as e:
            return f"Error: Invalid JSON in pipeline_body: {e}"
        
        # Parse hybrid weights if provided
        weights = None
        if hybrid_weights:
            try:
                weights = json.loads(hybrid_weights)
                if not isinstance(weights, list) or len(weights) != 2:
                    return "Error: hybrid_weights must be a JSON array with 2 elements"
            except json.JSONDecodeError as e:
                return f"Error: Invalid JSON in hybrid_weights: {e}"
        
        # Get OpenSearch client
        try:
            client = get_opensearch_client()
        except Exception as e:
            return f"Error: Could not connect to OpenSearch: {e}"
        
        # Verify index exists
        if not client.indices.exists(index=index_name):
            return f"Error: Index '{index_name}' does not exist. Create it first."
        
        # Create the pipeline
        if pipeline_type == "ingest":
            client.ingest.put_pipeline(id=pipeline_id, body=pipeline_config)
            
            # Attach to index as default ingest pipeline
            client.indices.put_settings(
                index=index_name,
                body={"index.default_pipeline": pipeline_id}
            )
            
            return (
                f"✅ Ingest pipeline '{pipeline_id}' created and attached to index '{index_name}' "
                f"as default_pipeline"
            )
        
        elif pipeline_type == "search":
            # For hybrid search, add normalization and combination if not present
            if is_hybrid_search and weights:
                if "request_processors" not in pipeline_config:
                    pipeline_config["request_processors"] = []
                if "response_processors" not in pipeline_config:
                    pipeline_config["response_processors"] = []
                
                # Add normalization processor if not present
                has_normalization = any(
                    "normalization-processor" in str(proc)
                    for proc in pipeline_config.get("response_processors", [])
                )
                if not has_normalization:
                    pipeline_config["response_processors"].append({
                        "normalization-processor": {
                            "normalization": {"technique": "min_max"},
                            "combination": {
                                "technique": "arithmetic_mean",
                                "parameters": {"weights": weights}
                            }
                        }
                    })
            
            client.search_pipeline.put(id=pipeline_id, body=pipeline_config)
            
            # Attach to index as default search pipeline
            client.indices.put_settings(
                index=index_name,
                body={"index.search.default_pipeline": pipeline_id}
            )
            
            weight_info = f" with weights {weights}" if is_hybrid_search and weights else ""
            return (
                f"✅ Search pipeline '{pipeline_id}' created{weight_info} and attached to "
                f"index '{index_name}' as search.default_pipeline"
            )
        
        else:
            return f"Error: Invalid pipeline_type '{pipeline_type}'. Must be 'ingest' or 'search'."
    
    except Exception as e:
        return f"Error creating/attaching pipeline '{pipeline_id}': {e}"


def create_bedrock_embedding_model(
    model_name: str,
    model_id: str,
    dimensions: int,
    region: str = "us-east-1"
) -> str:
    """
    Create a Bedrock embedding model connector in OpenSearch.
    
    Args:
        model_name: Name for the model in OpenSearch
        model_id: Bedrock model ID (e.g., "amazon.titan-embed-text-v2")
        dimensions: Embedding dimensions
        region: AWS region
    
    Returns:
        str: Success message with model ID or error details
    """
    try:
        # Get OpenSearch client
        try:
            client = get_opensearch_client()
        except Exception as e:
            return f"Error: Could not connect to OpenSearch: {e}"
        
        # Create connector
        connector_body = {
            "name": f"{model_name}_connector",
            "description": f"Bedrock connector for {model_id}",
            "version": "1",
            "protocol": "aws_sigv4",
            "parameters": {
                "region": region,
                "service_name": "bedrock"
            },
            "credential": {
                "roleArn": "arn:aws:iam::*:role/*"  # Will use instance profile or env credentials
            },
            "actions": [
                {
                    "action_type": "predict",
                    "method": "POST",
                    "url": f"https://bedrock-runtime.{region}.amazonaws.com/model/{model_id}/invoke",
                    "headers": {
                        "content-type": "application/json"
                    },
                    "request_body": "{\"inputText\": \"${parameters.inputText}\"}"
                }
            ]
        }
        
        # Create connector
        connector_response = client.transport.perform_request(
            "POST",
            "/_plugins/_ml/connectors/_create",
            body=connector_body
        )
        connector_id = connector_response.get("connector_id")
        
        if not connector_id:
            return f"Error: Failed to create connector: {connector_response}"
        
        # Register model
        model_body = {
            "name": model_name,
            "function_name": "remote",
            "description": f"Bedrock {model_id} embedding model",
            "connector_id": connector_id
        }
        
        model_response = client.transport.perform_request(
            "POST",
            "/_plugins/_ml/models/_register",
            body=model_body
        )
        model_id_response = model_response.get("model_id")
        
        if not model_id_response:
            return f"Error: Failed to register model: {model_response}"
        
        # Deploy model
        deploy_response = client.transport.perform_request(
            "POST",
            f"/_plugins/_ml/models/{model_id_response}/_deploy"
        )
        
        return (
            f"✅ Bedrock model '{model_name}' created successfully.\n"
            f"Model ID: {model_id_response}\n"
            f"Connector ID: {connector_id}\n"
            f"Dimensions: {dimensions}\n"
            f"Status: {deploy_response.get('status', 'deployed')}"
        )
    
    except Exception as e:
        return f"Error creating Bedrock model '{model_name}': {e}"


def create_local_pretrained_model(
    model_name: str,
    model_id: str,
    model_format: str = "TORCH_SCRIPT",
    embedding_dimension: Optional[int] = None
) -> str:
    """
    Create a local pretrained model in OpenSearch (for sparse or dense models).
    
    Args:
        model_name: Name for the model in OpenSearch
        model_id: Pretrained model ID (e.g., "amazon/neural-sparse/opensearch-neural-sparse-encoding-doc-v2-mini")
        model_format: Model format ("TORCH_SCRIPT" or "ONNX")
        embedding_dimension: Embedding dimensions (for dense models)
    
    Returns:
        str: Success message with model ID or error details
    """
    try:
        # Get OpenSearch client
        try:
            client = get_opensearch_client()
        except Exception as e:
            return f"Error: Could not connect to OpenSearch: {e}"
        
        # Register model
        model_body = {
            "name": model_name,
            "version": "1.0.0",
            "model_format": model_format,
            "model_config": {
                "model_type": "bert",
                "embedding_dimension": embedding_dimension,
                "framework_type": "sentence_transformers"
            }
        }
        
        # Add model_id for pretrained models
        if model_id:
            model_body["model_id"] = model_id
        
        model_response = client.transport.perform_request(
            "POST",
            "/_plugins/_ml/models/_register",
            body=model_body
        )
        model_id_response = model_response.get("model_id")
        
        if not model_id_response:
            return f"Error: Failed to register model: {model_response}"
        
        # Deploy model
        deploy_response = client.transport.perform_request(
            "POST",
            f"/_plugins/_ml/models/{model_id_response}/_deploy"
        )
        
        dim_info = f"\nDimensions: {embedding_dimension}" if embedding_dimension else ""
        return (
            f"✅ Local pretrained model '{model_name}' created successfully.\n"
            f"Model ID: {model_id_response}\n"
            f"Format: {model_format}{dim_info}\n"
            f"Status: {deploy_response.get('status', 'deployed')}"
        )
    
    except Exception as e:
        return f"Error creating local pretrained model '{model_name}': {e}"


def delete_doc(index_name: str, doc_id: str) -> str:
    """
    Delete a document from an index.
    
    Args:
        index_name: Index name
        doc_id: Document ID to delete
    
    Returns:
        str: Success message or error details
    """
    try:
        # Get OpenSearch client
        try:
            client = get_opensearch_client()
        except Exception as e:
            return f"Error: Could not connect to OpenSearch: {e}"
        
        # Delete document
        response = client.delete(index=index_name, id=doc_id, ignore=[404])
        
        result = response.get("result", "")
        if result == "deleted":
            return f"✅ Document '{doc_id}' deleted from index '{index_name}'"
        elif result == "not_found":
            return f"⚠️  Document '{doc_id}' not found in index '{index_name}'"
        else:
            return f"Document deletion result: {result}"
    
    except Exception as e:
        return f"Error deleting document '{doc_id}': {e}"


def index_doc(index_name: str, doc: str, doc_id: str) -> str:
    """
    Index a single document into an OpenSearch index.
    
    Args:
        index_name: Name of the index
        doc: Document as JSON string
        doc_id: Document ID
    
    Returns:
        str: Document after ingest pipeline processing (JSON string) or error details
    """
    try:
        # Parse the document
        try:
            doc_body = json.loads(doc)
        except json.JSONDecodeError as e:
            return f"Error: Invalid JSON in document: {e}"
        
        # Get OpenSearch client
        try:
            client = get_opensearch_client()
        except Exception as e:
            return f"Error: Could not connect to OpenSearch: {e}"
        
        # Index the document
        try:
            client.index(index=index_name, body=doc_body, id=doc_id)
        except Exception as e:
            return f"Error indexing document: {e}"
        
        # Refresh the index to make the document searchable
        client.indices.refresh(index=index_name)
        
        # Retrieve the document to show the result after ingest pipeline processing
        try:
            response = client.get(index=index_name, id=doc_id)
            return json.dumps(response, indent=2, ensure_ascii=False)
        except Exception as e:
            return f"✅ Document indexed successfully, but error retrieving it: {e}"
    
    except Exception as e:
        return f"Error: {e}"
