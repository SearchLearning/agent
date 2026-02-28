from pathlib import Path
import os
import sys
import subprocess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import opensearch_orchestrator.scripts.opensearch_ops_tools as tools


def _cp(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def test_run_docker_command_uses_resolved_absolute_cli_path(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(tools, "_resolve_docker_executable", lambda: "/usr/local/bin/docker")

    def _run(command: list[str], check: bool, capture_output: bool, text: bool):
        captured["command"] = command
        captured["check"] = check
        captured["capture_output"] = capture_output
        captured["text"] = text
        return _cp("ok")

    monkeypatch.setattr(tools.subprocess, "run", _run)

    result = tools._run_docker_command(["docker", "--version"])

    assert result.stdout == "ok"
    assert captured["command"] == ["/usr/local/bin/docker", "--version"]
    assert captured["check"] is True
    assert captured["capture_output"] is True
    assert captured["text"] is True


def test_resolve_docker_executable_uses_windows_default_path(monkeypatch):
    expected = os.path.join(
        r"C:\Program Files",
        "Docker",
        "Docker",
        "resources",
        "bin",
        "docker.exe",
    )

    monkeypatch.setattr(tools.platform, "system", lambda: "Windows")
    monkeypatch.delenv("OPENSEARCH_DOCKER_CLI_PATH", raising=False)
    monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
    monkeypatch.setenv("ProgramFiles(x86)", r"C:\Program Files (x86)")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(tools.shutil, "which", lambda _: None)

    original_isfile = tools.os.path.isfile
    monkeypatch.setattr(
        tools.os.path,
        "isfile",
        lambda path: path == expected or original_isfile(path),
    )

    assert tools._resolve_docker_executable() == expected


def test_new_container_bootstrap_sets_default_admin_password(monkeypatch):
    calls: list[list[str]] = []

    def _run(command: list[str]):
        calls.append(command)
        return _cp("ok")

    monkeypatch.delenv("OPENSEARCH_PASSWORD", raising=False)
    monkeypatch.setattr(tools, "_run_docker_command", _run)

    tools._run_new_local_opensearch_container()

    run_command = next(cmd for cmd in calls if cmd[:2] == ["docker", "run"])
    assert "OPENSEARCH_INITIAL_ADMIN_PASSWORD=myStrongPassword123!" in run_command


def test_new_container_bootstrap_uses_env_password(monkeypatch):
    calls: list[list[str]] = []

    def _run(command: list[str]):
        calls.append(command)
        return _cp("ok")

    monkeypatch.setenv("OPENSEARCH_PASSWORD", "AdminPass!234")
    monkeypatch.setattr(tools, "_run_docker_command", _run)

    tools._run_new_local_opensearch_container()

    run_command = next(cmd for cmd in calls if cmd[:2] == ["docker", "run"])
    assert "OPENSEARCH_INITIAL_ADMIN_PASSWORD=AdminPass!234" in run_command


def test_recover_running_container_without_restart(monkeypatch):
    calls: list[list[str]] = []

    monkeypatch.setattr(tools, "_is_local_host", lambda host: True)

    def _run(command: list[str]):
        calls.append(command)
        if command[:2] == ["docker", "--version"]:
            return _cp("Docker version")
        if command[:4] == ["docker", "ps", "-q", "-f"]:
            return _cp("running_id")
        if command[:4] == ["docker", "ps", "-aq", "-f"]:
            return _cp("running_id")
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr(tools, "_run_docker_command", _run)
    monkeypatch.setattr(tools, "_wait_for_cluster_after_start", lambda: object())

    recovered, note = tools.recover_local_opensearch_container()

    assert recovered is True
    assert "verified existing running container" in note.lower()
    assert not any(cmd[:2] == ["docker", "start"] for cmd in calls)
    assert not any(cmd[:2] == ["docker", "pull"] for cmd in calls)
    assert not any(cmd[:2] == ["docker", "run"] for cmd in calls)


def test_recover_starts_existing_stopped_container(monkeypatch):
    calls: list[list[str]] = []

    monkeypatch.setattr(tools, "_is_local_host", lambda host: True)

    def _run(command: list[str]):
        calls.append(command)
        if command[:2] == ["docker", "--version"]:
            return _cp("Docker version")
        if command[:4] == ["docker", "ps", "-q", "-f"]:
            return _cp("")
        if command[:4] == ["docker", "ps", "-aq", "-f"]:
            return _cp("stopped_id")
        if command[:2] == ["docker", "start"]:
            return _cp("stopped_id")
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr(tools, "_run_docker_command", _run)
    monkeypatch.setattr(tools, "_wait_for_cluster_after_start", lambda: object())

    recovered, note = tools.recover_local_opensearch_container()

    assert recovered is True
    assert "started existing stopped container" in note.lower()
    assert any(cmd[:2] == ["docker", "start"] for cmd in calls)


def test_recover_creates_new_container_when_missing(monkeypatch):
    calls: list[list[str]] = []

    monkeypatch.setattr(tools, "_is_local_host", lambda host: True)

    def _run(command: list[str]):
        calls.append(command)
        if command[:2] == ["docker", "--version"]:
            return _cp("Docker version")
        if command[:4] in (["docker", "ps", "-q", "-f"], ["docker", "ps", "-aq", "-f"]):
            return _cp("")
        if command[:2] == ["docker", "pull"]:
            return _cp("pulled")
        if command[:2] == ["docker", "run"]:
            return _cp("new_id")
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr(tools, "_run_docker_command", _run)
    monkeypatch.setattr(tools, "_wait_for_cluster_after_start", lambda: object())

    recovered, note = tools.recover_local_opensearch_container()

    assert recovered is True
    assert "created and started new container" in note.lower()
    assert any(cmd[:2] == ["docker", "pull"] for cmd in calls)
    assert any(cmd[:2] == ["docker", "run"] for cmd in calls)


def test_recover_skips_non_local_host(monkeypatch):
    monkeypatch.setattr(tools, "_is_local_host", lambda host: False)
    monkeypatch.setattr(
        tools,
        "_run_docker_command",
        lambda command: (_ for _ in ()).throw(AssertionError("docker must not be called")),
    )

    recovered, note = tools.recover_local_opensearch_container()

    assert recovered is False
    assert "skip recovery" in note.lower()


def test_recover_reports_docker_daemon_unreachable(monkeypatch):
    monkeypatch.setattr(tools, "_is_local_host", lambda host: True)

    def _run(command: list[str]):
        if command[:2] == ["docker", "--version"]:
            return _cp("Docker version")
        raise RuntimeError("permission denied")

    monkeypatch.setattr(tools, "_run_docker_command", _run)

    recovered, note = tools.recover_local_opensearch_container()

    assert recovered is False
    assert "docker daemon" in note.lower()


def test_create_client_default_mode_uses_admin_credentials(monkeypatch):
    attempts: list[tuple[bool, tuple[str, str] | None]] = []

    class _Client:
        def info(self):
            return {"version": {"number": "2.0.0"}}

    def _build(use_ssl: bool, http_auth: tuple[str, str] | None = None):
        attempts.append((use_ssl, http_auth))
        return _Client()

    monkeypatch.delenv("OPENSEARCH_AUTH_MODE", raising=False)
    monkeypatch.delenv("OPENSEARCH_USER", raising=False)
    monkeypatch.delenv("OPENSEARCH_PASSWORD", raising=False)
    monkeypatch.setattr(tools, "_build_client", _build)

    _ = tools._create_client()

    assert attempts
    assert attempts[0][1] == ("admin", "myStrongPassword123!")


def test_create_client_none_mode_uses_no_auth(monkeypatch):
    attempts: list[tuple[bool, tuple[str, str] | None]] = []

    class _Client:
        def info(self):
            return {"version": {"number": "2.0.0"}}

    def _build(use_ssl: bool, http_auth: tuple[str, str] | None = None):
        attempts.append((use_ssl, http_auth))
        return _Client()

    monkeypatch.setenv("OPENSEARCH_AUTH_MODE", "none")
    monkeypatch.setattr(tools, "_build_client", _build)

    _ = tools._create_client()

    assert attempts
    assert attempts[0][1] is None


def test_create_client_custom_mode_requires_credentials(monkeypatch):
    monkeypatch.setenv("OPENSEARCH_AUTH_MODE", "custom")
    monkeypatch.delenv("OPENSEARCH_USER", raising=False)
    monkeypatch.delenv("OPENSEARCH_PASSWORD", raising=False)

    with pytest.raises(RuntimeError, match="requires OPENSEARCH_USER and OPENSEARCH_PASSWORD"):
        tools._create_client()


def test_create_client_auth_failure_does_not_bootstrap_docker(monkeypatch):
    docker_called = False

    class _AuthFailureClient:
        def info(self):
            raise RuntimeError("401 Unauthorized")

    def _build(use_ssl: bool, http_auth: tuple[str, str] | None = None):
        _ = use_ssl
        _ = http_auth
        return _AuthFailureClient()

    def _start_container():
        nonlocal docker_called
        docker_called = True

    monkeypatch.setenv("OPENSEARCH_AUTH_MODE", "custom")
    monkeypatch.setenv("OPENSEARCH_USER", "customer-user")
    monkeypatch.setenv("OPENSEARCH_PASSWORD", "wrong-password")
    monkeypatch.setattr(tools, "_build_client", _build)
    monkeypatch.setattr(tools, "_start_local_opensearch_container", _start_container)

    with pytest.raises(RuntimeError, match="Authentication failed"):
        tools._create_client()

    assert docker_called is False
