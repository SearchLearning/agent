from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import opensearch_orchestrator.opensearch_ops_tools as tools


class _FakeIndices:
    def __init__(self, mapping_response=None, create_error=None):
        self._mapping_response = mapping_response or {}
        self._create_error = create_error
        self.created = []
        self.refreshed = []

    def create(self, index, body):
        if self._create_error is not None:
            raise Exception(self._create_error)
        self.created.append((index, body))

    def get_mapping(self, index):
        return self._mapping_response

    def refresh(self, index):
        self.refreshed.append(index)


class _FakeClient:
    def __init__(self, mapping_response=None, create_error=None):
        self.indices = _FakeIndices(mapping_response, create_error=create_error)
        self.indexed = []
        self.deleted = []

    def index(self, index, body, id):
        self.indexed.append((index, id, body))

    def bulk(self, body):
        items = []
        i = 0
        while i < len(body):
            action = body[i]
            doc = body[i + 1]
            meta = action.get("index", {})
            self.indexed.append((meta.get("_index"), meta.get("_id"), doc))
            items.append({"index": {"_id": meta.get("_id"), "result": "created", "status": 201}})
            i += 2
        return {"errors": False, "items": items}

    def delete(self, index, id, ignore=None):
        self.deleted.append((index, id, tuple(ignore or [])))



def _create_body(field_type):
    return {
        "mappings": {
            "properties": {
                "isAdult": {"type": field_type},
                "title": {
                    "type": "text",
                    "fields": {
                        "keyword": {"type": "keyword"},
                    },
                },
            }
        }
    }



def _mapping_response(field_type):
    return {
        "imdb_titles": {
            "mappings": {
                "properties": {
                    "isAdult": {"type": field_type},
                    "title": {
                        "type": "text",
                        "fields": {"keyword": {"type": "keyword"}},
                    },
                }
            }
        }
    }



def _worker_output():
    return "## Search Capabilities\n- Exact: title search\n"



def test_create_index_fails_for_boolean_mapping_with_string_binary_flags(monkeypatch):
    monkeypatch.setattr(
        tools,
        "get_sample_docs_payload",
        lambda limit=200, sample_doc_json="", source_local_file="": [{"isAdult": "0", "title": "Carmencita"}],
    )

    response = tools.create_index("imdb_titles", _create_body("boolean"))

    lowered = response.lower()
    assert "preflight failed" in lowered
    assert "map this field as keyword" in lowered
    assert "isadult" in lowered



def test_create_index_succeeds_for_keyword_mapping_with_string_binary_flags(monkeypatch):
    fake_client = _FakeClient()
    monkeypatch.setattr(
        tools,
        "get_sample_docs_payload",
        lambda limit=200, sample_doc_json="", source_local_file="": [{"isAdult": "1", "title": "Carmencita"}],
    )
    monkeypatch.setattr(tools, "_create_client", lambda: fake_client)

    response = tools.create_index("imdb_titles", _create_body("keyword"), replace_if_exists=False)

    assert "created successfully" in response.lower()
    assert fake_client.indices.created



def test_create_index_succeeds_for_boolean_mapping_with_native_booleans(monkeypatch):
    fake_client = _FakeClient()
    monkeypatch.setattr(
        tools,
        "get_sample_docs_payload",
        lambda limit=200, sample_doc_json="", source_local_file="": [{"isAdult": True, "title": "Carmencita"}],
    )
    monkeypatch.setattr(tools, "_create_client", lambda: fake_client)

    response = tools.create_index("imdb_titles", _create_body("boolean"))

    assert "created successfully" in response.lower()
    assert fake_client.indices.created



def test_create_index_fails_for_mixed_boolean_and_string_binary_flag_samples(monkeypatch):
    monkeypatch.setattr(
        tools,
        "get_sample_docs_payload",
        lambda limit=200, sample_doc_json="", source_local_file="": [
            {"isAdult": True, "title": "A"},
            {"isAdult": "0", "title": "B"},
        ],
    )

    response = tools.create_index("imdb_titles", _create_body("boolean"))

    lowered = response.lower()
    assert "preflight failed" in lowered
    assert "isadult" in lowered
    assert "string_binary_flag" in lowered



