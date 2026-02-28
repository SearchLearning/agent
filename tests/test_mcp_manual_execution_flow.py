import asyncio
from pathlib import Path
import sys

from mcp import types as mcp_types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import opensearch_orchestrator.mcp_server as mcp_server


class _DummyContext:
    def __init__(self, session) -> None:
        self.session = session


class _EchoSession:
    def __init__(self) -> None:
        self.message_lengths: list[int] = []
        self.system_prompts: list[str] = []

    async def create_message(self, *, messages, max_tokens, system_prompt):
        _ = max_tokens
        self.message_lengths.append(len(messages))
        self.system_prompts.append(str(system_prompt))
        return type(
            "SamplingResult",
            (),
            {"content": mcp_types.TextContent(type="text", text="ack")},
        )()


def test_talk_to_client_llm_tracks_conversation_and_reset() -> None:
    session = _EchoSession()
    ctx = _DummyContext(session)

    first = asyncio.run(
        mcp_server.talk_to_client_llm(
            system_prompt="sys",
            user_prompt="hello",
            conversation_id="conv-1",
            ctx=ctx,
        )
    )
    second = asyncio.run(
        mcp_server.talk_to_client_llm(
            system_prompt="sys",
            user_prompt="follow up",
            conversation_id="conv-1",
            ctx=ctx,
        )
    )
    third = asyncio.run(
        mcp_server.talk_to_client_llm(
            system_prompt="sys",
            user_prompt="fresh",
            conversation_id="conv-1",
            reset_conversation=True,
            ctx=ctx,
        )
    )

    assert first["llm_backend"] == "client_sampling"
    assert second["llm_backend"] == "client_sampling"
    assert third["llm_backend"] == "client_sampling"
    assert session.message_lengths == [1, 3, 1]


def test_talk_to_client_llm_returns_manual_payload_on_method_not_found() -> None:
    class _MethodNotFoundSession:
        async def create_message(self, **kwargs):
            _ = kwargs
            raise Exception("Method not found")

    result = asyncio.run(
        mcp_server.talk_to_client_llm(
            system_prompt="sys prompt",
            user_prompt="user prompt",
            conversation_id="conv-err",
            ctx=_DummyContext(_MethodNotFoundSession()),
        )
    )

    assert result["error"] == "Client LLM call failed."
    assert result["manual_llm_required"] is True
    assert result["manual_system_prompt"] == "sys prompt"
    assert result["manual_user_prompt"] == "user prompt"


def test_execute_plan_returns_manual_bootstrap(monkeypatch) -> None:
    class _Engine:
        def build_execution_context(self, *, additional_context: str = "") -> dict:
            _ = additional_context
            return {
                "execution_context": (
                    "Solution:\nS\n\nSearch Capabilities:\n- Exact: match\n\nKeynote:\nK"
                )
            }

    monkeypatch.setattr(mcp_server, "_engine", _Engine())

    result = asyncio.run(mcp_server.execute_plan())

    assert result["manual_execution_required"] is True
    assert result["execution_backend"] == "client_manual"
    assert "worker_system_prompt" in result
    assert "worker_initial_input" in result
    assert result["execution_context"].startswith("Solution:\nS")


def test_retry_execution_returns_manual_resume_bootstrap(monkeypatch) -> None:
    class _Engine:
        def build_retry_execution_context(self) -> dict:
            return {
                "execution_context": (
                    f"{mcp_server._RESUME_WORKER_MARKER}\n"
                    "Solution:\nS\n\nSearch Capabilities:\n- Exact: match\n\nKeynote:\nK"
                )
            }

    monkeypatch.setattr(mcp_server, "_engine", _Engine())

    result = asyncio.run(mcp_server.retry_execution())

    assert result["manual_execution_required"] is True
    assert result["is_retry"] is True
    assert result["execution_backend"] == "client_manual"
    assert result["execution_context"].startswith(mcp_server._RESUME_WORKER_MARKER)


