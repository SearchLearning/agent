"""
Knowledge Base Tools

Provides read-only access to OpenSearch semantic search documentation and guides.
"""

from pathlib import Path


def _get_knowledge_dir() -> Path:
    """Get the knowledge directory path."""
    # Knowledge files are in the server's knowledge directory
    server_dir = Path(__file__).parent.parent.parent
    knowledge_dir = server_dir / "knowledge"
    return knowledge_dir


def read_knowledge_base() -> str:
    """
    Read the OpenSearch Semantic Search Guide.
    
    Returns:
        str: The content of the guide covering BM25, Dense Vector, Sparse Vector,
             Hybrid, algorithms (HNSW, IVF, etc.), cost profiles, and deployment options.
    """
    try:
        knowledge_dir = _get_knowledge_dir()
        filepath = knowledge_dir / "opensearch_semantic_search_guide.md"
        
        if not filepath.exists():
            return f"Error: Knowledge base file not found at {filepath}"
        
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error reading knowledge base: {e}"


def read_dense_vector_models() -> str:
    """
    Read the Dense Vector Models Guide.
    
    Returns:
        str: The content of the guide covering models for OpenSearch Node,
             SageMaker GPU, and External API services.
    """
    try:
        knowledge_dir = _get_knowledge_dir()
        filepath = knowledge_dir / "dense_vector_models.md"
        
        if not filepath.exists():
            return f"Error: Dense vector models guide not found at {filepath}"
        
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error reading dense vector models guide: {e}"


def read_sparse_vector_models() -> str:
    """
    Read the Sparse Vector Models Guide.
    
    Returns:
        str: The content of the guide covering models for Doc-Only and
             Bi-Encoder modes.
    """
    try:
        knowledge_dir = _get_knowledge_dir()
        filepath = knowledge_dir / "sparse_vector_models.md"
        
        if not filepath.exists():
            return f"Error: Sparse vector models guide not found at {filepath}"
        
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error reading sparse vector models guide: {e}"
