from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import opensearch_orchestrator.scripts.opensearch_ops_tools as tools


class _FakeTransport:
    def __init__(self, deployment_errors: list[object] | None = None):
        self.requests: list[tuple[str, str, object]] = []
        self._deploy_attempt = 0
        self._deployment_errors = deployment_errors or []

    def perform_request(self, method, path, body=None):
        self.requests.append((method, path, body))

        if method == "POST" and path == "/_plugins/_ml/models/_register":
            return {"task_id": "register-task-1"}
        if method == "GET" and path == "/_plugins/_ml/tasks/register-task-1":
            return {"state": "COMPLETED", "model_id": "new-model"}
        if method == "POST" and path == "/_plugins/_ml/models/new-model/_deploy":
            self._deploy_attempt += 1
            return {"task_id": f"deploy-task-{self._deploy_attempt}"}
        if method == "GET" and path.startswith("/_plugins/_ml/tasks/deploy-task-"):
            task_num = int(path.rsplit("-", 1)[-1])
            if task_num <= len(self._deployment_errors):
                return {"state": "FAILED", "error": self._deployment_errors[task_num - 1]}
            return {"state": "COMPLETED"}
        if method == "POST" and path == "/_plugins/_ml/models/_search":
            return {
                "hits": {
                    "hits": [
                        {"_id": "old-model", "_source": {"model_state": "DEPLOYED"}},
                        {"_id": "new-model", "_source": {"model_state": "REGISTERED"}},
                    ]
                }
            }
        if method == "POST" and path == "/_plugins/_ml/models/old-model/_undeploy":
            return {"task_id": "undeploy-task-1"}
        if method == "GET" and path == "/_plugins/_ml/tasks/undeploy-task-1":
            return {"state": "COMPLETED"}

        raise AssertionError(f"Unexpected request: {method} {path}")


class _FakeClient:
    def __init__(self, deployment_errors: list[object] | None = None):
        self.transport = _FakeTransport(deployment_errors=deployment_errors)


def test_create_local_pretrained_model_recovers_by_undeploying_existing_model(monkeypatch):
    fake_client = _FakeClient(
        deployment_errors=[{"old-model": "Exceed max local model per node limit"}]
    )
    monkeypatch.setattr(tools, "_create_client", lambda: fake_client)
    monkeypatch.setattr(tools, "set_ml_settings", lambda opensearch_client=None: None)
    monkeypatch.setattr(tools.time, "sleep", lambda _: None)

    response = tools.create_local_pretrained_model(
        "amazon/neural-sparse/opensearch-neural-sparse-tokenizer-v1"
    )

    assert "created and deployed successfully" in response
    assert "after undeploying model(s): old-model" in response
    deploy_calls = [
        req
        for req in fake_client.transport.requests
        if req[0] == "POST" and req[1] == "/_plugins/_ml/models/new-model/_deploy"
    ]
    undeploy_calls = [
        req
        for req in fake_client.transport.requests
        if req[0] == "POST" and req[1] == "/_plugins/_ml/models/old-model/_undeploy"
    ]
    assert len(deploy_calls) == 2
    assert len(undeploy_calls) == 1


def test_create_local_pretrained_model_skips_undeploy_for_non_capacity_errors(monkeypatch):
    fake_client = _FakeClient(deployment_errors=[{"old-model": "some other deployment issue"}])
    monkeypatch.setattr(tools, "_create_client", lambda: fake_client)
    monkeypatch.setattr(tools, "set_ml_settings", lambda opensearch_client=None: None)
    monkeypatch.setattr(tools.time, "sleep", lambda _: None)

    response = tools.create_local_pretrained_model(
        "amazon/neural-sparse/opensearch-neural-sparse-tokenizer-v1"
    )

    assert "Model deployment failed:" in response
    assert "some other deployment issue" in response
    assert not any(req[1] == "/_plugins/_ml/models/_search" for req in fake_client.transport.requests)
    assert not any(req[1].endswith("/_undeploy") for req in fake_client.transport.requests)
