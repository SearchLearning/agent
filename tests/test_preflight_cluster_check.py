from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import opensearch_orchestrator.opensearch_ops_tools as tools


class _ConnectableClient:
    """Simulates a cluster that responds successfully."""

    def info(self):
        return {"version": {"number": "2.19.0"}}


class _AuthFailureClient:
    """Simulates a cluster that rejects credentials."""

    def info(self):
        raise RuntimeError("401 Unauthorized")


class _UnreachableClient:
    """Simulates no cluster listening."""

    def info(self):
        from opensearchpy.exceptions import ConnectionError as OSConnectionError

        raise OSConnectionError("N/A", "Connection refused", Exception("refused"))


def test_preflight_detects_no_auth_cluster(monkeypatch):
    """No-auth insecure cluster is detected as available."""

    def _build(use_ssl: bool, http_auth=None):
        if not use_ssl and http_auth is None:
            return _ConnectableClient()
        return _UnreachableClient()

    monkeypatch.setattr(tools, "_build_client", _build)

    result = tools.preflight_check_cluster()

    assert result["status"] == "available"
    assert result["auth_mode"] == "none"
    assert "security disabled" in result["message"]


def test_preflight_detects_ssl_no_auth_cluster(monkeypatch):
    """No-auth SSL cluster is detected as available."""

    call_count = 0

    def _build(use_ssl: bool, http_auth=None):
        nonlocal call_count
        call_count += 1
        if use_ssl and http_auth is None:
            return _ConnectableClient()
        return _UnreachableClient()

    monkeypatch.setattr(tools, "_build_client", _build)

    result = tools.preflight_check_cluster()

    assert result["status"] == "available"
    assert result["auth_mode"] == "none"


def test_preflight_detects_default_creds_cluster(monkeypatch):
    """Cluster reachable with default admin creds is detected as available."""

    def _build(use_ssl: bool, http_auth=None):
        if http_auth == ("admin", "myStrongPassword123!"):
            return _ConnectableClient()
        return _AuthFailureClient() if http_auth is None else _UnreachableClient()

    # Make no-auth attempts fail with auth error (cluster is there but needs creds)
    # and default-creds attempts succeed
    def _build_v2(use_ssl: bool, http_auth=None):
        if http_auth is None:
            return _UnreachableClient()
        if http_auth == ("admin", "myStrongPassword123!"):
            return _ConnectableClient()
        return _UnreachableClient()

    monkeypatch.setattr(tools, "_build_client", _build_v2)

    result = tools.preflight_check_cluster()

    assert result["status"] == "available"
    assert result["auth_mode"] == "default"
    assert "default credentials" in result["message"]


def test_preflight_detects_auth_required(monkeypatch):
    """Cluster that rejects both no-auth and default creds returns auth_required."""

    def _build(use_ssl: bool, http_auth=None):
        # Everything gets auth failure — cluster is there but rejects all
        return _AuthFailureClient()

    monkeypatch.setattr(tools, "_build_client", _build)

    result = tools.preflight_check_cluster()

    assert result["status"] == "auth_required"
    assert "authentication failed" in result["message"].lower()


def test_preflight_ssl_cluster_with_custom_creds_not_misdetected(monkeypatch):
    """HTTPS cluster with non-default creds: no-auth gets auth failure,
    default creds also get auth failure. Should return auth_required, not no_cluster."""

    def _build(use_ssl: bool, http_auth=None):
        if not use_ssl:
            # HTTP probes: connection refused (cluster only listens on HTTPS)
            return _UnreachableClient()
        # HTTPS probes: cluster is there but rejects everything we try
        return _AuthFailureClient()

    monkeypatch.setattr(tools, "_build_client", _build)

    result = tools.preflight_check_cluster()

    assert result["status"] == "auth_required"
    assert "authentication failed" in result["message"].lower()
    # All 4 probes should have been tried
    assert len(result["auth_modes_tried"]) == 4


def test_preflight_ssl_default_creds_succeeds_after_noauth_authfail(monkeypatch):
    """HTTPS cluster where no-auth returns 401 but default creds work.
    This is the common Docker OpenSearch case."""

    def _build(use_ssl: bool, http_auth=None):
        if not use_ssl:
            return _UnreachableClient()
        if http_auth is None:
            return _AuthFailureClient()
        if http_auth == ("admin", "myStrongPassword123!"):
            return _ConnectableClient()
        return _AuthFailureClient()

    monkeypatch.setattr(tools, "_build_client", _build)

    result = tools.preflight_check_cluster()

    assert result["status"] == "available"
    assert result["auth_mode"] == "default"
    assert "SSL" in result["message"]


