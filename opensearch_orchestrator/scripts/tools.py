import json
import os
import re
import csv
import math
import sys
from importlib import resources as importlib_resources
from typing import Any, Dict, Optional
from pathlib import Path
from html import unescape
from urllib.parse import parse_qs, quote_plus, urlparse
from urllib.request import Request, urlopen

from opensearch_orchestrator.scripts.shared import (
    SUPPORTED_SAMPLE_FILE_EXTENSION_REGEX,
    SUPPORTED_SAMPLE_FILE_FORMATS_COMMA,
    text_richness_score,
)


_LOCAL_PATH_WITH_SUPPORTED_EXTENSION_PATTERN = re.compile(
    rf"[^\s,;]+(?:{SUPPORTED_SAMPLE_FILE_EXTENSION_REGEX})\b",
    flags=re.IGNORECASE,
)
_INLINE_RELATIVE_PATH_WITH_SUPPORTED_EXTENSION_PATTERN = re.compile(
    rf"(?:\./|\../)?[^\s,;]+(?:{SUPPORTED_SAMPLE_FILE_EXTENSION_REGEX})\b",
    flags=re.IGNORECASE,
)

BUILTIN_IMDB_SAMPLE_PATH = "opensearch_orchestrator/scripts/sample_data/imdb.title.basics.tsv"
_LEGACY_BUILTIN_IMDB_SAMPLE_PATH = "scripts/sample_data/imdb.title.basics.tsv"
_KNOWLEDGE_PACKAGE_ROOT = ("scripts", "knowledge")
_SAMPLE_DATA_PACKAGE_ROOT = ("scripts", "sample_data")


def _read_packaged_text_file(*parts: str) -> str:
    return importlib_resources.files("opensearch_orchestrator").joinpath(*parts).read_text(
        encoding="utf-8"
    )


def _looks_like_builtin_imdb_path(path_text: str) -> bool:
    normalized = (path_text or "").strip().strip('"').strip("'")
    return normalized in {BUILTIN_IMDB_SAMPLE_PATH, _LEGACY_BUILTIN_IMDB_SAMPLE_PATH}


def _resolve_builtin_imdb_sample_file() -> Path:
    # Installed wheels are unpacked to site-packages, so this resolves to a real file.
    candidate = Path(__file__).resolve().parent / "sample_data" / "imdb.title.basics.tsv"
    if candidate.exists():
        return candidate
    # Fallback for unusual import loaders where __file__ path differs.
    traversable = importlib_resources.files("opensearch_orchestrator").joinpath(
        *_SAMPLE_DATA_PACKAGE_ROOT, "imdb.title.basics.tsv"
    )
    return Path(str(traversable))


def _resolve_source_local_file_path(source_local_file: str) -> Path | None:
    if not source_local_file:
        return None
    if _looks_like_builtin_imdb_path(source_local_file):
        return _resolve_builtin_imdb_sample_file()
    return Path(source_local_file)


def normalize_ingest_source_field_hints(
    fields: list[str] | tuple[str, ...] | str | None,
) -> tuple[str, ...]:
    """Normalize source field hints into a deduplicated, lowercased tuple.

    Accepts a list, tuple, comma-separated string, or None.
    """
    if fields is None:
        return ()

    if isinstance(fields, str):
        fields = [f.strip() for f in fields.split(",") if f.strip()]

    if not fields:
        return ("text",)

    normalized: list[str] = []
    seen: set[str] = set()
    for field in fields:
        name = str(field).strip().lower()
        if not name or name in seen:
            continue
        seen.add(name)
        normalized.append(name)

    return tuple(normalized) if normalized else ()



def read_knowledge_base() -> str:
    """Read the OpenSearch Semantic Search Guide to retrieve detailed information about search methods.

    Returns:
        str: The content of the guide covering BM25, Dense Vector, Sparse Vector, Hybrid, algorithms (HNSW, IVF, etc.), cost profiles, and deployment options.
    """
    try:
        return _read_packaged_text_file(
            *_KNOWLEDGE_PACKAGE_ROOT,
            "opensearch_semantic_search_guide.md",
        )
    except Exception as e:
        return f"Error reading knowledge base: {e}"


def read_dense_vector_models() -> str:
    """Read the Dense Vector Models Guide to retrieve available models for Dense Vector Search.

    Returns:
        str: The content of the guide covering models for OpenSearch Node, SageMaker GPU, and External API services.
    """
    try:
        return _read_packaged_text_file(
            *_KNOWLEDGE_PACKAGE_ROOT,
            "dense_vector_models.md",
        )
    except Exception as e:
        return f"Error reading dense vector models guide: {e}"


def read_sparse_vector_models() -> str:
    """Read the Sparse Vector Models Guide to retrieve available models for Sparse Vector Search.

    Returns:
        str: The content of the guide covering models for Doc-Only and Bi-Encoder modes.
    """
    try:
        return _read_packaged_text_file(
            *_KNOWLEDGE_PACKAGE_ROOT,
            "sparse_vector_models.md",
        )
    except Exception as e:
        return f"Error reading sparse vector models guide: {e}"



def submit_sample_doc(doc: str) -> str:
    """Parse a sample document provided by the user.

    Args:
        doc: User-provided sample document, preferably JSON.

    Returns:
        str: JSON string with ``sample_doc`` on success, or an error message.
    """
    raw = doc.strip()
    if not raw:
        return "Error: sample doc is empty."

    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return "Error: sample doc must be a JSON object."
    except json.JSONDecodeError:
        parsed = {"content": raw}

    return json.dumps(
        {"status": "Sample document stored.", "sample_doc": parsed},
        ensure_ascii=False,
    )


def _expand_local_path(path_text: str) -> Path:
    return Path(os.path.expandvars(path_text)).expanduser()