def test_set_execution_from_execution_report_updates_engine_phase(monkeypatch) -> None:
    class _Engine:
        phase = None

    monkeypatch.setattr(mcp_server, "_engine", _Engine())
    monkeypatch.setattr(
        mcp_server,
        "commit_execution_report",
        lambda worker_response, execution_context="": {
            "status": "Execution report stored.",
            "execution_context": execution_context,
            "execution_report": {"status": "failed", "steps": {}, "failed_step": "index_setup"},
        },
    )

    result = mcp_server.set_execution_from_execution_report(
        worker_response="<execution_report>{\"status\":\"failed\"}</execution_report>",
        execution_context="Solution:\nS",
    )

    assert result["status"] == "Execution report stored."
    assert result["execution_report"]["status"] == "failed"
    assert mcp_server._engine.phase == mcp_server.Phase.EXEC_FAILED


def test_create_index_uses_engine_sample_source_defaults(monkeypatch) -> None:
    class _State:
        sample_doc_json = '{"tconst":"tt0000001","primaryTitle":"Carmencita"}'
        source_local_file = "opensearch_orchestrator/scripts/sample_data/imdb.title.basics.tsv"
        source_index_name = ""

    class _Engine:
        state = _State()

    monkeypatch.setattr(mcp_server, "_engine", _Engine())

    captured: dict[str, object] = {}

    def _fake_create_index_impl(**kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(mcp_server, "create_index_impl", _fake_create_index_impl)

    result = mcp_server.create_index(index_name="imdb-movies", body={"settings": {}})

    assert result == "ok"
    assert captured["sample_doc_json"] == _State.sample_doc_json
    assert captured["source_local_file"] == _State.source_local_file
    assert captured["source_index_name"] == ""


def test_apply_capability_driven_verification_uses_engine_sample_source_defaults(
    monkeypatch,
) -> None:
    class _State:
        sample_doc_json = '{"tconst":"tt0000001","primaryTitle":"Carmencita"}'
        source_local_file = "opensearch_orchestrator/scripts/sample_data/imdb.title.basics.tsv"
        source_index_name = ""

    class _Engine:
        state = _State()

    monkeypatch.setattr(mcp_server, "_engine", _Engine())

    captured: dict[str, object] = {}

    def _fake_apply_impl(**kwargs):
        captured.update(kwargs)
        return {"applied": True, "suggestion_meta": [], "notes": []}

    monkeypatch.setattr(
        mcp_server,
        "apply_capability_driven_verification_impl",
        _fake_apply_impl,
    )

    result = asyncio.run(
        mcp_server.apply_capability_driven_verification(
            worker_output="worker output",
            index_name="imdb-movies",
            ctx=None,
        )
    )

    assert result["applied"] is True
    assert captured["sample_doc_json"] == _State.sample_doc_json
    assert captured["source_local_file"] == _State.source_local_file
    assert captured["source_index_name"] == ""


def test_apply_capability_driven_verification_rewrites_semantic_text_with_client_llm(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        mcp_server,
        "apply_capability_driven_verification_impl",
        lambda **kwargs: {
            "applied": True,
            "suggestion_meta": [
                {"capability": "semantic", "text": "raw semantic text"},
                {"capability": "exact", "text": "exact title"},
            ],
            "notes": [],
            "kwargs_seen": kwargs,
        },
    )

    class _Session:
        async def create_message(self, **kwargs):
            _ = kwargs
            return type(
                "SamplingResult",
                (),
                {"content": mcp_types.TextContent(type="text", text="semantic rewritten query")},
            )()

    result = asyncio.run(
        mcp_server.apply_capability_driven_verification(
            worker_output="worker output",
            index_name="idx",
            ctx=_DummyContext(_Session()),
        )
    )

    suggestion_meta = result["suggestion_meta"]
    semantic_entry = next(item for item in suggestion_meta if item.get("capability") == "semantic")
    exact_entry = next(item for item in suggestion_meta if item.get("capability") == "exact")
    assert semantic_entry["text"] == "semantic rewritten query"
    assert exact_entry["text"] == "exact title"