def test_preflight_detects_no_cluster(monkeypatch):
    """Nothing listening returns no_cluster."""

    def _build(use_ssl: bool, http_auth=None):
        return _UnreachableClient()

    monkeypatch.setattr(tools, "_build_client", _build)

    result = tools.preflight_check_cluster()

    assert result["status"] == "no_cluster"
    assert "no opensearch cluster detected" in result["message"].lower()
    assert result["is_local"] is True


def test_preflight_returns_host_port_info(monkeypatch):
    """Result always includes host, port, and is_local."""

    def _build(use_ssl: bool, http_auth=None):
        return _UnreachableClient()

    monkeypatch.setattr(tools, "_build_client", _build)

    result = tools.preflight_check_cluster()

    assert result["host"] == "localhost"
    assert result["port"] == 9200
    assert result["is_local"] is True
    assert "auth_modes_tried" in result


def test_cluster_auth_error_is_runtime_error():
    """ClusterAuthError is a subclass of RuntimeError for backward compat."""
    err = tools.ClusterAuthError("localhost", 9200)
    assert isinstance(err, RuntimeError)
    assert err.host == "localhost"
    assert err.port == 9200
    assert "localhost:9200" in str(err)


def test_create_client_raises_cluster_auth_error_on_auth_failure(monkeypatch):
    """_create_client raises ClusterAuthError (not plain RuntimeError) on auth failure."""

    def _build(use_ssl: bool, http_auth=None):
        return _AuthFailureClient()

    monkeypatch.setenv("OPENSEARCH_AUTH_MODE", "custom")
    monkeypatch.setenv("OPENSEARCH_USER", "user")
    monkeypatch.setenv("OPENSEARCH_PASSWORD", "wrong")
    monkeypatch.setattr(tools, "_build_client", _build)
    monkeypatch.setattr(
        tools, "_start_local_opensearch_container", lambda: None
    )

    with pytest.raises(tools.ClusterAuthError):
        tools._create_client()


# --- Tests for auth_mode / username / password params and cleanup ---


def test_preflight_custom_creds_success_sets_env_vars(monkeypatch):
    """Custom creds that succeed set OPENSEARCH_AUTH_MODE/USER/PASSWORD env vars."""

    def _build(use_ssl: bool, http_auth=None):
        if http_auth == ("myuser", "mypass"):
            return _ConnectableClient()
        return _UnreachableClient()

    monkeypatch.setattr(tools, "_build_client", _build)
    # Ensure env is clean before
    monkeypatch.delenv("OPENSEARCH_AUTH_MODE", raising=False)
    monkeypatch.delenv("OPENSEARCH_USER", raising=False)
    monkeypatch.delenv("OPENSEARCH_PASSWORD", raising=False)

    result = tools.preflight_check_cluster(
        auth_mode="custom", username="myuser", password="mypass"
    )

    assert result["status"] == "available"
    assert result["auth_mode"] == "custom"
    assert "myuser" not in result["message"]  # creds not leaked in message
    import os

    assert os.environ.get("OPENSEARCH_AUTH_MODE") == "custom"
    assert os.environ.get("OPENSEARCH_USER") == "myuser"
    assert os.environ.get("OPENSEARCH_PASSWORD") == "mypass"


def test_preflight_custom_creds_failure(monkeypatch):
    """Custom creds that fail return auth_required without setting env vars."""

    def _build(use_ssl: bool, http_auth=None):
        return _AuthFailureClient() if http_auth else _UnreachableClient()

    monkeypatch.setattr(tools, "_build_client", _build)
    monkeypatch.delenv("OPENSEARCH_AUTH_MODE", raising=False)
    monkeypatch.delenv("OPENSEARCH_USER", raising=False)
    monkeypatch.delenv("OPENSEARCH_PASSWORD", raising=False)

    result = tools.preflight_check_cluster(
        auth_mode="custom", username="bad", password="wrong"
    )

    assert result["status"] == "auth_required"
    assert "rejected" in result["message"].lower()
    import os

    assert os.environ.get("OPENSEARCH_AUTH_MODE") is None
    assert os.environ.get("OPENSEARCH_USER") is None