def test_create_index_existing_index_violation_is_error(monkeypatch):
    fake_client = _FakeClient(
        mapping_response=_mapping_response("boolean"),
        create_error="resource_already_exists_exception",
    )
    monkeypatch.setattr(
        tools,
        "get_sample_docs_payload",
        lambda limit=200, sample_doc_json="", source_local_file="": [{"isAdult": "0", "title": "Carmencita"}],
    )
    monkeypatch.setattr(tools, "_create_client", lambda: fake_client)

    response = tools.create_index("imdb_titles", _create_body("keyword"), replace_if_exists=False)

    lowered = response.lower()
    assert "already exists" in lowered
    assert "violates producer-driven boolean typing policy" in lowered


def test_create_index_normalizes_hnsw_engine_when_missing(monkeypatch):
    fake_client = _FakeClient()
    monkeypatch.setattr(tools, "_create_client", lambda: fake_client)
    monkeypatch.setattr(tools, "get_sample_docs_payload", lambda limit=200, sample_doc_json="", source_local_file="": [])

    body = {
        "mappings": {
            "properties": {
                "embedding": {
                    "type": "knn_vector",
                    "dimension": 384,
                    "method": {
                        "name": "hnsw",
                        "space_type": "cosinesimil",
                        "parameters": {"ef_construction": 100, "m": 16},
                    },
                }
            }
        }
    }

    response = tools.create_index("imdb_titles", body, replace_if_exists=False)

    assert "created successfully" in response.lower()
    assert fake_client.indices.created
    created_body = fake_client.indices.created[-1][1]
    method = created_body["mappings"]["properties"]["embedding"]["method"]
    assert method["engine"] == "lucene"


def test_create_index_rewrites_nmslib_engine_to_lucene_for_hnsw(monkeypatch):
    fake_client = _FakeClient()
    monkeypatch.setattr(tools, "_create_client", lambda: fake_client)
    monkeypatch.setattr(tools, "get_sample_docs_payload", lambda limit=200, sample_doc_json="", source_local_file="": [])

    body = {
        "mappings": {
            "properties": {
                "embedding": {
                    "type": "knn_vector",
                    "dimension": 384,
                    "method": {
                        "name": "hnsw",
                        "engine": "nmslib",
                        "space_type": "cosinesimil",
                        "parameters": {"ef_construction": 100, "m": 16},
                    },
                }
            }
        }
    }

    response = tools.create_index("imdb_titles", body, replace_if_exists=False)

    assert "created successfully" in response.lower()
    assert fake_client.indices.created
    created_body = fake_client.indices.created[-1][1]
    method = created_body["mappings"]["properties"]["embedding"]["method"]
    assert method["engine"] == "lucene"



def test_apply_verification_blocks_on_boolean_policy_conflict(monkeypatch):
    tools._search_ui.suggestion_meta_by_index.clear()

    fake_client = _FakeClient(mapping_response=_mapping_response("boolean"))

    monkeypatch.setattr(
        tools,
        "get_sample_docs_payload",
        lambda limit=200, sample_doc_json="", source_local_file="": [{"isAdult": "0", "title": "Carmencita"}],
    )
    monkeypatch.setattr(tools, "_create_client", lambda: fake_client)

    result = tools.apply_capability_driven_verification(
        worker_output=_worker_output(),
        index_name="imdb_titles",
    )

    assert result["applied"] is False
    assert result["indexed_count"] == 0
    notes = " ".join(result.get("notes", [])).lower()
    assert "preflight failed" in notes
    assert "map this field as keyword" in notes



def test_apply_verification_proceeds_with_keyword_mapping_for_string_binary_flags(monkeypatch):
    tools._search_ui.suggestion_meta_by_index.clear()

    fake_client = _FakeClient(mapping_response=_mapping_response("keyword"))

    monkeypatch.setattr(
        tools,
        "get_sample_docs_payload",
        lambda limit=200, sample_doc_json="", source_local_file="": [{"isAdult": "1", "title": "Carmencita"}],
    )
    monkeypatch.setattr(tools, "_create_client", lambda: fake_client)

    result = tools.apply_capability_driven_verification(
        worker_output=_worker_output(),
        index_name="imdb_titles",
        count=1,
    )

    assert result["applied"] is True
    assert result["indexed_count"] == 1
    assert fake_client.indexed
