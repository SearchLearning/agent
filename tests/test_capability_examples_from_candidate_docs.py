from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import opensearch_orchestrator.scripts.opensearch_ops_tools as tools


class _FakeIndices:
    def __init__(self, mapping_response):
        self._mapping_response = mapping_response
        self.refreshed = []

    def get_mapping(self, index):
        return self._mapping_response

    def refresh(self, index):
        self.refreshed.append(index)


class _FakeClient:
    def __init__(self, mapping_response):
        self.indices = _FakeIndices(mapping_response)
        self.indexed = []
        self.deleted = []

    def index(self, index, body, id):
        self.indexed.append((index, id, body))

    def delete(self, index, id, ignore=None):
        self.deleted.append((index, id, tuple(ignore or [])))


def _mapping_response():
    return {
        "imdb_titles": {
            "mappings": {
                "properties": {
                    "primaryTitle": {
                        "type": "text",
                        "fields": {"keyword": {"type": "keyword"}},
                    },
                    "genre": {"type": "keyword"},
                    "startYear": {"type": "integer"},
                }
            }
        }
    }


def test_extract_search_capabilities_does_not_store_quoted_examples():
    worker_output = (
        "## Search Capabilities\n"
        '- Semantic: "romantic comedies in paris"\n'
    )
    capabilities = tools._extract_search_capabilities(worker_output)

    assert capabilities
    assert capabilities[0]["id"] == "semantic"
    assert capabilities[0]["examples"] == []


def test_infer_capability_examples_from_features_semantic(monkeypatch):
    features = {
        "exact_candidates": [],
        "semantic_candidates": [
            {"text": "short text", "field": "primaryTitle"},
            {"text": "space exploration mission documentary", "field": "primaryTitle"},
        ],
        "structured_candidates": [],
        "anchor_tokens": [],
    }

    examples = tools._infer_capability_examples_from_features("semantic", features)

    assert examples == ["space exploration mission documentary"]


def test_rewrite_semantic_example_short_text_keeps_original():

    rewritten = tools._rewrite_semantic_example("Blacksmith Scene")

    assert rewritten == "Blacksmith Scene"


def test_rewrite_semantic_example_keeps_original_when_low_confidence():

    rewritten = tools._rewrite_semantic_example("Blacksmith Scene")

    assert rewritten == "Blacksmith Scene"


def test_rewrite_semantic_example_descriptive_phrase():

    rewritten = tools._rewrite_semantic_example("space exploration mission documentary")

    assert rewritten == "space exploration mission documentary"


def test_rewrite_semantic_example_non_english_fallback_safe():
    source = "宇宙探査についての短編映画"
    rewritten = tools._rewrite_semantic_example(source)

    assert rewritten == source


def test_rewrite_semantic_example_prefers_llm_when_enabled(monkeypatch):
    monkeypatch.setenv(tools.SEMANTIC_QUERY_REWRITE_FLAG, "true")
    monkeypatch.setattr(tools, "_rewrite_semantic_example_with_llm", lambda _text: "academy award winners history")

    rewritten = tools._rewrite_semantic_example("https wikipedia org wiki academy 20award")

    assert rewritten == "academy award winners history"


def test_semantic_query_rewrite_llm_disabled_in_mcp_runtime(monkeypatch):
    monkeypatch.setenv(tools.RUNTIME_MODE_ENV, tools.RUNTIME_MODE_MCP)
    monkeypatch.delenv(tools.SEMANTIC_QUERY_REWRITE_FLAG, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "dummy")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "dummy")

    assert tools._semantic_query_rewrite_llm_enabled() is False


def test_rewrite_semantic_example_url_noise_fallback_without_llm(monkeypatch):
    monkeypatch.delenv(tools.SEMANTIC_QUERY_REWRITE_FLAG, raising=False)

    rewritten = tools._rewrite_semantic_example("https wikipedia org wiki academy 20award")

    assert rewritten == "academy award"


def test_rewrite_semantic_example_disambiguation_becomes_human_readable(monkeypatch):
    monkeypatch.delenv(tools.SEMANTIC_QUERY_REWRITE_FLAG, raising=False)

    rewritten = tools._rewrite_semantic_example("Ada may refer to: Places Africa Ada Foah")

    assert rewritten == "Ada disambiguation"