def _strip_trailing_path_punctuation(path_text: str) -> str:
    # Sentence punctuation often follows pasted paths in free-form prompts.
    return path_text.rstrip(").,;!?")


def _extract_path_candidate(path_or_text: str) -> str:
    text = path_or_text.strip()
    if not text:
        return ""

    candidates: list[str] = []

    raw = text.strip('"').strip("'")
    if raw and (
        raw.startswith("~/")
        or raw.startswith("/")
        or raw.startswith("./")
        or raw.startswith("../")
        or _LOCAL_PATH_WITH_SUPPORTED_EXTENSION_PATTERN.fullmatch(raw)
    ):
        candidates.append(raw)

    # Quoted path support, including spaces in file names.
    for match in re.finditer(r"(['\"])([^'\"]+)\1", text):
        quoted = match.group(2).strip()
        if quoted.startswith("~/") or quoted.startswith("/"):
            candidates.append(quoted)

    # Unquoted inline path token inside free-form text.
    for match in re.finditer(r"(~\/[^\s,;]+|\/[^\s,;]+)", text):
        candidates.append(match.group(1))
    for match in _INLINE_RELATIVE_PATH_WITH_SUPPORTED_EXTENSION_PATTERN.finditer(text):
        candidates.append(match.group(0))

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        cleaned = candidate.strip().strip('"').strip("'")
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)

    if not deduped:
        return ""

    fallback_token = _strip_trailing_path_punctuation(deduped[0]) or deduped[0]
    for candidate in deduped:
        variants = [candidate]
        stripped = _strip_trailing_path_punctuation(candidate)
        if stripped and stripped != candidate:
            variants.append(stripped)

        for variant in variants:
            resolved = _expand_local_path(variant)
            if resolved.exists():
                return str(resolved)
    return str(_expand_local_path(fallback_token))


def _extract_url_candidate(url_or_text: str) -> str:
    raw = url_or_text.strip().strip('"').strip("'")
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw.rstrip(").,;")

    match = re.search(r"https?://[^\s]+", url_or_text)
    if not match:
        return ""
    return match.group(0).rstrip(").,;")


def _strip_trailing_index_punctuation(index_text: str) -> str:
    # Free-form prompts often end index names with sentence punctuation.
    return index_text.rstrip(").,;!?:")


