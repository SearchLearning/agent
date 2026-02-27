import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import opensearch_orchestrator.scripts.tools as tools
from opensearch_orchestrator.scripts.tools import (
    _extract_path_candidate,
    _extract_index_candidate,
    get_sample_docs_payload,
    submit_sample_doc_from_local_file,
    submit_sample_doc_from_localhost_index,
)
from opensearch_orchestrator.scripts.shared import (
    looks_like_builtin_imdb_sample_request,
    looks_like_local_path_message,
    looks_like_localhost_index_message,
)


class _FakeLocalhostIndices:
    def exists(self, index):
        return index == "yellow-tripdata"


class _FakeLocalhostCat:
    def __init__(self, indices_response=None):
        self._indices_response = indices_response if indices_response is not None else []
        self.indices_calls = []

    def indices(self, format="json"):
        self.indices_calls.append(format)
        return self._indices_response


class _FakeLocalhostClient:
    def __init__(
        self,
        count_response=None,
        search_response=None,
        count_error=None,
        cat_indices_response=None,
    ):
        self.indices = _FakeLocalhostIndices()
        self.cat = _FakeLocalhostCat(cat_indices_response)
        self._count_response = count_response if count_response is not None else {"count": 0}
        self._search_response = search_response if search_response is not None else {}
        self._count_error = count_error
        self.count_calls = []
        self.search_calls = []

    def count(self, index, body):
        self.count_calls.append((index, body))
        if self._count_error is not None:
            raise self._count_error
        return self._count_response

    def search(self, index, body):
        self.search_calls.append((index, body))
        return self._search_response