def test_infer_capability_examples_from_features_combined():
    features = {
        "exact_candidates": [],
        "semantic_candidates": [],
        "structured_candidates": [
            {"field": "genre", "value": "Documentary", "type": "keyword"},
            {"field": "startYear", "value": "1969", "type": "integer"},
        ],
        "anchor_tokens": [],
    }

    examples = tools._infer_capability_examples_from_features("combined", features)

    assert examples == ["genre: Documentary and startYear: 1969"]


def test_build_suggestion_entry_combined_does_not_require_semantic():
    capability = {"id": "combined", "examples": []}
    features = {
        "exact_candidates": [],
        "phrase_candidates": [],
        "semantic_candidates": [],
        "structured_candidates": [
            {"field": "genre", "value": "Documentary", "type": "keyword"},
            {"field": "startYear", "value": "1969", "type": "integer"},
        ],
        "anchor_tokens": [],
    }

    entry = tools._build_suggestion_entry(capability, features)

    assert entry is not None
    assert entry["capability"] == "combined"
    assert entry["text"] == "genre: Documentary and startYear: 1969"
    assert entry["field"] == "genre"
    assert entry["value"] == "Documentary"


def test_infer_capability_examples_from_features_combined_uses_text_plus_structured_when_needed():
    features = {
        "exact_candidates": [],
        "phrase_candidates": [
            {"text": "Ada", "field": "title", "query_mode": "match_phrase"},
        ],
        "semantic_candidates": [],
        "structured_candidates": [
            {"field": "id", "value": "630", "type": "keyword"},
        ],
        "anchor_tokens": [],
    }

    examples = tools._infer_capability_examples_from_features("combined", features)

    assert examples == ['title: Ada and id: 630']


def test_build_suggestion_entry_combined_requires_multiple_conditions():
    capability = {"id": "combined", "examples": []}
    features = {
        "exact_candidates": [],
        "phrase_candidates": [],
        "semantic_candidates": [],
        "structured_candidates": [
            {"field": "id", "value": "630", "type": "keyword"},
        ],
        "anchor_tokens": [],
    }

    entry = tools._build_suggestion_entry(capability, features)

    assert entry is None


def test_build_suggestion_entry_combined_uses_text_plus_structured_when_needed():
    capability = {"id": "combined", "examples": []}
    features = {
        "exact_candidates": [],
        "phrase_candidates": [
            {"text": "Ada", "field": "title", "query_mode": "match_phrase"},
        ],
        "semantic_candidates": [],
        "structured_candidates": [
            {"field": "id", "value": "630", "type": "keyword"},
        ],
        "anchor_tokens": [],
    }

    entry = tools._build_suggestion_entry(capability, features)

    assert entry is not None
    assert entry["capability"] == "combined"
    assert entry["text"] == 'title: Ada and id: 630'
    assert entry["field"] == "id"
    assert entry["value"] == "630"


def test_score_doc_for_capability_combined_uses_structured_candidates():
    features = {
        "exact_candidates": [],
        "semantic_candidates": [],
        "structured_candidates": [{"field": "genre", "value": "Documentary", "type": "keyword"}],
        "anchor_tokens": [],
    }

    score = tools._score_doc_for_capability(features, "combined")

    assert score > 0.0


def test_apply_verification_infers_semantic_example_from_candidate_docs(monkeypatch):
    tools._search_ui.suggestion_meta_by_index.clear()

    fake_client = _FakeClient(mapping_response=_mapping_response())
    monkeypatch.setattr(tools, "_create_client", lambda: fake_client)
    monkeypatch.setattr(
        tools,
        "get_sample_docs_payload",
        lambda limit=200, sample_doc_json="", source_local_file="": [
            {
                "primaryTitle": "space exploration mission documentary",
                "genre": "Documentary",
                "startYear": 1969,
            }
        ],
    )

    worker_output = (
        "## Search Capabilities\n"
        '- Semantic: "romantic comedies in paris"\n'
    )

    result = tools.apply_capability_driven_verification(
        worker_output=worker_output,
        index_name="imdb_titles",
        count=1,
    )

    assert result["applied"] is True
    suggestion_meta = result.get("suggestion_meta", [])
    semantic_entries = [entry for entry in suggestion_meta if entry.get("capability") == "semantic"]
    assert semantic_entries
    assert semantic_entries[0]["text"] == "space exploration mission documentary"
    assert "romantic comedies in paris" not in semantic_entries[0]["text"].lower()