def _extract_index_candidate(index_or_text: str) -> str:
    text = (index_or_text or "").strip()
    if not text:
        return ""

    stop_words = {
        "please",
        "data",
        "dataset",
        "localhost",
        "opensearch",
        "local",
        "existing",
        "already",
        "index",
        "indices",
        "this",
        "that",
        "there",
        "here",
        "from",
        "with",
    }

    raw = _strip_trailing_index_punctuation(text.strip('"').strip("'"))
    if re.fullmatch(r"[A-Za-z0-9._-]+", raw) and raw.lower() not in stop_words:
        return raw

    url_candidate = _extract_url_candidate(text)
    if url_candidate:
        parsed = urlparse(url_candidate)
        if parsed.path:
            for segment in [p for p in parsed.path.split("/") if p]:
                if segment.startswith("_"):
                    continue
                sanitized_segment = _strip_trailing_index_punctuation(segment)
                if (
                    re.fullmatch(r"[A-Za-z0-9._-]+", sanitized_segment)
                    and sanitized_segment.lower() not in stop_words
                ):
                    return sanitized_segment

    patterns = (
        r"(?:index[_\s-]*name)\s*(?:=|:)?\s*([A-Za-z0-9._-]+)",
        r"(?:index)\s*(?:=|:)\s*([A-Za-z0-9._-]+)",
        r"(?:index)\s+(?!name\b)([A-Za-z0-9._-]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            candidate = _strip_trailing_index_punctuation(match.group(1).strip())
            if (
                re.fullmatch(r"[A-Za-z0-9._-]+", candidate)
                and candidate.lower() not in stop_words
            ):
                return candidate

    return ""


def _list_localhost_non_system_indices(opensearch_client) -> tuple[list[tuple[str, int]], Optional[str]]:
    try:
        indices = opensearch_client.cat.indices(format="json")
    except Exception as e:
        return [], f"Error: failed to list local indices: {e}"

    ranked_indices: list[tuple[str, int]] = []
    for item in indices or []:
        name = str(item.get("index", "")).strip()
        if not name or name.startswith("."):
            continue
        raw_count = str(item.get("docs.count", "0")).replace(",", "").strip()
        try:
            count = int(float(raw_count)) if raw_count else 0
        except ValueError:
            count = 0
        ranked_indices.append((name, max(0, count)))

    if not ranked_indices:
        return [], "Error: no non-system indices found on localhost OpenSearch."
    ranked_indices.sort(key=lambda pair: pair[1], reverse=True)
    return ranked_indices, None


def _format_index_options(index_counts: list[tuple[str, int]], limit: int = 20) -> str:
    if not index_counts:
        return ""
    lines = [
        f"- {name} (docs={count:,})"
        for name, count in index_counts[: max(1, limit)]
    ]
    return "\n".join(lines)


_OPENSEARCH_AUTH_MODE_ENV = "OPENSEARCH_AUTH_MODE"
_OPENSEARCH_AUTH_MODE_DEFAULT = "default"
_OPENSEARCH_AUTH_MODE_NONE = "none"
_OPENSEARCH_AUTH_MODE_CUSTOM = "custom"
_OPENSEARCH_USER_ENV = "OPENSEARCH_USER"
_OPENSEARCH_PASSWORD_ENV = "OPENSEARCH_PASSWORD"
_OPENSEARCH_DEFAULT_USER = "admin"
_OPENSEARCH_DEFAULT_PASSWORD = "myStrongPassword123!"


def _resolve_localhost_http_auth_from_env() -> tuple[tuple[str, str] | None, str | None]:
    mode = str(os.getenv(_OPENSEARCH_AUTH_MODE_ENV, _OPENSEARCH_AUTH_MODE_DEFAULT) or "").strip().lower()
    if mode == _OPENSEARCH_AUTH_MODE_NONE:
        return None, None
    if mode == _OPENSEARCH_AUTH_MODE_CUSTOM:
        user = str(os.getenv(_OPENSEARCH_USER_ENV, "") or "").strip()
        password = str(os.getenv(_OPENSEARCH_PASSWORD_ENV, "") or "").strip()
        if not user or not password:
            return None, (
                "Error: OPENSEARCH_AUTH_MODE=custom requires OPENSEARCH_USER and OPENSEARCH_PASSWORD."
            )
        return (user, password), None
    return (_OPENSEARCH_DEFAULT_USER, _OPENSEARCH_DEFAULT_PASSWORD), None


def _create_local_opensearch_client():
    from opensearchpy import OpenSearch

    host = os.getenv("OPENSEARCH_HOST", "localhost")
    port = int(os.getenv("OPENSEARCH_PORT", "9200"))
    http_auth, auth_error = _resolve_localhost_http_auth_from_env()
    if auth_error:
        return None, auth_error

    def _build(use_ssl: bool) -> OpenSearch:
        kwargs = {
            "hosts": [{"host": host, "port": port}],
            "use_ssl": use_ssl,
            "verify_certs": False,
            "ssl_show_warn": False,
        }
        if http_auth is not None:
            kwargs["http_auth"] = http_auth
        return OpenSearch(**kwargs)

    errors: list[str] = []
    for use_ssl in (True, False):
        scheme = "https" if use_ssl else "http"
        client = _build(use_ssl=use_ssl)
        try:
            client.info()
            return client, None
        except Exception as e:
            errors.append(f"{scheme}: {e}")

    error_summary = "; ".join(errors) if errors else "unknown connection error"
    return None, (
        f"Error: unable to connect to local OpenSearch at {host}:{port}. "
        f"Tried HTTPS and HTTP. Details: {error_summary}"
    )


def _normalize_cell_value(value: str) -> Any:
    text = value.strip()
    if text in {"\\N", "NULL", "null", ""}:
        return None
    return text


_TABULAR_EXTENSIONS = {".tsv", ".tab", ".csv"}
_LINE_BASED_EXTENSIONS = _TABULAR_EXTENSIONS | {".jsonl", ".ndjson", ".txt"}
_PARQUET_EXTENSIONS = {".parquet"}
_SUPPORTED_LOCAL_DATA_EXTENSIONS = _LINE_BASED_EXTENSIONS | {".json"} | _PARQUET_EXTENSIONS
_SCRIPT_LABELS = {
    "latin": "Latin",
    "cyrillic": "Cyrillic",
    "cjk": "CJK",
    "japanese": "Japanese",
    "korean": "Korean",
    "arabic": "Arabic",
    "hebrew": "Hebrew",
    "devanagari": "Devanagari",
    "other_alpha": "Other alphabetic",
}


def _to_json_compatible_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {
            str(k): _to_json_compatible_value(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_to_json_compatible_value(item) for item in value]

    item_method = getattr(value, "item", None)
    if callable(item_method):
        try:
            return _to_json_compatible_value(item_method())
        except Exception:
            pass

    isoformat_method = getattr(value, "isoformat", None)
    if callable(isoformat_method):
        try:
            return isoformat_method()
        except Exception:
            pass

    return str(value)


def _normalize_record_for_json(record: dict[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    for idx, (key, value) in enumerate(record.items()):
        normalized_key = str(key).strip() or f"field_{idx + 1}"
        normalized[normalized_key] = _to_json_compatible_value(value)
    return normalized


def _extract_parquet_columns(candidate_path: Path) -> set[str]:
    try:
        import pyarrow.parquet as pq  # type: ignore[import-not-found]

        parquet_file = pq.ParquetFile(candidate_path)
        names = list(parquet_file.schema.names or [])
        return {str(name).strip().lower() for name in names if str(name).strip()}
    except Exception:
        return set()


def _load_records_from_parquet_file(
    file_path: Path, limit: int = 10
) -> tuple[list[Dict[str, Any]], Optional[str]]:
    effective_limit = max(1, min(limit, 200))
    docs: list[Dict[str, Any]] = []
    backend_errors: list[str] = []

    try:
        import pyarrow.parquet as pq  # type: ignore[import-not-found]

        table = pq.read_table(file_path)
        for record in table.slice(0, effective_limit).to_pylist():
            if isinstance(record, dict):
                docs.append(_normalize_record_for_json(record))
                if len(docs) >= effective_limit:
                    break
        if docs:
            return docs, None
    except Exception as e:
        backend_errors.append(f"pyarrow: {e}")

    try:
        import pandas as pd  # type: ignore[import-not-found]

        frame = pd.read_parquet(file_path)
        if not frame.empty:
            for record in frame.head(effective_limit).to_dict(orient="records"):
                if isinstance(record, dict):
                    docs.append(_normalize_record_for_json(record))
                    if len(docs) >= effective_limit:
                        break
        if docs:
            return docs, None
    except Exception as e:
        backend_errors.append(f"pandas: {e}")

    if backend_errors:
        return (
            [],
            f"Error reading parquet file '{file_path}': {'; '.join(backend_errors)}",
        )
    return (
        [],
        f"Error: parquet support requires pyarrow or pandas+parquet engine to read '{file_path}'.",
    )


def _estimate_parquet_record_count(file_path: Path) -> Optional[int]:
    try:
        import pyarrow.parquet as pq  # type: ignore[import-not-found]

        parquet_file = pq.ParquetFile(file_path)
        metadata = parquet_file.metadata
        if metadata is None:
            return None
        row_count = metadata.num_rows
        return max(0, int(row_count))
    except Exception:
        return None


def _pick_directory_sample_file(
    directory: Path,
    limit: int = 500,
    ingest_source_field_hints: tuple[str, ...] = (),
) -> tuple[Optional[Path], int]:
    def _extract_candidate_fields(candidate_path: Path) -> set[str]:
        extension = candidate_path.suffix.lower()

        try:
            if extension in _TABULAR_EXTENSIONS:
                with candidate_path.open("r", encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        header_line = line.rstrip("\r\n")
                        if not header_line:
                            continue
                        delimiter = "\t" if extension in {".tsv", ".tab"} else ","
                        header = next(csv.reader([header_line], delimiter=delimiter))
                        return {
                            col.strip().lower()
                            for col in header
                            if col and col.strip()
                        }

            if extension in {".jsonl", ".ndjson"}:
                with candidate_path.open("r", encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        raw = line.strip()
                        if not raw:
                            continue
                        parsed = json.loads(raw)
                        if isinstance(parsed, dict):
                            return {
                                str(key).strip().lower()
                                for key in parsed.keys()
                                if str(key).strip()
                            }
                        return set()

            if extension in _PARQUET_EXTENSIONS:
                return _extract_parquet_columns(candidate_path)
        except Exception:
            return set()

        return set()

    candidates: list[Path] = []
    try:
        for candidate in directory.rglob("*"):
            if candidate.is_file() and candidate.suffix.lower() in _SUPPORTED_LOCAL_DATA_EXTENSIONS:
                candidates.append(candidate)
                if len(candidates) >= limit:
                    break
    except Exception:
        pass

    if not candidates:
        return None, 0

    # most machine-parseable and schema-rich first
    extension_priority = {
        ".tsv": 0,
        ".tab": 1,
        ".csv": 2,
        ".jsonl": 3,
        ".ndjson": 4,
        ".parquet": 5,
        ".json": 6,
        ".txt": 7,
    }

    preferred_fields = set(ingest_source_field_hints)
    field_match_score: dict[Path, int] = {}
    for candidate in candidates:
        candidate_fields = _extract_candidate_fields(candidate)
        if not candidate_fields:
            field_match_score[candidate] = 0
            continue
        field_match_score[candidate] = len(preferred_fields & candidate_fields)

    # Candidate files are ranked by:
    # 1. Number of matched fields with pipeline source fields (higher first).
    # 2. Existing extension priority (.tsv, .tab, .csv, ...).
    # 3. Path name.
    candidates.sort(
        key=lambda p: (
            -field_match_score.get(p, 0),
            extension_priority.get(p.suffix.lower(), 99),
            str(p).lower(),
        )
    )
    return candidates[0], len(candidates)


def _count_lines_exact(file_path: Path) -> int:
    newline_count = 0
    total_bytes = 0
    last_byte = b""
    with file_path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            total_bytes += len(chunk)
            newline_count += chunk.count(b"\n")
            last_byte = chunk[-1:]

    if total_bytes == 0:
        return 0
    if newline_count == 0:
        return 1
    if last_byte != b"\n":
        newline_count += 1
    return newline_count


def _estimate_line_count(file_path: Path, sample_lines: int = 4000) -> int:
    try:
        total_size = file_path.stat().st_size
    except Exception:
        return 0

    if total_size == 0:
        return 0

    sampled_bytes = 0
    sampled_lines = 0
    try:
        with file_path.open("rb") as handle:
            for _ in range(sample_lines):
                line = handle.readline()
                if not line:
                    break
                if not line.strip():
                    continue
                sampled_bytes += len(line)
                sampled_lines += 1
    except Exception:
        return 0

    if sampled_lines == 0 or sampled_bytes == 0:
        return 0

    average_line_bytes = sampled_bytes / sampled_lines
    estimated_lines = int(total_size / max(average_line_bytes, 1.0))
    return max(sampled_lines, estimated_lines)


def _estimate_record_count(file_path: Path) -> tuple[Optional[int], str]:
    extension = file_path.suffix.lower()
    if extension in _PARQUET_EXTENSIONS:
        parquet_count = _estimate_parquet_record_count(file_path)
        if parquet_count is None:
            return None, "unknown"
        return parquet_count, "exact"

    if extension not in _LINE_BASED_EXTENSIONS:
        return None, "unknown"

    try:
        file_size = file_path.stat().st_size
    except Exception:
        return None, "unknown"

    if file_size <= 150 * 1024 * 1024:
        line_count = _count_lines_exact(file_path)
        method = "exact"
    else:
        line_count = _estimate_line_count(file_path)
        method = "estimated"

    header_rows = 1 if extension in _TABULAR_EXTENSIONS and line_count > 0 else 0
    return max(line_count - header_rows, 0), method




def _script_bucket(char: str) -> Optional[str]:
    code = ord(char)
    if "A" <= char <= "Z" or "a" <= char <= "z":
        return "latin"
    if 0x00C0 <= code <= 0x024F or 0x1E00 <= code <= 0x1EFF:
        return "latin"
    if 0x0400 <= code <= 0x052F:
        return "cyrillic"
    if 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
        return "cjk"
    if 0x3040 <= code <= 0x309F or 0x30A0 <= code <= 0x30FF:
        return "japanese"
    if 0xAC00 <= code <= 0xD7AF:
        return "korean"
    if 0x0600 <= code <= 0x06FF:
        return "arabic"
    if 0x0590 <= code <= 0x05FF:
        return "hebrew"
    if 0x0900 <= code <= 0x097F:
        return "devanagari"
    if char.isalpha():
        return "other_alpha"
    return None


def _infer_language_hint(parsed_doc: Dict[str, Any]) -> str:
    """Infer the likely language/script from a sample document.

    Instead of relying on hardcoded field name hints (which don't generalise
    across unknown customer schemas), we score each value by its text richness
    (alphabetic ratio, word count, length) and use the highest-scoring values
    for script detection.
    """
    # Collect all scalar string values with their text richness scores
    scored_texts: list[tuple[float, str]] = []
    for value in parsed_doc.values():
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        score = text_richness_score(text)
        if score > 0:
            scored_texts.append((score, text))

    # Sort by richness descending — most alphabetic, multi-word text first
    scored_texts.sort(key=lambda item: item[0], reverse=True)
    samples = [text for _, text in scored_texts]
    if not samples:
        return "insufficient text in sample row to infer language."

    script_counts: Dict[str, int] = {}
    for text in samples[:20]:
        for char in text:
            script = _script_bucket(char)
            if script is None:
                continue
            script_counts[script] = script_counts.get(script, 0) + 1

    if not script_counts:
        return "insufficient alphabetic signal in sample row to infer language."

    total = sum(script_counts.values())
    latin_ratio = script_counts.get("latin", 0) / total
    significant_non_latin = [
        _SCRIPT_LABELS.get(name, name)
        for name, count in script_counts.items()
        if name != "latin" and (count / total) >= 0.08
    ]

    if latin_ratio >= 0.9 and not significant_non_latin:
        return "sample appears predominantly Latin script (often English-like)."
    if latin_ratio >= 0.6:
        if significant_non_latin:
            return (
                "sample appears mostly Latin with additional scripts: "
                f"{', '.join(significant_non_latin[:3])}."
            )
        return "sample appears mostly Latin script."
    top_scripts = sorted(script_counts.items(), key=lambda item: item[1], reverse=True)
    top_labels = [_SCRIPT_LABELS.get(name, name) for name, _ in top_scripts[:3]]
    return f"sample appears non-Latin or multilingual (top scripts: {', '.join(top_labels)})."


def _load_sample_record_from_file(file_path: Path) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    if file_path.suffix.lower() in _PARQUET_EXTENSIONS:
        docs, parse_error = _load_records_from_parquet_file(file_path, limit=1)
        if parse_error is not None:
            return None, parse_error
        if not docs:
            return None, f"Error: no records found in file '{file_path}'."
        return docs[0], None

    try:
        lines = []
        with file_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.rstrip("\r\n")
                if stripped == "":
                    continue
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
    delimiter = None
    if extension in {".tsv", ".tab"}:
        delimiter = "\t"
    elif extension == ".csv":
        delimiter = ","
    elif "\t" in header_line:
        delimiter = "\t"
    elif "," in header_line:
        delimiter = ","

    parsed_doc: Dict[str, Any]
    if delimiter and sample_line is not None:
        header = next(csv.reader([header_line], delimiter=delimiter))
        sample_row = next(csv.reader([sample_line], delimiter=delimiter))

        if len(sample_row) < len(header):
            sample_row.extend([""] * (len(header) - len(sample_row)))
        if len(sample_row) > len(header):
            sample_row = sample_row[: len(header)]

        parsed_doc = {}
        for idx, key in enumerate(header):
            normalized_key = key.strip() or f"field_{idx + 1}"
            parsed_doc[normalized_key] = _normalize_cell_value(sample_row[idx])
    else:
        parsed_doc = {"content": sample_line or header_line}

    return parsed_doc, None


def _load_records_from_local_file(file_path: Path, limit: int = 10) -> tuple[list[Dict[str, Any]], Optional[str]]:
    effective_limit = max(1, min(limit, 200))
    if file_path.suffix.lower() in _PARQUET_EXTENSIONS:
        return _load_records_from_parquet_file(file_path, effective_limit)

    try:
        lines: list[str] = []
        with file_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.rstrip("\r\n")
                if stripped == "":
                    continue
                lines.append(stripped)
                if len(lines) >= effective_limit + 1:
                    break
    except Exception as e:
        return [], f"Error reading file '{file_path}': {e}"

    if not lines:
        return [], f"Error: file '{file_path}' is empty."

    header_line = lines[0]
    extension = file_path.suffix.lower()
    delimiter = None
    if extension in {".tsv", ".tab"}:
        delimiter = "\t"
    elif extension == ".csv":
        delimiter = ","
    elif "\t" in header_line:
        delimiter = "\t"
    elif "," in header_line:
        delimiter = ","

    docs: list[Dict[str, Any]] = []
    if delimiter and len(lines) > 1:
        header = next(csv.reader([header_line], delimiter=delimiter))
        for row_line in lines[1:]:
            sample_row = next(csv.reader([row_line], delimiter=delimiter))
            if len(sample_row) < len(header):
                sample_row.extend([""] * (len(header) - len(sample_row)))
            if len(sample_row) > len(header):
                sample_row = sample_row[: len(header)]

            parsed_doc: Dict[str, Any] = {}
            for idx, key in enumerate(header):
                normalized_key = key.strip() or f"field_{idx + 1}"
                parsed_doc[normalized_key] = _normalize_cell_value(sample_row[idx])
            docs.append(parsed_doc)
            if len(docs) >= effective_limit:
                break
    else:
        for line in lines[:effective_limit]:
            docs.append({"content": line})

    if not docs:
        return [], f"Error: no records found in file '{file_path}'."
    return docs, None


def _normalize_index_name(index_or_text: str) -> str:
    candidate = _extract_index_candidate(index_or_text)
    if candidate:
        return candidate

    raw = _strip_trailing_index_punctuation(
        (index_or_text or "").strip().strip('"').strip("'")
    )
    if raw and re.fullmatch(r"[A-Za-z0-9._-]+", raw):
        return raw
    return ""


def _load_records_from_localhost_index(
    index_or_text: str,
    limit: int = 10,
) -> tuple[list[Dict[str, Any]], Optional[str]]:
    effective_limit = max(1, min(limit, 200))
    index_name = _normalize_index_name(index_or_text)
    if not index_name:
        return [], "Error: could not detect a valid localhost index name."

    try:
        opensearch_client, connect_error = _create_local_opensearch_client()
    except Exception as e:
        return [], f"Error: failed to initialize OpenSearch client: {e}"

    if connect_error is not None or opensearch_client is None:
        return [], connect_error or "Error: unable to connect to local OpenSearch."

    try:
        exists = opensearch_client.indices.exists(index=index_name)
        if not bool(exists):
            return [], f"Error: index '{index_name}' was not found on local OpenSearch."
    except Exception as e:
        return [], f"Error: failed to validate index '{index_name}': {e}"

    try:
        response = opensearch_client.search(
            index=index_name,
            body={
                "size": effective_limit,
                "query": {"match_all": {}},
                "sort": [{"_doc": "asc"}],
                "track_total_hits": False,
            },
        )
    except Exception as e:
        return [], f"Error: failed to fetch sample documents from index '{index_name}': {e}"

    hits = (
        response.get("hits", {}).get("hits", [])
        if isinstance(response, dict)
        else []
    )
    if not hits:
        return [], f"Error: index '{index_name}' has no documents."

    docs: list[Dict[str, Any]] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        source = hit.get("_source")
        if isinstance(source, dict):
            docs.append(source)
        elif source is None:
            fallback = hit.get("fields")
            if isinstance(fallback, dict):
                docs.append(fallback)
            else:
                doc_id = str(hit.get("_id", "")).strip()
                if doc_id:
                    docs.append({"_id": doc_id})
        else:
            docs.append({"content": str(source)})
        if len(docs) >= effective_limit:
            break

    if not docs:
        return [], f"Error: index '{index_name}' has no readable documents."
    return docs[:effective_limit], None


def get_sample_docs_payload(
    limit: int = 10,
    sample_doc_json: str = "",
    source_local_file: str = "",
    source_index_name: str = "",
) -> list[Dict[str, Any]]:
    """Return sample docs for verification from local source when available.

    Args:
        limit: Maximum number of documents to return.
        sample_doc_json: JSON string of a single sample document.
        source_local_file: Path to the local file the sample was loaded from.
        source_index_name: Localhost OpenSearch index name used as sample source.
    """
    effective_limit = max(1, min(limit, 200))
    parsed_sample_doc: Dict[str, Any] | None = None
    detected_source_index = _normalize_index_name(source_index_name)

    parsed_payload: Any = None
    if sample_doc_json:
        try:
            parsed_payload = json.loads(sample_doc_json)
        except (json.JSONDecodeError, TypeError):
            parsed_payload = None

    if isinstance(parsed_payload, dict):
        payload_sample_doc = parsed_payload.get("sample_doc")
        if isinstance(payload_sample_doc, dict):
            parsed_sample_doc = payload_sample_doc
        elif "sample_doc" not in parsed_payload:
            parsed_sample_doc = parsed_payload

        if not detected_source_index and bool(parsed_payload.get("source_localhost_index")):
            detected_source_index = _normalize_index_name(
                str(parsed_payload.get("source_index_name", ""))
            )

    if source_local_file:
        file_path = _resolve_source_local_file_path(source_local_file)
        if file_path is None:
            file_path = Path(source_local_file)
        if file_path.exists() and file_path.is_file():
            docs, _ = _load_records_from_local_file(file_path, effective_limit)
            if docs:
                return docs[:effective_limit]

    if detected_source_index:
        docs, _ = _load_records_from_localhost_index(detected_source_index, effective_limit)
        if docs:
            return docs[:effective_limit]

    if isinstance(parsed_sample_doc, dict):
        return [parsed_sample_doc]
    return []



def get_sample_docs_for_verification(
    limit: int = 10,
    sample_doc_json: str = "",
    source_local_file: str = "",
    source_index_name: str = "",
) -> str:
    """Return sample docs for verification indexing as JSON array.

    Args:
        limit: Maximum number of documents to return.
        sample_doc_json: JSON string of a single sample document.
        source_local_file: Path to the local file the sample was loaded from.
        source_index_name: Localhost OpenSearch index name used as sample source.
    """
    return json.dumps(
        get_sample_docs_payload(
            limit=limit,
            sample_doc_json=sample_doc_json,
            source_local_file=source_local_file,
            source_index_name=source_index_name,
        ),
        ensure_ascii=False,
    )



def submit_sample_doc_from_local_file(
    path_or_text: str,
    ingest_source_field_hints: str = "",
) -> str:
    """Load one sample record from a local file or directory.

    Args:
        path_or_text: Local file/directory path, or text containing a path like
            ~/Downloads/data.tsv.
        ingest_source_field_hints: Optional comma-separated field names used to
            rank candidate files when *path_or_text* is a directory.

    Returns:
        str: JSON string with ``sample_doc`` and ``source_local_file`` on
            success, or a plain error message starting with ``Error:``.
    """
    resolved_text = _extract_path_candidate(path_or_text)
    if not resolved_text:
        return "Error: could not detect a local file path."

    source_display_path = resolved_text
    if _looks_like_builtin_imdb_path(resolved_text):
        source_path = _resolve_builtin_imdb_sample_file()
        source_display_path = BUILTIN_IMDB_SAMPLE_PATH
    else:
        source_path = Path(resolved_text)
    if not source_path.exists():
        return f"Error: local path not found: {source_path}"

    hints = normalize_ingest_source_field_hints(ingest_source_field_hints or None)

    directory_note = ""
    if source_path.is_dir():
        selected_file, file_count = _pick_directory_sample_file(
            source_path, ingest_source_field_hints=hints,
        )
        if selected_file is None:
            return (
                f"Error: no supported data files found under directory '{source_path}'. "
                f"Supported extensions: {SUPPORTED_SAMPLE_FILE_FORMATS_COMMA}"
            )
        file_path = selected_file
        directory_note = (
            f" Source directory: '{source_path}' (detected {file_count} supported data file(s)); "
            f"using '{file_path}' as the sample file."
        )
    elif source_path.is_file():
        file_path = source_path
    else:
        return f"Error: local path is neither a file nor a directory: {source_path}"

    parsed_doc, parse_error = _load_sample_record_from_file(file_path)
    if parse_error is not None:
        return parse_error
    if parsed_doc is None:
        return f"Error: failed to parse sample record from '{file_path}'."

    field_preview = ", ".join(list(parsed_doc.keys())[:8])
    record_count, count_method = _estimate_record_count(file_path)
    if record_count is None:
        count_note = "record count could not be inferred from this file type."
    else:
        extension = file_path.suffix.lower()
        if extension in _TABULAR_EXTENSIONS:
            count_prefix = "inferred records (excluding header)"
        elif extension in _PARQUET_EXTENSIONS:
            count_prefix = "records"
        else:
            count_prefix = "inferred records"
        method_suffix = "exact" if count_method == "exact" else "estimated"
        count_note = f"{count_prefix}: {record_count:,} ({method_suffix})."
    language_note = _infer_language_hint(parsed_doc)
    scope_note = (
        "Planning hint: treat this source as representative sample data and assume future ingestion growth; "
        "infer language strategy from observed content unless the user specifies otherwise."
    )
    status = (
        f"Sample document loaded from '{source_display_path if source_display_path == BUILTIN_IMDB_SAMPLE_PATH else file_path}'. "
        f"Detected fields: {field_preview}.{directory_note} "
        f"Data profile: {count_note} Language hint: {language_note} {scope_note}"
    )
    source_local_file = (
        BUILTIN_IMDB_SAMPLE_PATH
        if source_display_path == BUILTIN_IMDB_SAMPLE_PATH
        else str(file_path)
    )
    return json.dumps(
        {
            "status": status,
            "sample_doc": parsed_doc,
            "source_local_file": source_local_file,
        },
        ensure_ascii=False,
    )



def submit_sample_doc_from_localhost_index(index_or_text: str = "") -> str:
    """Load one sample record from a localhost OpenSearch index.

    Args:
        index_or_text: Optional index name or free-form text that references an
            index (for example, "use index movies").

    Returns:
        str: JSON string with ``sample_doc`` and ``source_index_name`` on
            success, or a plain error message starting with ``Error:``.
    """
    index_name = _extract_index_candidate(index_or_text)

    try:
        opensearch_client, connect_error = _create_local_opensearch_client()
    except Exception as e:
        return f"Error: failed to initialize OpenSearch client: {e}"

    if connect_error is not None or opensearch_client is None:
        return connect_error or "Error: unable to connect to local OpenSearch."

    ranked_indices: list[tuple[str, int]] | None = None
    if not index_name:
        ranked_indices, indices_error = _list_localhost_non_system_indices(opensearch_client)
        if indices_error:
            return indices_error
        options = _format_index_options(ranked_indices)
        return (
            "Error: option 3 selected but no index name was provided.\n"
            "Available non-system indices on localhost OpenSearch:\n"
            f"{options}\n"
            "Please choose one index name from this list and retry option 3."
        )

    try:
        exists = opensearch_client.indices.exists(index=index_name)
        if not bool(exists):
            if ranked_indices is None:
                ranked_indices, _ = _list_localhost_non_system_indices(opensearch_client)
            if ranked_indices:
                options = _format_index_options(ranked_indices)
                return (
                    f"Error: index '{index_name}' was not found on local OpenSearch.\n"
                    "Available non-system indices on localhost OpenSearch:\n"
                    f"{options}\n"
                    "Please choose one index name from this list and retry option 3."
                )
            return f"Error: index '{index_name}' was not found on local OpenSearch."
    except Exception as e:
        return f"Error: failed to validate index '{index_name}': {e}"

    exact_total_count: Optional[int] = None
    try:
        count_response = opensearch_client.count(
            index=index_name,
            body={"query": {"match_all": {}}},
        )
        raw_count = count_response.get("count") if isinstance(count_response, dict) else None
        if isinstance(raw_count, bool):
            exact_total_count = None
        elif isinstance(raw_count, (int, float)):
            exact_total_count = max(0, int(raw_count))
        elif isinstance(raw_count, str):
            cleaned = raw_count.replace(",", "").strip()
            if cleaned:
                exact_total_count = max(0, int(float(cleaned)))
    except Exception:
        # Keep sample loading robust even if count API is unavailable.
        exact_total_count = None

    try:
        response = opensearch_client.search(
            index=index_name,
            body={"size": 1, "query": {"match_all": {}}, "track_total_hits": True},
        )
    except Exception as e:
        return f"Error: failed to fetch sample document from index '{index_name}': {e}"

    hits = (
        response.get("hits", {}).get("hits", [])
        if isinstance(response, dict)
        else []
    )
    if not hits:
        return f"Error: index '{index_name}' has no documents."

    first_hit = hits[0] if isinstance(hits[0], dict) else {}
    source = first_hit.get("_source")
    if not isinstance(source, dict):
        if source is None:
            fallback = first_hit.get("fields")
            source = (
                fallback
                if isinstance(fallback, dict)
                else {"_id": first_hit.get("_id", "")}
            )
        else:
            source = {"content": str(source)}

    total_hits = (
        response.get("hits", {}).get("total", None)
        if isinstance(response, dict)
        else None
    )
    if isinstance(total_hits, dict):
        total_count = total_hits.get("value")
    elif isinstance(total_hits, int):
        total_count = total_hits
    else:
        total_count = None

    field_preview = ", ".join(list(source.keys())[:8]) or "(none)"
    if isinstance(exact_total_count, int):
        total_note = f"exact documents: {exact_total_count:,} (via count API)"
    elif isinstance(total_count, int):
        total_note = f"inferred documents from search response: {total_count:,}"
    else:
        total_note = "document count unavailable from search response"

    status = (
        f"Sample document loaded from localhost OpenSearch index '{index_name}'. "
        f"Detected fields: {field_preview}. Data profile: {total_note}."
    )
    payload: Dict[str, Any] = {
        "status": status,
        "sample_doc": source,
        "source_index_name": index_name,
        "source_localhost_index": True,
    }
    if isinstance(exact_total_count, int):
        payload["source_index_doc_count"] = exact_total_count
    return json.dumps(payload, ensure_ascii=False)


def submit_sample_doc_from_url(url_or_text: str) -> str:
    """Download one sample record from a URL.

    Args:
        url_or_text: HTTP/HTTPS URL, or text containing a URL.

    Returns:
        str: JSON string with ``sample_doc`` on success, or a plain error
            message starting with ``Error:``.
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
            content_type = (response.headers.get("Content-Type", "") or "").lower()
            raw = response.read(1024 * 1024)
    except Exception as e:
        return f"Error downloading URL '{url}': {e}"

    if not raw:
        return f"Error: downloaded content from '{url}' is empty."

    text = raw.decode(charset, errors="replace")
    if not text.strip():
        return f"Error: downloaded content from '{url}' is blank."

    def _success(parsed_doc: Dict[str, Any]) -> str:
        field_preview = ", ".join(list(parsed_doc.keys())[:8])
        status = (
            f"Sample document loaded from URL '{url}'. "
            f"Detected fields: {field_preview}"
        )
        return json.dumps(
            {"status": status, "sample_doc": parsed_doc},
            ensure_ascii=False,
        )

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
            return _success(parsed_doc)
        except json.JSONDecodeError:
            pass

    lines = [line.rstrip("\r\n") for line in text.splitlines() if line.strip()]
    if not lines:
        return f"Error: no usable lines found in downloaded content from '{url}'."

    first_line = lines[0].strip()
    if first_line.startswith("{"):
        try:
            parsed_doc = json.loads(first_line)
            if isinstance(parsed_doc, dict):
                return _success(parsed_doc)
        except json.JSONDecodeError:
            pass

    header_line = lines[0]
    sample_line = lines[1] if len(lines) > 1 else None
    extension = Path(parsed_url.path).suffix.lower()

    delimiter = None
    if extension in {".tsv", ".tab"}:
        delimiter = "\t"
    elif extension == ".csv":
        delimiter = ","
    elif "\t" in header_line:
        delimiter = "\t"
    elif "," in header_line:
        delimiter = ","

    parsed_doc: Dict[str, Any]
    if delimiter and sample_line is not None:
        header = next(csv.reader([header_line], delimiter=delimiter))
        sample_row = next(csv.reader([sample_line], delimiter=delimiter))

        if len(sample_row) < len(header):
            sample_row.extend([""] * (len(header) - len(sample_row)))
        if len(sample_row) > len(header):
            sample_row = sample_row[: len(header)]

        parsed_doc = {}
        for idx, key in enumerate(header):
            normalized_key = key.strip() or f"field_{idx + 1}"
            parsed_doc[normalized_key] = _normalize_cell_value(sample_row[idx])
    else:
        parsed_doc = {"content": sample_line or header_line}

    return _success(parsed_doc)

def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _decode_duckduckgo_redirect(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [None])[0]
        if target:
            return target
    return url



def search_opensearch_org(query: str, numberOfResults: int = 5) -> str:
    """Search from the OpenSearch documentation

    example query: "sparse_vector field parameters"

    This uses a site-restricted web query (`site:opensearch.org`) and returns
    the top matching results with title, URL, and snippet.

    Args:
        query: The query text to search for.
        numberOfResults: Max number of results to return.

    Returns:
        str: JSON string with query and filtered results from opensearch.org.
    """
    try:
        print(f"\033[91m[search_opensearch_org] Query: {query}\033[0m", file=sys.stderr)

        limited_results = max(1, min(numberOfResults, 10))
        search_query = quote_plus(f"site:opensearch.org {query}")
        # DuckDuckGo's HTML endpoint is easy to fetch/parse without API keys or
        # anti-bot/captcha friction that commonly affects automated Google SERP scraping.
        url = f"https://duckduckgo.com/html/?q={search_query}"
        request = Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; OpenSearchAgent/1.0)"},
        )

        with urlopen(request, timeout=15) as response:
            html = response.read().decode("utf-8", errors="ignore")

        title_matches = re.findall(
            r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        snippet_matches = re.findall(
            r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>|'
            r'<div[^>]*class="result__snippet"[^>]*>(.*?)</div>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        snippets = [left or right for left, right in snippet_matches]

        results = []
        for idx, (raw_href, raw_title) in enumerate(title_matches):
            href = _decode_duckduckgo_redirect(unescape(raw_href))
            netloc = urlparse(href).netloc.lower()
            if "opensearch.org" not in netloc:
                continue

            title = _normalize_text(unescape(_strip_html(raw_title)))
            snippet = ""
            if idx < len(snippets):
                snippet = _normalize_text(unescape(_strip_html(snippets[idx])))

            results.append(
                {
                    "title": title,
                    "url": href,
                    "snippet": snippet,
                }
            )
            if len(results) >= limited_results:
                break

        if not results:
            return json.dumps(
                {
                    "query": query,
                    "results": [],
                    "message": "No opensearch.org results found.",
                },
                ensure_ascii=False,
                indent=2,
            )

        return json.dumps(
            {
                "query": query,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return f"Error searching opensearch.org: {e}"
