import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import opensearch_orchestrator.orchestrator as orchestrator


def test_extract_localhost_index_options_from_error():
    error_text = (
        "Error: option 3 selected but no index name was provided.\n"
        "Available non-system indices on localhost OpenSearch:\n"
        "- yellow-tripdata (docs=10,000)\n"
        "- wikipedia_hybrid (docs=10)\n"
        "- top_queries-2026.02.21-95583 (docs=8)\n"
        "Please choose one index name from this list and retry option 3."
    )

    options = orchestrator._extract_localhost_index_options_from_error(error_text)

    assert options == [
        "yellow-tripdata",
        "wikipedia_hybrid",
        "top_queries-2026.02.21-95583",
    ]


def test_resolve_pending_localhost_index_selection_by_number():
    selected = orchestrator._resolve_pending_localhost_index_selection(
        "2)",
        ["yellow-tripdata", "wikipedia_hybrid", "wikipedia"],
    )
    assert selected == "wikipedia_hybrid"


def test_resolve_pending_localhost_index_selection_by_exact_name():
    selected = orchestrator._resolve_pending_localhost_index_selection(
        "yellow-tripdata",
        ["yellow-tripdata", "wikipedia_hybrid", "wikipedia"],
    )
    assert selected == "yellow-tripdata"


def test_resolve_pending_localhost_index_selection_from_sentence():
    selected = orchestrator._resolve_pending_localhost_index_selection(
        "Use index wikipedia_hybrid for option 3.",
        ["yellow-tripdata", "wikipedia_hybrid", "wikipedia"],
    )
    assert selected == "wikipedia_hybrid"


def test_detect_pasted_sample_content_from_json_object():
    message = '{"id":"1","content":"The quick brown fox jumps over the lazy dog."}'
    assert orchestrator._looks_like_pasted_sample_content(message)


def test_detect_pasted_sample_content_from_ndjson_lines():
    message = (
        '{"id":"1","title":"Example A","description":"Sample text A"}\n'
        '{"id":"2","title":"Example B","description":"Sample text B"}'
    )
    assert orchestrator._looks_like_pasted_sample_content(message)


def test_detect_pasted_sample_content_rejects_plain_text():
    assert not orchestrator._looks_like_pasted_sample_content("create a search app")


def test_augment_worker_context_with_source_and_sample_from_builtin_file():
    state = orchestrator.SessionState(
        source_local_file="opensearch_orchestrator/scripts/sample_data/imdb.title.basics.tsv",
        sample_doc_json=json.dumps({"sample_doc": {"primaryTitle": "Carmencita"}}, ensure_ascii=False),
    )
    context = "**SOLUTION**:\n- Retrieval Method: Hybrid Search (BM25 + Dense Vector)"

    augmented = orchestrator._augment_worker_context_with_source(state, context)

    assert orchestrator._SYSTEM_SOURCE_CONTEXT_HEADER in augmented
    assert "Source: opensearch_orchestrator/scripts/sample_data/imdb.title.basics.tsv" in augmented
    assert 'Sample document: {"primaryTitle": "Carmencita"}' in augmented


def test_augment_worker_context_with_source_is_idempotent():
    state = orchestrator.SessionState(
        source_local_file="opensearch_orchestrator/scripts/sample_data/imdb.title.basics.tsv",
        sample_doc_json=json.dumps({"sample_doc": {"primaryTitle": "Carmencita"}}, ensure_ascii=False),
    )
    context = "**SOLUTION**:\n- Retrieval Method: Hybrid Search (BM25 + Dense Vector)"

    once = orchestrator._augment_worker_context_with_source(state, context)
    twice = orchestrator._augment_worker_context_with_source(state, once)

    assert twice == once
    assert twice.count(orchestrator._SYSTEM_SOURCE_CONTEXT_HEADER) == 1


def test_augment_worker_context_with_source_uses_localhost_index_when_file_missing():
    state = orchestrator.SessionState(
        source_index_name="yellow-tripdata",
        sample_doc_json=json.dumps({"sample_doc": {"vendor_id": "1"}}, ensure_ascii=False),
    )
    context = "**SOLUTION**:\n- Retrieval Method: BM25"

    augmented = orchestrator._augment_worker_context_with_source(state, context)

    assert "Source: localhost OpenSearch index 'yellow-tripdata'" in augmented
    assert 'Sample document: {"vendor_id": "1"}' in augmented


def test_augment_worker_context_with_source_preserves_resume_marker():
    state = orchestrator.SessionState(
        source_local_file="opensearch_orchestrator/scripts/sample_data/imdb.title.basics.tsv",
        sample_doc_json=json.dumps({"sample_doc": {"primaryTitle": "Carmencita"}}, ensure_ascii=False),
    )
    context = (
        f"{orchestrator._RESUME_WORKER_MARKER}\n"
        "**SOLUTION**:\n- Retrieval Method: Hybrid Search (BM25 + Dense Vector)"
    )

    augmented = orchestrator._augment_worker_context_with_source(state, context)

    assert augmented.startswith(orchestrator._RESUME_WORKER_MARKER)
    assert orchestrator._SYSTEM_SOURCE_CONTEXT_HEADER in augmented


def test_extract_localhost_auth_override_custom_credentials():
    mode, username, password, error = orchestrator._extract_localhost_auth_override_from_text(
        "use option 3 with username=alice password='s3cr3t'"
    )

    assert error is None
    assert mode == orchestrator._LOCALHOST_AUTH_MODE_CUSTOM
    assert username == "alice"
    assert password == "s3cr3t"


def test_extract_localhost_auth_override_none_from_phrase():
    mode, username, password, error = orchestrator._extract_localhost_auth_override_from_text(
        "Use localhost index, I don't have username and password."
    )

    assert error is None
    assert mode == orchestrator._LOCALHOST_AUTH_MODE_NONE
    assert username == ""
    assert password == ""


def test_extract_localhost_auth_override_rejects_incomplete_custom_credentials():
    mode, username, password, error = orchestrator._extract_localhost_auth_override_from_text(
        "use localhost index username=alice"
    )

    assert mode is None
    assert username == ""
    assert password == ""
    assert error is not None
    assert "requires both username and password" in error.lower()


def test_redact_localhost_auth_secrets_masks_password_tokens():
    text = "option 3 username=alice password=mySecret123!"

    redacted = orchestrator._redact_localhost_auth_secrets(text)

    assert "mySecret123!" not in redacted
    assert "password=***" in redacted
