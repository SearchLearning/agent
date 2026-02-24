"""
Verification and Cleanup Tools

Provides tools for managing verification documents and search UI.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional
from .opensearch_client import get_opensearch_client


# Storage for verification document tracking
VERIFICATION_STORAGE_DIR = Path.home() / ".opensearch-architect" / "verification"
VERIFICATION_TRACKER_FILE = VERIFICATION_STORAGE_DIR / "doc_tracker.json"


def _ensure_verification_storage() -> None:
    """Ensure verification storage directory exists."""
    VERIFICATION_STORAGE_DIR.mkdir(parents=True, exist_ok=True)


def _load_verification_tracker() -> Dict[str, List[str]]:
    """Load verification document tracker."""
    if not VERIFICATION_TRACKER_FILE.exists():
        return {}
    
    try:
        with open(VERIFICATION_TRACKER_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_verification_tracker(tracker: Dict[str, List[str]]) -> None:
    """Save verification document tracker."""
    _ensure_verification_storage()
    with open(VERIFICATION_TRACKER_FILE, "w", encoding="utf-8") as f:
        json.dump(tracker, f, ensure_ascii=False, indent=2)


def cleanup_verification_docs(index_name: Optional[str] = None) -> str:
    """
    Remove verification documents from an index.
    
    Args:
        index_name: Index name to clean up. If None, cleans all tracked indexes.
    
    Returns:
        str: Status message with cleanup results
    """
    try:
        # Get OpenSearch client
        try:
            client = get_opensearch_client()
        except Exception as e:
            return f"Error: Could not connect to OpenSearch: {e}"
        
        # Load tracker
        tracker = _load_verification_tracker()
        
        if not tracker:
            return "No verification documents tracked for cleanup."
        
        # Determine which indexes to clean
        if index_name:
            if index_name not in tracker:
                return f"No verification documents tracked for index '{index_name}'."
            indexes_to_clean = {index_name: tracker[index_name]}
        else:
            indexes_to_clean = tracker
        
        # Clean up documents
        total_deleted = 0
        results = []
        
        for idx_name, doc_ids in indexes_to_clean.items():
            deleted_count = 0
            for doc_id in doc_ids:
                try:
                    client.delete(index=idx_name, id=doc_id, ignore=[404])
                    deleted_count += 1
                except Exception:
                    pass
            
            if deleted_count > 0:
                try:
                    client.indices.refresh(index=idx_name)
                except Exception:
                    pass
            
            total_deleted += deleted_count
            results.append(f"  - {idx_name}: {deleted_count} documents")
            
            # Remove from tracker
            if index_name:
                tracker.pop(index_name, None)
            else:
                tracker.pop(idx_name, None)
        
        # Save updated tracker
        _save_verification_tracker(tracker)
        
        result_text = "\n".join(results)
        return (
            f"✅ Cleanup complete. Deleted {total_deleted} verification documents:\n"
            f"{result_text}"
        )
    
    except Exception as e:
        return f"Error during cleanup: {e}"


def apply_capability_driven_verification(
    worker_output: str,
    index_name: str,
    count: int = 10,
    id_prefix: str = "verification"
) -> str:
    """
    Index verification documents based on search capabilities.
    
    This is a simplified version that indexes sample documents for testing.
    The full capability-driven selection logic can be added later.
    
    Args:
        worker_output: Worker output containing search capabilities
        index_name: Target index name
        count: Number of documents to index (1-100)
        id_prefix: Prefix for document IDs
    
    Returns:
        str: JSON string with verification results
    """
    try:
        from .sample_docs import get_sample_doc
        
        effective_count = max(1, min(count, 100))
        
        result = {
            "applied": False,
            "index_name": index_name,
            "capabilities": [],
            "indexed_count": 0,
            "doc_ids": [],
            "notes": []
        }
        
        if not index_name:
            result["notes"] = ["index_name is required"]
            return json.dumps(result, ensure_ascii=False, indent=2)
        
        # Get OpenSearch client
        try:
            client = get_opensearch_client()
        except Exception as e:
            result["notes"] = [f"Could not connect to OpenSearch: {e}"]
            return json.dumps(result, ensure_ascii=False, indent=2)
        
        # Verify index exists
        if not client.indices.exists(index=index_name):
            result["notes"] = [f"Index '{index_name}' does not exist"]
            return json.dumps(result, ensure_ascii=False, indent=2)
        
        # Get sample document
        sample_doc_str = get_sample_doc()
        if sample_doc_str == "MISSING_SAMPLE_DOC":
            result["notes"] = ["No sample document available"]
            return json.dumps(result, ensure_ascii=False, indent=2)
        
        # Parse sample document
        try:
            sample_data = json.loads(sample_doc_str)
            if "document" in sample_data:
                sample_doc = sample_data["document"]
            else:
                sample_doc = sample_data
        except json.JSONDecodeError:
            result["notes"] = ["Could not parse sample document"]
            return json.dumps(result, ensure_ascii=False, indent=2)
        
        # Clean up existing verification docs for this index
        tracker = _load_verification_tracker()
        existing_ids = tracker.get(index_name, [])
        for existing_id in existing_ids:
            try:
                client.delete(index=index_name, id=existing_id, ignore=[404])
            except Exception:
                pass
        
        # Index verification documents
        indexed_ids = []
        for i in range(1, effective_count + 1):
            doc_id = f"{id_prefix}-{i}"
            try:
                # Create a variant of the sample doc
                doc_to_index = dict(sample_doc)
                doc_to_index["_verification_id"] = doc_id
                doc_to_index["_verification_index"] = i
                
                client.index(index=index_name, body=doc_to_index, id=doc_id)
                indexed_ids.append(doc_id)
            except Exception as e:
                result["notes"].append(f"Failed to index {doc_id}: {e}")
        
        # Refresh index
        if indexed_ids:
            try:
                client.indices.refresh(index=index_name)
            except Exception:
                pass
            
            # Update tracker
            tracker[index_name] = indexed_ids
            _save_verification_tracker(tracker)
        
        # Update result
        result["applied"] = bool(indexed_ids)
        result["indexed_count"] = len(indexed_ids)
        result["doc_ids"] = indexed_ids
        
        if not result["notes"]:
            result["notes"] = [f"Successfully indexed {len(indexed_ids)} verification documents"]
        
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    except Exception as e:
        return json.dumps({
            "applied": False,
            "index_name": index_name,
            "capabilities": [],
            "indexed_count": 0,
            "doc_ids": [],
            "notes": [f"Error: {e}"]
        }, ensure_ascii=False, indent=2)
