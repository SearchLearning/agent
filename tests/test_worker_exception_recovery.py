from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import opensearch_orchestrator.worker as worker


def test_retry_once_after_exception_when_recovery_succeeds(monkeypatch):
    calls = {"run_once": 0, "recover": 0}

    def _run_once(_context: str) -> str:
        calls["run_once"] += 1
        if calls["run_once"] == 1:
            raise RuntimeError("boom-1")
        return "success-after-retry"

    def _recover() -> tuple[bool, str]:
        calls["recover"] += 1
        return True, "Recovery succeeded: verified existing running container."

    monkeypatch.setattr(worker, "_run_worker_once", _run_once)
    monkeypatch.setattr(worker, "recover_local_opensearch_container", _recover)

    result = worker._run_worker_with_exception_recovery("context", max_retries_after_exception=1)

    assert result == "success-after-retry"
    assert calls["run_once"] == 2
    assert calls["recover"] == 1


def test_failure_when_recovery_fails(monkeypatch):
    calls = {"run_once": 0, "recover": 0}
    captured: dict[str, object] = {}

    def _run_once(_context: str) -> str:
        calls["run_once"] += 1
        raise RuntimeError("boom-fail")

    def _recover() -> tuple[bool, str]:
        calls["recover"] += 1
        return False, "Recovery failed: Docker daemon is not reachable."

    def _finalize(response_text: str, execution_context: str, report: dict) -> str:
        captured["response_text"] = response_text
        captured["execution_context"] = execution_context
        captured["report"] = report
        return "finalized"

    monkeypatch.setattr(worker, "_run_worker_once", _run_once)
    monkeypatch.setattr(worker, "recover_local_opensearch_container", _recover)
    monkeypatch.setattr(worker, "_finalize_worker_response", _finalize)

    result = worker._run_worker_with_exception_recovery("context", max_retries_after_exception=1)

    assert result == "finalized"
    assert calls["run_once"] == 1
    assert calls["recover"] == 1
    assert "Recovery diagnostics" in str(captured["response_text"])
    assert "Docker daemon is not reachable" in str(captured["response_text"])
    report = captured["report"]
    assert isinstance(report, dict)
    assert report["status"] == "failed"


def test_failure_when_second_attempt_raises(monkeypatch):
    calls = {"run_once": 0, "recover": 0}
    captured: dict[str, object] = {}

    def _run_once(_context: str) -> str:
        calls["run_once"] += 1
        raise RuntimeError(f"boom-{calls['run_once']}")

    def _recover() -> tuple[bool, str]:
        calls["recover"] += 1
        return True, "Recovery succeeded: started existing stopped container."

    def _finalize(response_text: str, execution_context: str, report: dict) -> str:
        captured["response_text"] = response_text
        captured["execution_context"] = execution_context
        captured["report"] = report
        return "finalized"

    monkeypatch.setattr(worker, "_run_worker_once", _run_once)
    monkeypatch.setattr(worker, "recover_local_opensearch_container", _recover)
    monkeypatch.setattr(worker, "_finalize_worker_response", _finalize)

    result = worker._run_worker_with_exception_recovery("context", max_retries_after_exception=1)

    assert result == "finalized"
    assert calls["run_once"] == 2
    assert calls["recover"] == 1
    assert "attempt 1 exception" in str(captured["response_text"])
    assert "attempt 2 exception" in str(captured["response_text"])


def test_retries_even_when_recovery_reports_already_running(monkeypatch):
    calls = {"run_once": 0, "recover": 0}

    def _run_once(_context: str) -> str:
        calls["run_once"] += 1
        if calls["run_once"] == 1:
            raise RuntimeError("first boom")
        return "success-after-verify"

    def _recover() -> tuple[bool, str]:
        calls["recover"] += 1
        return True, "Recovery succeeded: verified existing running container."

    monkeypatch.setattr(worker, "_run_worker_once", _run_once)
    monkeypatch.setattr(worker, "recover_local_opensearch_container", _recover)

    result = worker._run_worker_with_exception_recovery("context", max_retries_after_exception=1)

    assert result == "success-after-verify"
    assert calls["run_once"] == 2
    assert calls["recover"] == 1
