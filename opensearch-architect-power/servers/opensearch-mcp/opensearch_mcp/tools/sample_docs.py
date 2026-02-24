"""
Sample Document Management Tools

Provides tools for managing sample documents with file-based persistence.
Sample documents are stored in ~/.opensearch-architect/samples/ directory.
"""

import json
import os
import re
import csv
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen


# Storage configuration
SAMPLE_STORAGE_DIR = Path.home() / ".opensearch-architect" / "samples"
SAMPLE_DOC_FILE = SAMPLE_STORAGE_DIR / "current_sample.json"
SAMPLE_METADATA_FILE = SAMPLE_STORAGE_DIR / "metadata.json"


def _ensure_storage_dir() -> None:
    """Ensure the storage directory exists."""
    SAMPLE_STORAGE_DIR.mkdir(parents=True, exist_ok=True)


def _save_sample_doc(doc: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> None:
    """Save sample document and metadata to disk."""
    _ensure_storage_dir()
    
    # Save the document
    with open(SAMPLE_DOC_FILE, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    
    # Save metadata if provided
    if metadata:
        with open(SAMPLE_METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)


def _load_sample_doc() -> Optional[Dict[str, Any]]:
    """Load sample document from disk."""
    if not SAMPLE_DOC_FILE.exists():
        return None
    
    try:
        with open(SAMPLE_DOC_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _load_metadata() -> Dict[str, Any]:
    """Load sample document metadata."""
    if not SAMPLE_METADATA_FILE.exists():
        return {}
    
    try:
        with open(SAMPLE_METADATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _clear_sample_doc() -> None:
    """Clear stored sample document and metadata."""
    if SAMPLE_DOC_FILE.exists():
        SAMPLE_DOC_FILE.unlink()
    if SAMPLE_METADATA_FILE.exists():
        SAMPLE_METADATA_FILE.unlink()


def _normalize_cell_value(value: str) -> Any:
    """Normalize cell value from CSV/TSV."""
    text = value.strip()
    if text in {"\\N", "NULL", "null", ""}:
        return None
    return text


def _extract_path_candidate(path_or_text: str) -> str:
    """Extract file path from text."""
    text = path_or_text.strip()
    if not text:
        return ""
    
    # Remove quotes
    raw = text.strip('"').strip("'")
    
    # Check if it looks like a path
    if (raw.startswith("~/") or raw.startswith("/") or 
        raw.startswith("./") or raw.startswith("../")):
        return raw.rstrip(").,;!?")
    
    # Check for file extensions
    if re.search(r'\.(tsv|tab|csv|jsonl|ndjson|json|txt)$', raw, re.IGNORECASE):
        return raw.rstrip(").,;!?")
    
    return ""


def _extract_url_candidate(url_or_text: str) -> str:
    """Extract URL from text."""
    raw = url_or_text.strip().strip('"').strip("'")
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw.rstrip(").,;")
    
    match = re.search(r"https?://[^\s]+", url_or_text)
    if match:
        return match.group(0).rstrip(").,;")
    
    return ""


def _load_sample_from_file(file_path: Path) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Load one sample record from a local file."""
    try:
        lines = []
        with file_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.rstrip("\r\n")
                if stripped:
                    lines.append(stripped)
                if len(lines) >= 2:
                    break
    except Exception as e:
        return None, f"Error reading file '{file_path}': {e}"
    
    if not lines:
        return None, f"Error: file '{file_path}' is empty."
    
    header_line = lines[0]
    sample_line = lines[1] if len(lines) > 1 else None
    extension = file_path.suffix.lower()
    
    # Determine delimiter
    delimiter = None
    if extension in {".tsv", ".tab"}:
        delimiter = "\t"
    elif extension == ".csv":
        delimiter = ","
    elif "\t" in header_line:
        delimiter = "\t"
    elif "," in header_line:
        delimiter = ","
    
    # Parse based on format
    if delimiter and sample_line:
        try:
            header = next(csv.reader([header_line], delimiter=delimiter))
            sample_row = next(csv.reader([sample_line], delimiter=delimiter))
            
            # Pad or trim row to match header
            if len(sample_row) < len(header):
                sample_row.extend([""] * (len(header) - len(sample_row)))
            if len(sample_row) > len(header):
                sample_row = sample_row[:len(header)]
            
            parsed_doc = {}
            for idx, key in enumerate(header):
                normalized_key = key.strip() or f"field_{idx + 1}"
                parsed_doc[normalized_key] = _normalize_cell_value(sample_row[idx])
            
            return parsed_doc, None
        except Exception as e:
            return None, f"Error parsing CSV/TSV: {e}"
    else:
        # Plain text or single line
        return {"content": sample_line or header_line}, None


# ============================================================================
# Public Tool Functions
# ============================================================================

def get_sample_doc() -> str:
    """
    Get the stored sample document.
    
    Returns:
        str: The sample document as JSON string, or error message if not set.
    """
    doc = _load_sample_doc()
    if doc is None:
        return "MISSING_SAMPLE_DOC"
    
    metadata = _load_metadata()
    
    # Include metadata in response if available
    if metadata:
        return json.dumps({
            "document": doc,
            "metadata": metadata
        }, ensure_ascii=False, indent=2)
    
    return json.dumps(doc, ensure_ascii=False, indent=2)


def submit_sample_doc(doc: str) -> str:
    """
    Store a sample document provided by the user.
    
    Args:
        doc: User-provided sample document, preferably JSON.
    
    Returns:
        str: Status message indicating success or validation error.
    """
    raw = doc.strip()
    if not raw:
        return "Error: sample doc is empty."
    
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return "Error: sample doc must be a JSON object."
    except json.JSONDecodeError:
        # Fallback: treat plain text as content field
        parsed = {"content": raw}
    
    metadata = {
        "source": "manual_input",
        "fields": list(parsed.keys())
    }
    
    _save_sample_doc(parsed, metadata)
    return f"Sample document stored with {len(parsed)} fields: {', '.join(list(parsed.keys())[:5])}"


def submit_sample_doc_from_local_file(path_or_text: str) -> str:
    """
    Load one sample record from a local file and store it.
    
    Args:
        path_or_text: Local file path or text containing a path.
    
    Returns:
        str: Status message with file info or error.
    """
    resolved_text = _extract_path_candidate(path_or_text)
    if not resolved_text:
        return "Error: could not detect a local file path."
    
    # Expand user home directory
    source_path = Path(os.path.expanduser(resolved_text)).expanduser()
    
    if not source_path.exists():
        return f"Error: local path not found: {source_path}"
    
    if not source_path.is_file():
        return f"Error: path is not a file: {source_path}"
    
    parsed_doc, error = _load_sample_from_file(source_path)
    if error:
        return error
    if not parsed_doc:
        return f"Error: failed to parse sample from '{source_path}'."
    
    metadata = {
        "source": "local_file",
        "source_path": str(source_path),
        "fields": list(parsed_doc.keys())
    }
    
    _save_sample_doc(parsed_doc, metadata)
    
    field_preview = ", ".join(list(parsed_doc.keys())[:8])
    return (
        f"Sample document loaded from '{source_path}'. "
        f"Detected fields: {field_preview}"
    )


def submit_sample_doc_from_url(url_or_text: str) -> str:
    """
    Download one sample record from a URL and store it.
    
    Args:
        url_or_text: HTTP/HTTPS URL or text containing a URL.
    
    Returns:
        str: Status message with URL info or error.
    """
    url = _extract_url_candidate(url_or_text)
    if not url:
        return "Error: could not detect an HTTP/HTTPS URL."
    
    parsed_url = urlparse(url)
    if parsed_url.scheme not in {"http", "https"}:
        return f"Error: unsupported URL scheme '{parsed_url.scheme}'."
    
    try:
        request = Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; OpenSearchAgent/1.0)"},
        )
        with urlopen(request, timeout=20) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            raw = response.read(1024 * 1024)  # Read first 1MB
    except Exception as e:
        return f"Error downloading URL '{url}': {e}"
    
    if not raw:
        return f"Error: downloaded content from '{url}' is empty."
    
    text = raw.decode(charset, errors="replace")
    if not text.strip():
        return f"Error: downloaded content from '{url}' is blank."
    
    # Try JSON first
    content_type = response.headers.get("Content-Type", "").lower()
    if "application/json" in content_type or parsed_url.path.lower().endswith(".json"):
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                parsed_doc = payload
            elif isinstance(payload, list) and payload:
                first = payload[0]
                parsed_doc = first if isinstance(first, dict) else {"content": str(first)}
            else:
                parsed_doc = {"content": text.splitlines()[0]}
            
            metadata = {
                "source": "url",
                "source_url": url,
                "fields": list(parsed_doc.keys())
            }
            
            _save_sample_doc(parsed_doc, metadata)
            field_preview = ", ".join(list(parsed_doc.keys())[:8])
            return f"Sample document loaded from URL '{url}'. Detected fields: {field_preview}"
        except json.JSONDecodeError:
            pass
    
    # Try line-based format
    lines = [line.rstrip("\r\n") for line in text.splitlines() if line.strip()]
    if not lines:
        return f"Error: no usable lines found in downloaded content from '{url}'."
    
    # Try JSONL
    first_line = lines[0].strip()
    if first_line.startswith("{"):
        try:
            parsed_doc = json.loads(first_line)
            if isinstance(parsed_doc, dict):
                metadata = {
                    "source": "url",
                    "source_url": url,
                    "fields": list(parsed_doc.keys())
                }
                _save_sample_doc(parsed_doc, metadata)
                field_preview = ", ".join(list(parsed_doc.keys())[:8])
                return f"Sample document loaded from URL '{url}'. Detected fields: {field_preview}"
        except json.JSONDecodeError:
            pass
    
    # Fallback to plain text
    parsed_doc = {"content": lines[0]}
    metadata = {
        "source": "url",
        "source_url": url,
        "fields": ["content"]
    }
    _save_sample_doc(parsed_doc, metadata)
    return f"Sample document loaded from URL '{url}'. Detected fields: content"


def clear_sample_doc() -> str:
    """
    Clear the stored sample document.
    
    Returns:
        str: Confirmation message.
    """
    _clear_sample_doc()
    return "Sample document cleared."
