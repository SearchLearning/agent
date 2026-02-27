import asyncio

import opensearch_orchestrator.mcp_server as mcp_server


class _DummyContext:
    def __init__(self) -> None:
        self.session = object()


class _FailingEngine:
    async def start_planning(self, *, additional_context: str = "", planning_agent=None) -> dict:
        _ = additional_context
        _ = planning_agent
        raise Exception("Method not found")


def test_start_planning_returns_manual_fallback_on_method_not_found(monkeypatch) -> None:
    monkeypatch.setenv(mcp_server.PLANNER_MODE_ENV, mcp_server.PLANNER_MODE_CLIENT)
    monkeypatch.setattr(mcp_server, "_engine", _FailingEngine())

    result = asyncio.run(mcp_server.start_planning(ctx=_DummyContext()))

    assert result["error"] == "Planning failed in client mode."
    assert result["planner_backend"] == "client_manual"
    assert result["manual_planning_required"] is True
    assert "sampling/createMessage" in result["hint"]
    assert "bedrock" not in result["hint"].lower()
    assert result["manual_planner_system_prompt"]
    assert result["manual_planner_initial_input"]


def test_set_plan_delegates_normalized_result_to_engine(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class _RecordingEngine:
        def set_plan(self, *, solution: str, search_capabilities: str = "", keynote: str = "") -> dict:
            captured["solution"] = solution
            captured["search_capabilities"] = search_capabilities
            captured["keynote"] = keynote
            return {"status": "Plan stored."}

    monkeypatch.setattr(mcp_server, "_engine", _RecordingEngine())
    monkeypatch.setattr(
        mcp_server,
        "_normalize_manual_plan",
        lambda **kwargs: {
            "solution": "Normalized solution",
            "search_capabilities": "- Exact: title lookup",
            "keynote": "Normalized keynote",
        },
    )

    result = mcp_server.set_plan(
        solution="Use hybrid retrieval",
        search_capabilities="- Exact\n- Semantic",
        keynote="Use balanced profile",
    )

    assert result["status"] == "Plan stored."
    assert captured["solution"] == "Normalized solution"
    assert captured["search_capabilities"] == "- Exact: title lookup"
    assert captured["keynote"] == "Normalized keynote"


def test_set_plan_from_planning_complete_parses_and_delegates(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class _RecordingEngine:
        def set_plan(self, *, solution: str, search_capabilities: str = "", keynote: str = "") -> dict:
            captured["solution"] = solution
            captured["search_capabilities"] = search_capabilities
            captured["keynote"] = keynote
            return {"status": "Plan stored."}

    monkeypatch.setattr(mcp_server, "_engine", _RecordingEngine())
    monkeypatch.setattr(
        mcp_server,
        "_normalize_manual_plan",
        lambda **kwargs: {
            "solution": kwargs["solution"],
            "search_capabilities": kwargs["search_capabilities"],
            "keynote": kwargs["keynote"],
        },
    )

    response = """
Some planner text.
<planning_complete>
  <solution>Use hybrid retrieval</solution>
  <search_capabilities>- Exact: title lookup</search_capabilities>
  <keynote>balanced profile</keynote>
</planning_complete>
"""
    result = mcp_server.set_plan_from_planning_complete(response)
    assert result["status"] == "Plan stored."
    assert captured["solution"] == "Use hybrid retrieval"
    assert captured["search_capabilities"] == "- Exact: title lookup"
    assert captured["keynote"] == "balanced profile"
