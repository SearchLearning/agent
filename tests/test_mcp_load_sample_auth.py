import os

import opensearch_orchestrator.mcp_server as mcp_server


def test_mcp_load_sample_forwards_localhost_auth_args(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class _RecordingEngine:
        def load_sample(
            self,
            source_type: str,
            source_value: str = "",
            localhost_auth_mode: str = "default",
            localhost_auth_username: str = "",
            localhost_auth_password: str = "",
        ) -> dict:
            captured["source_type"] = source_type
            captured["source_value"] = source_value
            captured["localhost_auth_mode"] = localhost_auth_mode
            captured["localhost_auth_username"] = localhost_auth_username
            captured["localhost_auth_password"] = localhost_auth_password
            return {"status": "ok"}

    monkeypatch.setattr(mcp_server, "_engine", _RecordingEngine())

    result = mcp_server.load_sample(
        source_type="localhost_index",
        source_value="yellow-tripdata",
        localhost_auth_mode="custom",
        localhost_auth_username="alice",
        localhost_auth_password="secret",
    )

    assert result["status"] == "ok"
    assert captured == {
        "source_type": "localhost_index",
        "source_value": "yellow-tripdata",
        "localhost_auth_mode": "custom",
        "localhost_auth_username": "alice",
        "localhost_auth_password": "secret",
    }


def test_workflow_prompt_documents_localhost_default_auth_credentials() -> None:
    prompt = mcp_server.WORKFLOW_PROMPT

    assert '"default": use localhost auth `admin` / `myStrongPassword123!`' in prompt
    assert '"none": force no authentication' in prompt
    assert '"custom": use provided username/password' in prompt


def test_workflow_prompt_forbids_user_facing_default_auth_choice() -> None:
    prompt = mcp_server.WORKFLOW_PROMPT

    assert 'Never present "default" as a user-facing choice.' in prompt
    assert "do not ask for credentials again" in prompt.lower()


def test_mcp_create_index_reuses_localhost_custom_auth_from_engine_state(monkeypatch) -> None:
    captured: dict[str, str | None] = {}

    class _State:
        source_index_name = "yellow-tripdata"
        localhost_auth_mode = "custom"
        localhost_auth_username = "alice"
        localhost_auth_password = "secret"

    class _Engine:
        state = _State()

    def _fake_create_index_impl(**kwargs) -> str:
        _ = kwargs
        captured["mode"] = os.getenv("OPENSEARCH_AUTH_MODE")
        captured["user"] = os.getenv("OPENSEARCH_USER")
        captured["password"] = os.getenv("OPENSEARCH_PASSWORD")
        return "ok"

    monkeypatch.setattr(mcp_server, "_engine", _Engine())
    monkeypatch.setattr(mcp_server, "create_index_impl", _fake_create_index_impl)
    monkeypatch.setenv("OPENSEARCH_AUTH_MODE", "none")
    monkeypatch.setenv("OPENSEARCH_USER", "existing-user")
    monkeypatch.setenv("OPENSEARCH_PASSWORD", "existing-password")

    result = mcp_server.create_index(index_name="idx", body={})

    assert result == "ok"
    assert captured == {
        "mode": "custom",
        "user": "alice",
        "password": "secret",
    }
    assert os.getenv("OPENSEARCH_AUTH_MODE") == "none"
    assert os.getenv("OPENSEARCH_USER") == "existing-user"
    assert os.getenv("OPENSEARCH_PASSWORD") == "existing-password"


def test_mcp_create_index_keeps_existing_env_when_no_localhost_source(monkeypatch) -> None:
    captured: dict[str, str | None] = {}

    class _State:
        source_index_name = None
        localhost_auth_mode = "custom"
        localhost_auth_username = "alice"
        localhost_auth_password = "secret"

    class _Engine:
        state = _State()

    def _fake_create_index_impl(**kwargs) -> str:
        _ = kwargs
        captured["mode"] = os.getenv("OPENSEARCH_AUTH_MODE")
        captured["user"] = os.getenv("OPENSEARCH_USER")
        captured["password"] = os.getenv("OPENSEARCH_PASSWORD")
        return "ok"

    monkeypatch.setattr(mcp_server, "_engine", _Engine())
    monkeypatch.setattr(mcp_server, "create_index_impl", _fake_create_index_impl)
    monkeypatch.setenv("OPENSEARCH_AUTH_MODE", "none")
    monkeypatch.setenv("OPENSEARCH_USER", "existing-user")
    monkeypatch.setenv("OPENSEARCH_PASSWORD", "existing-password")

    result = mcp_server.create_index(index_name="idx", body={})

    assert result == "ok"
    assert captured == {
        "mode": "none",
        "user": "existing-user",
        "password": "existing-password",
    }