def test_extract_doc_features_anchor_tokens_only_from_text_and_keyword_fields():
    source = {
        "tpep_pickup_datetime": "2024-12-01T00:12:27",
        "trip_distance": 9.76,
        "store_and_fwd_flag": "active_route",
    }
    field_specs = {
        "tpep_pickup_datetime": {"type": "date", "normalizer": ""},
        "trip_distance": {"type": "float", "normalizer": ""},
        "store_and_fwd_flag": {"type": "keyword", "normalizer": ""},
    }

    features = tools._extract_doc_features(source, field_specs)

    assert features["anchor_tokens"] == [{"token": "active_route", "field": "store_and_fwd_flag"}]


def test_build_suggestion_entry_skips_autocomplete_and_fuzzy_for_date_numeric_only_docs():
    source = {
        "tpep_pickup_datetime": "2024-12-01T00:12:27",
        "tpep_dropoff_datetime": "2024-12-01T00:31:12",
        "trip_distance": 9.76,
    }
    field_specs = {
        "tpep_pickup_datetime": {"type": "date", "normalizer": ""},
        "tpep_dropoff_datetime": {"type": "date", "normalizer": ""},
        "trip_distance": {"type": "float", "normalizer": ""},
    }

    features = tools._extract_doc_features(source, field_specs)

    assert features["anchor_tokens"] == []
    assert tools._build_suggestion_entry({"id": "autocomplete", "examples": []}, features) is None
    assert tools._build_suggestion_entry({"id": "fuzzy", "examples": []}, features) is None


def test_extract_doc_features_numeric_date_schema_has_no_semantic_candidates():
    source = {
        "VendorID": 2,
        "tpep_pickup_datetime": "2024-12-01T00:12:27",
        "trip_distance": 9.76,
        "PULocationID": 138,
    }
    field_specs = {
        "VendorID": {"type": "integer", "normalizer": ""},
        "tpep_pickup_datetime": {"type": "date", "normalizer": ""},
        "trip_distance": {"type": "float", "normalizer": ""},
        "PULocationID": {"type": "integer", "normalizer": ""},
    }

    features = tools._extract_doc_features(source, field_specs)

    assert features["semantic_candidates"] == []


def test_apply_verification_skips_unsupported_capabilities_for_numeric_date_schema(monkeypatch):
    tools._search_ui.suggestion_meta_by_index.clear()

    mapping_response = {
        "nyc_taxi": {
            "mappings": {
                "properties": {
                    "VendorID": {"type": "integer"},
                    "tpep_pickup_datetime": {"type": "date"},
                    "trip_distance": {"type": "float"},
                    "store_and_fwd_flag": {"type": "keyword"},
                    "PULocationID": {"type": "integer"},
                }
            }
        }
    }
    fake_client = _FakeClient(mapping_response=mapping_response)
    monkeypatch.setattr(tools, "_create_client", lambda: fake_client)
    monkeypatch.setattr(
        tools,
        "get_sample_docs_payload",
        lambda limit=200, sample_doc_json="", source_local_file="": [
            {
                "VendorID": 2,
                "tpep_pickup_datetime": "2024-12-01T00:12:27",
                "trip_distance": 9.76,
                "store_and_fwd_flag": "N",
                "PULocationID": 138,
            }
        ],
    )

    worker_output = (
        "## Search Capabilities\n"
        "- Exact: exact record lookup\n"
        "- Structured: numeric/date filters\n"
        "- Autocomplete: typeahead prefixes\n"
    )
    result = tools.apply_capability_driven_verification(
        worker_output=worker_output,
        index_name="nyc_taxi",
        count=1,
    )

    assert result["applied"] is True
    assert result["indexed_count"] == 1
    assert result.get("applicable_capabilities") == ["structured"]

    skipped = result.get("skipped_capabilities", [])
    assert [item.get("id") for item in skipped] == ["exact", "autocomplete"]

    notes = " ".join(result.get("notes", [])).lower()
    assert "no compatible sample document found" not in notes

    suggestion_meta = result.get("suggestion_meta", [])
    assert [entry.get("capability") for entry in suggestion_meta] == ["structured"]