def test_preflight_custom_creds_missing_returns_error(monkeypatch):
    """Custom mode without username/password returns error."""

    def _build(use_ssl: bool, http_auth=None):
        return _UnreachableClient()

    monkeypatch.setattr(tools, "_build_client", _build)

    result = tools.preflight_check_cluster(auth_mode="custom", username="", password="")

    assert result["status"] == "error"
    assert "requires" in result["message"].lower()


def test_preflight_none_mode_success_sets_env(monkeypatch):
    """Explicit none mode that succeeds sets OPENSEARCH_AUTH_MODE=none."""

    def _build(use_ssl: bool, http_auth=None):
        if http_auth is None:
            return _ConnectableClient()
        return _UnreachableClient()

    monkeypatch.setattr(tools, "_build_client", _build)
    monkeypatch.delenv("OPENSEARCH_AUTH_MODE", raising=False)

    result = tools.preflight_check_cluster(auth_mode="none")

    assert result["status"] == "available"
    assert result["auth_mode"] == "none"
    import os

    assert os.environ.get("OPENSEARCH_AUTH_MODE") == "none"
    assert os.environ.get("OPENSEARCH_USER") is None


def test_preflight_none_mode_failure(monkeypatch):
    """Explicit none mode that fails returns auth_required."""

    def _build(use_ssl: bool, http_auth=None):
        return _AuthFailureClient()

    monkeypatch.setattr(tools, "_build_client", _build)

    result = tools.preflight_check_cluster(auth_mode="none")

    assert result["status"] == "auth_required"


def test_clear_cluster_credentials(monkeypatch):
    """clear_cluster_credentials removes all auth env vars."""
    import os

    monkeypatch.setenv("OPENSEARCH_AUTH_MODE", "custom")
    monkeypatch.setenv("OPENSEARCH_USER", "admin")
    monkeypatch.setenv("OPENSEARCH_PASSWORD", "secret")

    tools.clear_cluster_credentials()

    assert os.environ.get("OPENSEARCH_AUTH_MODE") is None
    assert os.environ.get("OPENSEARCH_USER") is None
    assert os.environ.get("OPENSEARCH_PASSWORD") is None


def test_clear_cluster_credentials_noop_when_unset():
    """clear_cluster_credentials is safe to call when env vars are not set."""
    import os

    os.environ.pop("OPENSEARCH_AUTH_MODE", None)
    os.environ.pop("OPENSEARCH_USER", None)
    os.environ.pop("OPENSEARCH_PASSWORD", None)

    # Should not raise
    tools.clear_cluster_credentials()


def test_autodetect_sets_env_for_default_creds(monkeypatch):
    """Auto-detect mode that finds default creds sets OPENSEARCH_AUTH_MODE=default."""

    def _build(use_ssl: bool, http_auth=None):
        if http_auth == ("admin", "myStrongPassword123!") and use_ssl:
            return _ConnectableClient()
        if http_auth is None:
            return _AuthFailureClient()
        return _UnreachableClient()

    monkeypatch.setattr(tools, "_build_client", _build)
    monkeypatch.delenv("OPENSEARCH_AUTH_MODE", raising=False)

    result = tools.preflight_check_cluster()

    assert result["status"] == "available"
    assert result["auth_mode"] == "default"
    import os

    assert os.environ.get("OPENSEARCH_AUTH_MODE") == "default"


def test_autodetect_sets_env_for_none_mode(monkeypatch):
    """Auto-detect mode that finds no-auth cluster sets OPENSEARCH_AUTH_MODE=none."""

    def _build(use_ssl: bool, http_auth=None):
        if not use_ssl and http_auth is None:
            return _ConnectableClient()
        return _UnreachableClient()

    monkeypatch.setattr(tools, "_build_client", _build)
    monkeypatch.delenv("OPENSEARCH_AUTH_MODE", raising=False)

    result = tools.preflight_check_cluster()

    assert result["status"] == "available"
    assert result["auth_mode"] == "none"
    import os

    assert os.environ.get("OPENSEARCH_AUTH_MODE") == "none"
    assert os.environ.get("OPENSEARCH_USER") is None