def test_extract_path_candidate_strips_sentence_trailing_period(tmp_path, monkeypatch):
    home = tmp_path / "home"
    downloads = home / "Downloads"
    downloads.mkdir(parents=True)
    data_file = downloads / "title.basics.tsv"
    data_file.write_text("id\ttitle\n1\tTest\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    message = "Data source is ~/Downloads/title.basics.tsv. Please index it."
    detected = _extract_path_candidate(message)

    assert detected == str(data_file)


def test_extract_path_candidate_supports_quoted_absolute_path_with_spaces(tmp_path):
    data_file = tmp_path / "title basics.tsv"
    data_file.write_text("id\ttitle\n1\tTest\n", encoding="utf-8")

    message = f'Use "{data_file}" for indexing.'
    detected = _extract_path_candidate(message)

    assert detected == str(data_file)


def test_submit_sample_doc_from_local_file_accepts_sentence_style_path(tmp_path, monkeypatch):
    home = tmp_path / "home"
    downloads = home / "Downloads"
    downloads.mkdir(parents=True)
    data_file = downloads / "title.basics.tsv"
    data_file.write_text(
        "tconst\tprimaryTitle\tisAdult\n"
        "tt0000001\tCarmencita\t0\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))

    message = (
        "Help setup OpenSearch. Data for search is ~/Downloads/title.basics.tsv. "
        "Please proceed."
    )
    result = submit_sample_doc_from_local_file(message)

    parsed = json.loads(result)
    assert "sample_doc" in parsed
    assert parsed["status"].startswith("Sample document loaded from")
    stored = parsed["sample_doc"]
    assert stored["tconst"] == "tt0000001"
    assert stored["primaryTitle"] == "Carmencita"
    assert stored["isAdult"] == "0"


def test_extract_path_candidate_returns_sanitized_missing_path(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    message = "Input file: ~/Downloads/missing.tsv."
    detected = _extract_path_candidate(message)

    assert detected.endswith("missing.tsv")


def test_extract_index_candidate_from_free_form_text():
    detected = _extract_index_candidate("Use localhost index movies_catalog for this project.")
    assert detected == "movies_catalog"


def test_extract_index_candidate_from_localhost_url():
    detected = _extract_index_candidate("http://localhost:9200/movies-index/_search")
    assert detected == "movies-index"


def test_extract_index_candidate_strips_sentence_trailing_period():
    detected = _extract_index_candidate(
        "Data for search is index yellow-tripdata. Please build search."
    )
    assert detected == "yellow-tripdata"


def test_extract_index_candidate_from_index_name_phrase():
    detected = _extract_index_candidate("3. index name yellow-tripdata")
    assert detected == "yellow-tripdata"


def test_create_local_opensearch_client_default_mode_uses_admin_credentials(monkeypatch):
    calls: list[dict[str, object]] = []

    class _FakeOpenSearch:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def info(self):
            return {"version": {"number": "2.0.0"}}

    import opensearchpy

    monkeypatch.delenv("OPENSEARCH_AUTH_MODE", raising=False)
    monkeypatch.delenv("OPENSEARCH_USER", raising=False)
    monkeypatch.delenv("OPENSEARCH_PASSWORD", raising=False)
    monkeypatch.setattr(opensearchpy, "OpenSearch", _FakeOpenSearch)

    client, error = tools._create_local_opensearch_client()

    assert error is None
    assert client is not None
    assert calls
    assert calls[0].get("http_auth") == ("admin", "myStrongPassword123!")


def test_create_local_opensearch_client_none_mode_uses_no_auth(monkeypatch):
    calls: list[dict[str, object]] = []

    class _FakeOpenSearch:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def info(self):
            return {"version": {"number": "2.0.0"}}

    import opensearchpy

    monkeypatch.setenv("OPENSEARCH_AUTH_MODE", "none")
    monkeypatch.delenv("OPENSEARCH_USER", raising=False)
    monkeypatch.delenv("OPENSEARCH_PASSWORD", raising=False)
    monkeypatch.setattr(opensearchpy, "OpenSearch", _FakeOpenSearch)

    client, error = tools._create_local_opensearch_client()

    assert error is None
    assert client is not None
    assert calls
    assert "http_auth" not in calls[0]


def test_create_local_opensearch_client_custom_mode_uses_supplied_credentials(monkeypatch):
    calls: list[dict[str, object]] = []

    class _FakeOpenSearch:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def info(self):
            return {"version": {"number": "2.0.0"}}

    import opensearchpy

    monkeypatch.setenv("OPENSEARCH_AUTH_MODE", "custom")
    monkeypatch.setenv("OPENSEARCH_USER", "customer-user")
    monkeypatch.setenv("OPENSEARCH_PASSWORD", "customer-password")
    monkeypatch.setattr(opensearchpy, "OpenSearch", _FakeOpenSearch)

    client, error = tools._create_local_opensearch_client()

    assert error is None
    assert client is not None
    assert calls
    assert calls[0].get("http_auth") == ("customer-user", "customer-password")


def test_create_local_opensearch_client_custom_mode_requires_credentials(monkeypatch):
    monkeypatch.setenv("OPENSEARCH_AUTH_MODE", "custom")
    monkeypatch.delenv("OPENSEARCH_USER", raising=False)
    monkeypatch.delenv("OPENSEARCH_PASSWORD", raising=False)

    client, error = tools._create_local_opensearch_client()

    assert client is None
    assert isinstance(error, str)
    assert "requires OPENSEARCH_USER and OPENSEARCH_PASSWORD" in error


def test_detect_imdb_and_localhost_index_intents():
    assert looks_like_builtin_imdb_sample_request("Use the sample IMDb dataset.")
    assert looks_like_localhost_index_message("Data is already in localhost index movies-index.")


def test_builtin_imdb_detector_ignores_pasted_json_sample_content():
    message = (
        '{"content": "The quick brown fox jumps over the lazy dog. '
        'This is a sample document for testing search capabilities."}'
    )
    assert not looks_like_builtin_imdb_sample_request(message)


def test_imdb_index_reference_prefers_index_intent_not_builtin_sample():
    message = "Data for search is in index imdb_titles."
    assert looks_like_localhost_index_message(message)
    assert not looks_like_builtin_imdb_sample_request(message)


def test_plain_index_reference_is_detected_for_localhost_index_intent():
    message = (
        "Help me create a search application. Data for search is index yellow-tripdata. "
        "I want to run queries on this dataset using opensearch."
    )
    assert looks_like_localhost_index_message(message)
    assert not looks_like_builtin_imdb_sample_request(message)


def test_submit_sample_doc_from_localhost_index_surfaces_exact_count(monkeypatch):
    fake_client = _FakeLocalhostClient(
        count_response={"count": 12345},
        search_response={
            "hits": {
                "total": {"value": 12345, "relation": "eq"},
                "hits": [{"_source": {"VendorID": "1", "fare_amount": "14.5"}}],
            }
        },
    )
    monkeypatch.setattr(tools, "_create_local_opensearch_client", lambda: (fake_client, None))

    result = submit_sample_doc_from_localhost_index("Data for search is index yellow-tripdata.")

    parsed = json.loads(result)
    assert parsed["source_localhost_index"] is True
    assert parsed["source_index_name"] == "yellow-tripdata"
    assert parsed["source_index_doc_count"] == 12345
    assert "exact documents: 12,345 (via count API)" in parsed["status"]
    assert fake_client.count_calls == [("yellow-tripdata", {"query": {"match_all": {}}})]
    assert fake_client.search_calls
    assert fake_client.search_calls[0][1]["track_total_hits"] is True


def test_submit_sample_doc_from_localhost_index_falls_back_when_count_unavailable(monkeypatch):
    fake_client = _FakeLocalhostClient(
        count_error=RuntimeError("count unavailable"),
        search_response={
            "hits": {
                "total": {"value": 88, "relation": "eq"},
                "hits": [{"_source": {"VendorID": "2", "trip_distance": "1.2"}}],
            }
        },
    )
    monkeypatch.setattr(tools, "_create_local_opensearch_client", lambda: (fake_client, None))

    result = submit_sample_doc_from_localhost_index("index yellow-tripdata")

    parsed = json.loads(result)
    assert parsed["source_localhost_index"] is True
    assert parsed["source_index_name"] == "yellow-tripdata"
    assert "source_index_doc_count" not in parsed
    assert "inferred documents from search response: 88" in parsed["status"]


def test_submit_sample_doc_from_localhost_index_requires_selection_when_name_missing(monkeypatch):
    fake_client = _FakeLocalhostClient(
        cat_indices_response=[
            {"index": ".plugins-ml-model", "docs.count": "135"},
            {"index": "yellow-tripdata", "docs.count": "10000"},
            {"index": "wikipedia", "docs.count": "10"},
        ]
    )
    monkeypatch.setattr(tools, "_create_local_opensearch_client", lambda: (fake_client, None))

    result = submit_sample_doc_from_localhost_index("")

    assert result.startswith("Error: option 3 selected but no index name was provided.")
    assert "Available non-system indices on localhost OpenSearch:" in result
    assert "- yellow-tripdata (docs=10,000)" in result
    assert "- wikipedia (docs=10)" in result
    assert "Please choose one index name from this list and retry option 3." in result
    assert fake_client.cat.indices_calls == ["json"]


def test_submit_sample_doc_from_localhost_index_not_found_lists_available_indices(monkeypatch):
    fake_client = _FakeLocalhostClient(
        cat_indices_response=[
            {"index": ".plugins-ml-model", "docs.count": "135"},
            {"index": "yellow-tripdata", "docs.count": "10000"},
            {"index": "wikipedia", "docs.count": "10"},
        ]
    )
    monkeypatch.setattr(tools, "_create_local_opensearch_client", lambda: (fake_client, None))

    result = submit_sample_doc_from_localhost_index("index not-present")

    assert result.startswith("Error: index 'not-present' was not found on local OpenSearch.")
    assert "Available non-system indices on localhost OpenSearch:" in result
    assert "- yellow-tripdata (docs=10,000)" in result
    assert "- wikipedia (docs=10)" in result
    assert "Please choose one index name from this list and retry option 3." in result
    assert fake_client.cat.indices_calls == ["json"]


def test_get_sample_docs_payload_reads_records_from_localhost_index(monkeypatch):
    fake_client = _FakeLocalhostClient(
        search_response={
            "hits": {
                "hits": [
                    {"_source": {"VendorID": "1", "fare_amount": "14.5"}},
                    {"_source": {"VendorID": "2", "fare_amount": "8.0"}},
                ]
            }
        },
    )
    monkeypatch.setattr(tools, "_create_local_opensearch_client", lambda: (fake_client, None))

    docs = get_sample_docs_payload(limit=2, source_index_name="yellow-tripdata")

    assert len(docs) == 2
    assert docs[0]["VendorID"] == "1"
    assert docs[1]["VendorID"] == "2"
    assert fake_client.search_calls
    assert fake_client.search_calls[0][1]["size"] == 2


def test_get_sample_docs_payload_uses_payload_source_index_metadata(monkeypatch):
    fake_client = _FakeLocalhostClient(
        search_response={
            "hits": {
                "hits": [
                    {"_source": {"VendorID": "42", "trip_distance": "3.1"}},
                ]
            }
        },
    )
    monkeypatch.setattr(tools, "_create_local_opensearch_client", lambda: (fake_client, None))
    sample_payload = json.dumps(
        {
            "sample_doc": {"VendorID": "fallback"},
            "source_localhost_index": True,
            "source_index_name": "yellow-tripdata",
        }
    )

    docs = get_sample_docs_payload(limit=1, sample_doc_json=sample_payload)

    assert len(docs) == 1
    assert docs[0]["VendorID"] == "42"


def test_get_sample_docs_payload_falls_back_to_sample_doc_on_index_error(monkeypatch):
    monkeypatch.setattr(
        tools,
        "_create_local_opensearch_client",
        lambda: (None, "Error: unable to connect to local OpenSearch."),
    )
    sample_payload = json.dumps(
        {
            "sample_doc": {"VendorID": "fallback"},
            "source_localhost_index": True,
            "source_index_name": "yellow-tripdata",
        }
    )

    docs = get_sample_docs_payload(limit=1, sample_doc_json=sample_payload)

    assert docs == [{"VendorID": "fallback"}]


def test_extract_path_candidate_supports_parquet_extension(tmp_path):
    parquet_file = tmp_path / "movies.parquet"
    parquet_file.write_text("placeholder", encoding="utf-8")

    detected = _extract_path_candidate(f"Use {parquet_file} for indexing.")

    assert detected == str(parquet_file)


def test_looks_like_local_path_message_supports_parquet_path():
    assert looks_like_local_path_message("Use /data/imdb/title-basics.parquet for sample loading.")


def test_submit_sample_doc_from_local_file_uses_parquet_loader(monkeypatch, tmp_path):
    parquet_file = tmp_path / "movies.parquet"
    parquet_file.write_text("placeholder", encoding="utf-8")

    monkeypatch.setattr(
        tools,
        "_load_records_from_parquet_file",
        lambda file_path, limit=10: ([{"id": 1, "title": "Parquet Sample"}], None),
    )

    result = submit_sample_doc_from_local_file(f"Data source is {parquet_file}.")

    parsed = json.loads(result)
    assert parsed["sample_doc"]["id"] == 1
    assert parsed["sample_doc"]["title"] == "Parquet Sample"
    assert parsed["source_local_file"] == str(parquet_file)


def test_get_sample_docs_payload_reads_records_from_parquet_local_file(monkeypatch, tmp_path):
    parquet_file = tmp_path / "records.parquet"
    parquet_file.write_text("placeholder", encoding="utf-8")

    monkeypatch.setattr(
        tools,
        "_load_records_from_parquet_file",
        lambda file_path, limit=10: (
            [
                {"VendorID": 1, "fare_amount": 14.5},
                {"VendorID": 2, "fare_amount": 8.0},
            ][:limit],
            None,
        ),
    )

    docs = get_sample_docs_payload(limit=2, source_local_file=str(parquet_file))

    assert len(docs) == 2
    assert docs[0]["VendorID"] == 1
    assert docs[1]["VendorID"] == 2


def test_get_sample_docs_payload_accepts_plain_sample_doc_json():
    sample_doc_json = json.dumps(
        {"title": "Manual paste sample", "description": "copied record"},
        ensure_ascii=False,
    )

    docs = get_sample_docs_payload(limit=3, sample_doc_json=sample_doc_json)

    assert docs == [{"title": "Manual paste sample", "description": "copied record"}]