def test_apply_verification_skips_semantic_for_numeric_date_schema(monkeypatch):
    tools._search_ui.suggestion_meta_by_index.clear()

    mapping_response = {
        "nyc_taxi": {
            "mappings": {
                "properties": {
                    "VendorID": {"type": "integer"},
                    "tpep_pickup_datetime": {"type": "date"},
                    "trip_distance": {"type": "float"},
                    "PULocationID": {"type": "integer"},
                }
            }
        }
    }
    fake_client = _FakeClient(mapping_response=mapping_response)
    monkeypatch.setattr(tools, "_create_client", lambda: fake_client)
    monkeypatch.setattr(
        tools,
        "get_sample_docs_payload",
        lambda limit=200, sample_doc_json="", source_local_file="": [
            {
                "VendorID": 2,
                "tpep_pickup_datetime": "2024-12-01T00:12:27",
                "trip_distance": 9.76,
                "PULocationID": 138,
            }
        ],
    )

    worker_output = (
        "## Search Capabilities\n"
        "- Semantic: concept search\n"
        "- Structured: numeric/date filters\n"
    )
    result = tools.apply_capability_driven_verification(
        worker_output=worker_output,
        index_name="nyc_taxi",
        count=1,
    )

    assert result["applied"] is True
    assert result.get("applicable_capabilities") == ["structured"]
    skipped = result.get("skipped_capabilities", [])
    assert [item.get("id") for item in skipped] == ["semantic"]
    suggestion_meta = result.get("suggestion_meta", [])
    assert [entry.get("capability") for entry in suggestion_meta] == ["structured"]


def test_preview_verification_infers_text_like_fields_without_index_mapping(monkeypatch):
    monkeypatch.setattr(
        tools,
        "get_sample_docs_payload",
        lambda limit=200, sample_doc_json="", source_local_file="", source_index_name="": [
            {
                "title": "Toyota Camry hybrid sedan",
                "description": "Reliable family sedan with roomy interior and smooth ride",
                "category": "Family Cars",
                "year": "2024",
            }
        ],
    )
    monkeypatch.setattr(tools, "_create_client", lambda: (_ for _ in ()).throw(RuntimeError("should not connect")))

    worker_output = (
        "## Search Capabilities\n"
        "- Exact: exact title lookup\n"
        "- Semantic: concept search\n"
        "- Structured: numeric/date filters\n"
        "- Combined: semantic + filters\n"
        "- Autocomplete: prefix lookup\n"
        "- Fuzzy: typo tolerance\n"
    )
    preview = tools.preview_cap_driven_verification(worker_output=worker_output, count=1)

    applicable = set(preview.get("applicable_capabilities", []))
    assert {"exact", "autocomplete", "fuzzy"}.issubset(applicable)
    notes_text = " ".join(preview.get("notes", [])).lower()
    assert "inferred field specs" in notes_text


def test_preview_and_apply_keep_capability_applicability_consistent(monkeypatch):
    tools._search_ui.suggestion_meta_by_index.clear()

    # Use empty mapping so both preview/apply paths rely on inferred field specs.
    mapping_response = {}
    fake_client = _FakeClient(mapping_response=mapping_response)
    monkeypatch.setattr(tools, "_create_client", lambda: fake_client)
    monkeypatch.setattr(
        tools,
        "get_sample_docs_payload",
        lambda limit=200, sample_doc_json="", source_local_file="", source_index_name="": [
            {
                "VendorID": 2,
                "tpep_pickup_datetime": "2024-12-01T00:12:27",
                "trip_distance": 9.76,
                "store_and_fwd_flag": "N",
                "PULocationID": 138,
            }
        ],
    )

    worker_output = (
        "## Search Capabilities\n"
        "- Exact: exact record lookup\n"
        "- Structured: numeric/date filters\n"
        "- Autocomplete: typeahead prefixes\n"
    )
    preview = tools.preview_cap_driven_verification(
        worker_output=worker_output,
        count=1,
    )
    applied = tools.apply_capability_driven_verification(
        worker_output=worker_output,
        index_name="nyc_taxi",
        count=1,
    )

    assert preview.get("applicable_capabilities") == applied.get("applicable_capabilities")
    assert [item.get("id") for item in preview.get("skipped_capabilities", [])] == [
        item.get("id") for item in applied.get("skipped_capabilities", [])
    ]
