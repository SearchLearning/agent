from opensearchpy import OpenSearch

from opensearch_orchestrator.shared import normalize_text, value_shape, text_richness_score
import getpass
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from opensearch_orchestrator.tools import get_sample_docs_payload, normalize_ingest_source_field_hints


class ClusterAuthError(RuntimeError):
    """Raised when an existing cluster is detected but authentication fails.

    Attributes:
        host: The OpenSearch host that was probed.
        port: The OpenSearch port that was probed.
    """

    def __init__(self, host: str, port: int, message: str | None = None) -> None:
        self.host = host
        self.port = port
        super().__init__(
            message
            or (
                f"An OpenSearch cluster is running at {host}:{port} but authentication "
                f"failed. Provide valid credentials via OPENSEARCH_AUTH_MODE=custom with "
                f"OPENSEARCH_USER and OPENSEARCH_PASSWORD, or use "
                f"OPENSEARCH_AUTH_MODE=none if security is disabled."
            )
        )

OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "localhost")
OPENSEARCH_PORT = int(os.getenv("OPENSEARCH_PORT", "9200"))
OPENSEARCH_AUTH_MODE_ENV = "OPENSEARCH_AUTH_MODE"
OPENSEARCH_AUTH_MODE_DEFAULT = "default"
OPENSEARCH_AUTH_MODE_NONE = "none"
OPENSEARCH_AUTH_MODE_CUSTOM = "custom"
OPENSEARCH_USER_ENV = "OPENSEARCH_USER"
OPENSEARCH_PASSWORD_ENV = "OPENSEARCH_PASSWORD"
OPENSEARCH_DEFAULT_USER = "admin"
OPENSEARCH_DEFAULT_PASSWORD = "myStrongPassword123!"
OPENSEARCH_DOCKER_IMAGE = os.getenv("OPENSEARCH_DOCKER_IMAGE", "opensearchproject/opensearch:latest")
OPENSEARCH_DOCKER_CONTAINER = os.getenv("OPENSEARCH_DOCKER_CONTAINER", "opensearch-local")
OPENSEARCH_DOCKER_START_TIMEOUT = int(os.getenv("OPENSEARCH_DOCKER_START_TIMEOUT", "120"))
OPENSEARCH_DOCKER_CLI_PATH_ENV = "OPENSEARCH_DOCKER_CLI_PATH"
SEARCH_UI_HOST = os.getenv("SEARCH_UI_HOST", "127.0.0.1")
SEARCH_UI_PORT = int(os.getenv("SEARCH_UI_PORT", "8765"))
try:
    SEARCH_UI_IDLE_TIMEOUT_SECONDS = max(
        60, int(os.getenv("SEARCH_UI_IDLE_TIMEOUT_SECONDS", "2700"))
    )
except ValueError:
    SEARCH_UI_IDLE_TIMEOUT_SECONDS = 2700
SEARCH_UI_STATIC_DIR = (
    Path(__file__).resolve().parent / "ui" / "search_builder"
)
_MODEL_MEMORY_SIGNAL_TOKENS = (
    "memory constraints",
    "memory constraint",
    "native memory",
    "out of memory",
    "outofmemory",
    "circuit_breaking_exception",
    "ml_commons.native_memory_threshold",
)
_MODEL_LOCAL_LIMIT_SIGNAL_TOKENS = (
    "exceed max local model per node limit",
    "max local model per node limit",
)
_AUTH_FAILURE_TOKENS = (
    "401",
    "403",
    "unauthorized",
    "forbidden",
    "authentication",
    "security_exception",
    "missing authentication credentials",
)
SEMANTIC_QUERY_REWRITE_FLAG = "SEMANTIC_QUERY_REWRITE_USE_LLM"
SEMANTIC_QUERY_REWRITE_MODEL_ID_ENV = "SEMANTIC_QUERY_REWRITE_MODEL_ID"
DEFAULT_SEMANTIC_QUERY_REWRITE_MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
RUNTIME_MODE_ENV = "OPENSEARCH_RUNTIME_MODE"
RUNTIME_MODE_MCP = "mcp"

_SEARCH_UI_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".jsx": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}

_BOOLEAN_STRING_FLAG_VALUES = {"0", "1", "true", "false", "yes", "no", "y", "n", "t", "f"}

class _SearchUIRuntime:
    def __init__(self) -> None:
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.default_index: str = ""
        self.lock = threading.Lock()
        self.suggestion_meta_by_index: dict[str, list[dict[str, object]]] = {}
        self.endpoint_override_host: str = ""
        self.endpoint_override_port: int = 0
        self.endpoint_override_use_ssl: bool = True
        self.endpoint_override_auth: tuple[str, str] | None = None
        self.endpoint_override_aws_region: str = ""
        self.endpoint_override_aws_service: str = ""

_search_ui = _SearchUIRuntime()

_UI_STATE_FILE = Path(tempfile.gettempdir()) / f"opensearch_search_ui_{SEARCH_UI_PORT}.json"
_ui_state_mtime: float = 0.0
_SEARCH_UI_SERVICE_NAME = "opensearch-search-ui"
_UI_LOCK_BASENAME = f"opensearch_search_ui_{SEARCH_UI_PORT}.lock"

try:
    _CURRENT_UID: int | None = os.getuid()
except AttributeError:
    _CURRENT_UID = None
try:
    _CURRENT_USERNAME = getpass.getuser()
except Exception:
    _CURRENT_USERNAME = "unknown"

if _CURRENT_UID is not None:
    _ui_owner_token = f"uid_{_CURRENT_UID}"
else:
    _ui_owner_token = re.sub(r"[^A-Za-z0-9_.-]", "_", _CURRENT_USERNAME or "unknown")

_UI_RUNTIME_DIR = Path(tempfile.gettempdir()) / f"opensearch_search_ui_{_ui_owner_token}"
_UI_LOCK_FILE = _UI_RUNTIME_DIR / _UI_LOCK_BASENAME
_UI_SERVER_SCRIPT_NAME = "ui_server_standalone.py"
_UI_SERVER_MODULE = "opensearch_orchestrator.ui_server_standalone"

_ui_instance_id: str = ""
_ui_idle_timeout_seconds: int = SEARCH_UI_IDLE_TIMEOUT_SECONDS
_ui_last_active_epoch: float = 0.0


def _write_ui_state() -> None:
    """Persist UI config so the standalone subprocess can pick it up."""
    state: dict[str, object] = {
        "default_index": _search_ui.default_index,
        "suggestion_meta_by_index": _search_ui.suggestion_meta_by_index,
    }
    # Persist endpoint override so the detached UI server subprocess can use it.
    if _search_ui.endpoint_override_host:
        state["endpoint_override"] = {
            "host": _search_ui.endpoint_override_host,
            "port": _search_ui.endpoint_override_port,
            "use_ssl": _search_ui.endpoint_override_use_ssl,
            "aws_region": _search_ui.endpoint_override_aws_region,
            "aws_service": _search_ui.endpoint_override_aws_service,
        }
        if _search_ui.endpoint_override_auth is not None:
            state["endpoint_override"]["username"] = _search_ui.endpoint_override_auth[0]
            state["endpoint_override"]["password"] = _search_ui.endpoint_override_auth[1]
    else:
        state["endpoint_override"] = None
    try:
        _UI_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _maybe_reload_ui_state() -> None:
    """Reload UI state from disk if the state file was updated externally."""
    global _ui_state_mtime
    try:
        mtime = _UI_STATE_FILE.stat().st_mtime
    except OSError:
        return
    if mtime <= _ui_state_mtime:
        return
    try:
        state = json.loads(_UI_STATE_FILE.read_text(encoding="utf-8"))
        _search_ui.default_index = state.get("default_index", "")
        _search_ui.suggestion_meta_by_index = state.get("suggestion_meta_by_index", {})
        # Restore endpoint override from persisted state.
        override = state.get("endpoint_override")
        if isinstance(override, dict) and override.get("host"):
            _search_ui.endpoint_override_host = str(override.get("host", ""))
            _search_ui.endpoint_override_port = int(override.get("port", 443))
            _search_ui.endpoint_override_use_ssl = bool(override.get("use_ssl", True))
            _search_ui.endpoint_override_aws_region = str(override.get("aws_region", ""))
            _search_ui.endpoint_override_aws_service = str(override.get("aws_service", ""))
            username = str(override.get("username", ""))
            password = str(override.get("password", ""))
            if username and password:
                _search_ui.endpoint_override_auth = (username, password)
            else:
                _search_ui.endpoint_override_auth = None
        else:
            _search_ui.endpoint_override_host = ""
            _search_ui.endpoint_override_port = 0
            _search_ui.endpoint_override_auth = None
            _search_ui.endpoint_override_aws_region = ""
            _search_ui.endpoint_override_aws_service = ""
        _ui_state_mtime = mtime
    except (OSError, json.JSONDecodeError, ValueError):
        pass


def _ensure_ui_runtime_dir() -> None:
    try:
        _UI_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        if hasattr(os, "chmod"):
            os.chmod(_UI_RUNTIME_DIR, 0o700)
    except OSError:
        pass


def _read_ui_lock() -> dict[str, object] | None:
    try:
        raw = _UI_LOCK_FILE.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return None


def _write_ui_lock(payload: dict[str, object]) -> None:
    _ensure_ui_runtime_dir()
    tmp_file = _UI_LOCK_FILE.with_suffix(_UI_LOCK_FILE.suffix + ".tmp")
    try:
        tmp_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp_file.replace(_UI_LOCK_FILE)
    except OSError:
        try:
            tmp_file.unlink()
        except OSError:
            pass


def _remove_ui_lock() -> None:
    try:
        _UI_LOCK_FILE.unlink()
    except OSError:
        pass


def _configure_ui_server_runtime(
    instance_id: str = "",
    idle_timeout_seconds: int = SEARCH_UI_IDLE_TIMEOUT_SECONDS,
) -> None:
    global _ui_instance_id, _ui_idle_timeout_seconds
    normalized_instance = str(instance_id or "").strip()
    try:
        timeout_val = int(idle_timeout_seconds or SEARCH_UI_IDLE_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        timeout_val = SEARCH_UI_IDLE_TIMEOUT_SECONDS
    _ui_instance_id = normalized_instance
    _ui_idle_timeout_seconds = max(60, timeout_val)


def _register_ui_server_lock() -> None:
    """Register the current process as the owned standalone UI server.

    Lock file shape (example):
    {
      "pid": 12345,
      "uid": 501,
      "username": "kaituo",
      "port": 8765,
      "project_root": "/Users/kaituo/code/poc/agent-poc",
      "instance_id": "7d5c...",
      "started_epoch": 1739999999.12,
      "last_active_epoch": 1739999999.12
    }

    The lock file is stored under a user-scoped runtime directory with mode 0700.
    """
    global _ui_last_active_epoch
    now = time.time()
    _ui_last_active_epoch = now
    lock_payload = {
        "service": _SEARCH_UI_SERVICE_NAME,
        "pid": os.getpid(),
        "uid": _CURRENT_UID,
        "username": _CURRENT_USERNAME,
        "port": SEARCH_UI_PORT,
        "host": SEARCH_UI_HOST,
        "project_root": str(Path(__file__).resolve().parents[2]),
        "script_path": _UI_SERVER_MODULE,
        "instance_id": _ui_instance_id,
        "started_epoch": now,
        "last_active_epoch": now,
        "idle_timeout_seconds": _ui_idle_timeout_seconds,
    }
    _write_ui_lock(lock_payload)


def _clear_ui_server_lock_if_owned_by_current_process() -> None:
    """Remove lock file only when it belongs to this exact process instance.

    Why this exists:
    - PID alone is not safe because PIDs can be reused.
    - A lock file must represent process ownership, not just a port.

    Ownership pattern used by this module:
    1. `uid` + user-private lock location differentiates OS users.
    2. `pid` must still be alive.
    3. Command must match the UI server script.
    4. `instance_id` must match the launched server instance.
    5. (Optional conceptual check) process start time should match lock start time.

    This specific cleanup helper is intentionally strict and local: it only unlinks
    the lock when the lock points to *this* process (`pid` + `instance_id` match).
    Broader ownership validation for reuse/stop is handled in
    `_is_owned_ui_process()`.
    """
    lock = _read_ui_lock()
    if not lock:
        return
    try:
        lock_pid = int(lock.get("pid", 0))
    except (TypeError, ValueError):
        return
    lock_instance_id = str(lock.get("instance_id", "")).strip()
    if lock_pid != os.getpid():
        return
    if _ui_instance_id and lock_instance_id and lock_instance_id != _ui_instance_id:
        return
    _remove_ui_lock()


def _record_ui_activity() -> None:
    global _ui_last_active_epoch
    now = time.time()
    _ui_last_active_epoch = now

    lock = _read_ui_lock()
    if not lock:
        return
    try:
        lock_pid = int(lock.get("pid", 0))
    except (TypeError, ValueError):
        return
    lock_instance_id = str(lock.get("instance_id", "")).strip()
    if lock_pid != os.getpid():
        return
    if _ui_instance_id and lock_instance_id and lock_instance_id != _ui_instance_id:
        return

    lock["last_active_epoch"] = now
    if _ui_idle_timeout_seconds:
        lock["idle_timeout_seconds"] = _ui_idle_timeout_seconds
    _write_ui_lock(lock)


def _should_ui_server_auto_stop(now: float | None = None) -> bool:
    if _ui_idle_timeout_seconds <= 0:
        return False
    reference = now if now is not None else time.time()
    last_active = _ui_last_active_epoch or reference
    return (reference - last_active) >= _ui_idle_timeout_seconds


def _list_listener_pids_on_ui_port() -> list[int]:
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{SEARCH_UI_PORT}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return []

    pids: list[int] = []
    for token in result.stdout.strip().split():
        if not token.strip().isdigit():
            continue
        pid = int(token)
        if pid == os.getpid():
            continue
        pids.append(pid)
    return sorted(set(pids))


def _is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat="],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            state = result.stdout.strip().upper()
            if state.startswith("Z"):
                return False
    except Exception:
        pass
    return True


def _get_process_command(pid: int) -> str:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _get_lock_pid(lock: dict[str, object]) -> int:
    try:
        return int(lock.get("pid", 0))
    except (TypeError, ValueError):
        return 0


def _is_owned_ui_process(lock: dict[str, object] | None) -> tuple[bool, str]:
    """Validate that a lock points to an owned Search UI server process.

    Conceptual ownership policy:
    - `lock.uid == os.getuid()` (user boundary)
    - `lock.pid` is alive
    - command line contains module/server marker
    - command line contains the expected `instance_id`
    - optional hardening: process start time equals lock start time

    Kill/reuse policy downstream:
    - all checks pass: safe to reuse/stop
    - checks fail but port is occupied: do not kill, return conflict guidance
    - lock exists but pid is dead: treat as stale lock and remove it
    """
    if not lock:
        return False, "lock file missing"

    lock_port = lock.get("port")
    if isinstance(lock_port, int) and lock_port != SEARCH_UI_PORT:
        return False, "lock port mismatch"

    lock_uid = lock.get("uid")
    if (
        isinstance(lock_uid, int)
        and _CURRENT_UID is not None
        and lock_uid != _CURRENT_UID
    ):
        return False, "uid mismatch"

    pid = _get_lock_pid(lock)
    if pid <= 0:
        return False, "invalid lock pid"
    if not _is_pid_running(pid):
        return False, "lock pid not running"

    cmd = _get_process_command(pid)
    if not cmd:
        return False, "cannot inspect process command"
    if _UI_SERVER_MODULE not in cmd and _UI_SERVER_SCRIPT_NAME not in cmd:
        return False, "process command mismatch"

    instance_id = str(lock.get("instance_id", "")).strip()
    if instance_id and instance_id not in cmd:
        return False, "instance id mismatch"

    return True, ""


def _cleanup_stale_ui_lock() -> bool:
    lock = _read_ui_lock()
    if not lock:
        return False
    pid = _get_lock_pid(lock)
    if pid > 0 and _is_pid_running(pid):
        return False
    _remove_ui_lock()
    return True


def _read_ui_health(timeout_seconds: float = 2.0) -> dict[str, object] | None:
    try:
        url = f"http://{SEARCH_UI_HOST}:{SEARCH_UI_PORT}/api/health"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            if resp.status != 200:
                return None
            payload = json.loads(resp.read().decode("utf-8"))
            if isinstance(payload, dict):
                return payload
    except Exception:
        return None
    return None


def _is_ui_server_responsive(expected_instance_id: str = "") -> bool:
    """Return True if the search UI server is healthy and is our UI service."""
    payload = _read_ui_health(timeout_seconds=2.0)
    if not payload:
        return False
    if payload.get("service") != _SEARCH_UI_SERVICE_NAME:
        return False
    if not bool(payload.get("ok", False)):
        return False
    expected = str(expected_instance_id or "").strip()
    if expected and str(payload.get("instance_id", "")).strip() != expected:
        return False
    return True


def _parse_id_list(raw_ids: str) -> list[str]:
    """Parse a comma-separated (["v-1","v-2"]) or JSON-array string (["v-1","v-2"]) into a list of strings."""
    raw = (raw_ids or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            return [str(i) for i in json.loads(raw) if i]
        except (json.JSONDecodeError, TypeError):
            pass
    return [s.strip() for s in raw.split(",") if s.strip()]


def _load_sample_docs(
    limit: int,
    sample_doc_json: str = "",
    source_local_file: str = "",
    source_index_name: str = "",
) -> list[dict[str, object]]:
    docs, _ = _load_sample_docs_with_note(
        limit=limit,
        sample_doc_json=sample_doc_json,
        source_local_file=source_local_file,
        source_index_name=source_index_name,
    )
    return docs


def _fetch_docs_from_index_via_client(
    source_index_name: str,
    limit: int,
) -> tuple[list[dict[str, object]], str]:
    effective_limit = max(1, min(limit, 200))
    target_source_index = str(source_index_name or "").strip().strip("'").strip('"')
    if not target_source_index:
        return [], ""

    try:
        opensearch_client = _create_client()
    except Exception as e:
        return [], f"failed to connect while loading source index '{target_source_index}': {e}"

    try:
        exists = opensearch_client.indices.exists(index=target_source_index)
        if not bool(exists):
            return [], f"source index '{target_source_index}' does not exist."
    except Exception as e:
        return [], f"failed to validate source index '{target_source_index}': {e}"

    try:
        response = opensearch_client.search(
            index=target_source_index,
            body={
                "size": effective_limit,
                "query": {"match_all": {}},
                "sort": [{"_doc": "asc"}],
                "track_total_hits": False,
            },
        )
    except Exception as e:
        return [], f"failed to fetch source sample documents from '{target_source_index}': {e}"

    hits = (
        response.get("hits", {}).get("hits", [])
        if isinstance(response, dict)
        else []
    )
    if not hits:
        return [], f"source index '{target_source_index}' returned 0 hits for match_all."

    docs: list[dict[str, object]] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        source = hit.get("_source")
        if isinstance(source, dict):
            docs.append(source)
        elif isinstance(hit.get("fields"), dict):
            docs.append(hit["fields"])
        elif source is not None:
            docs.append({"content": str(source)})
    if not docs:
        return [], f"source index '{target_source_index}' has no readable _source/fields payload."
    return docs[:effective_limit], ""


def _load_sample_docs_with_note(
    limit: int,
    sample_doc_json: str = "",
    source_local_file: str = "",
    source_index_name: str = "",
) -> tuple[list[dict[str, object]], str]:
    """Load sample docs with backward-compatible source_index_name support and diagnostics."""
    diagnostics: list[str] = []

    try:
        docs = get_sample_docs_payload(
            limit=limit,
            sample_doc_json=sample_doc_json,
            source_local_file=source_local_file,
            source_index_name=source_index_name,
        )
    except TypeError as e:
        # Some tests monkeypatch get_sample_docs_payload with an older 3-arg signature.
        if "source_index_name" not in str(e):
            raise
        docs = get_sample_docs_payload(limit, sample_doc_json, source_local_file)

    parsed_docs = [doc for doc in docs if isinstance(doc, dict)]
    if parsed_docs:
        return parsed_docs, ""

    normalized_source_index = (
        str(source_index_name or "").strip().strip("'").strip('"')
    )
    if normalized_source_index:
        fallback_docs, fallback_note = _fetch_docs_from_index_via_client(
            source_index_name=normalized_source_index,
            limit=limit,
        )
        if fallback_docs:
            return fallback_docs, ""
        if fallback_note:
            diagnostics.append(fallback_note)

    if not diagnostics:
        return [], ""
    return [], "; ".join(diagnostics)


def _resolve_http_auth_from_env() -> tuple[str, str] | None:
    mode = str(os.getenv(OPENSEARCH_AUTH_MODE_ENV, OPENSEARCH_AUTH_MODE_DEFAULT) or "").strip().lower()
    if mode == OPENSEARCH_AUTH_MODE_NONE:
        return None
    if mode == OPENSEARCH_AUTH_MODE_CUSTOM:
        user = str(os.getenv(OPENSEARCH_USER_ENV, "") or "").strip()
        password = str(os.getenv(OPENSEARCH_PASSWORD_ENV, "") or "").strip()
        if not user or not password:
            raise RuntimeError(
                "OPENSEARCH_AUTH_MODE=custom requires OPENSEARCH_USER and OPENSEARCH_PASSWORD."
            )
        return user, password
    return OPENSEARCH_DEFAULT_USER, OPENSEARCH_DEFAULT_PASSWORD


def _build_client(use_ssl: bool, http_auth: tuple[str, str] | None = None) -> OpenSearch:
    kwargs = {
        "hosts": [{"host": OPENSEARCH_HOST, "port": OPENSEARCH_PORT}],
        "use_ssl": use_ssl,
        "verify_certs": False,
        "ssl_show_warn": False,
    }
    if http_auth is not None:
        kwargs["http_auth"] = http_auth
    return OpenSearch(**kwargs)


def _can_connect(opensearch_client: OpenSearch) -> tuple[bool, bool]:
    try:
        opensearch_client.info()
        return True, False
    except Exception as e:
        lowered = normalize_text(e).lower()
        # AOSS (OpenSearch Serverless) returns 404 on GET / but is reachable.
        # Fall back to cat.indices as a connectivity check.
        if "404" in lowered or "notfounderror" in lowered:
            try:
                opensearch_client.cat.indices(format="json")
                return True, False
            except Exception:
                pass
        auth_failure = any(token in lowered for token in _AUTH_FAILURE_TOKENS)
        return False, auth_failure


def _is_local_host(host: str) -> bool:
    return host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def preflight_check_cluster(
    auth_mode: str = "",
    username: str = "",
    password: str = "",
) -> dict[str, object]:
    """Probe the configured OpenSearch host:port before execution.

    On the first call (no auth args), probes with no-auth and default creds.
    If the result is ``auth_required``, the caller should ask the user for
    credentials and call again with ``auth_mode="custom"``, ``username``,
    and ``password``.  When custom creds succeed the function sets
    ``OPENSEARCH_AUTH_MODE``, ``OPENSEARCH_USER``, and ``OPENSEARCH_PASSWORD``
    as in-process environment variables so every subsequent tool call
    (``create_index``, ``execute_plan``, etc.) picks them up automatically
    via ``_resolve_http_auth_from_env()``.

    Args:
        auth_mode: ``""`` (auto-detect), ``"none"``, or ``"custom"``.
        username: Required when auth_mode is ``"custom"``.
        password: Required when auth_mode is ``"custom"``.

    Returns:
        dict with status, host, port, is_local, message, auth_modes_tried,
        and auth_mode (when status is "available").
    """
    host = OPENSEARCH_HOST
    port = OPENSEARCH_PORT
    is_local = _is_local_host(host)
    result: dict[str, object] = {"host": host, "port": port, "is_local": is_local}

    normalized_mode = str(auth_mode or "").strip().lower()

    # --- Caller-supplied custom credentials ---
    if normalized_mode == "custom":
        user = str(username or "").strip()
        pwd = str(password or "").strip()
        if not user or not pwd:
            result["status"] = "error"
            result["message"] = (
                "auth_mode='custom' requires both username and password."
            )
            result["auth_modes_tried"] = []
            return result

        custom_auth = (user, pwd)
        for use_ssl in (True, False):
            client = _build_client(use_ssl=use_ssl, http_auth=custom_auth)
            ok, _ = _can_connect(client)
            if ok:
                # Persist creds in-process for all subsequent tool calls.
                os.environ[OPENSEARCH_AUTH_MODE_ENV] = OPENSEARCH_AUTH_MODE_CUSTOM
                os.environ[OPENSEARCH_USER_ENV] = user
                os.environ[OPENSEARCH_PASSWORD_ENV] = pwd
                ssl_label = "SSL" if use_ssl else "HTTP"
                result["status"] = "available"
                result["auth_mode"] = "custom"
                result["message"] = (
                    f"Connected to OpenSearch at {host}:{port} ({ssl_label}) "
                    f"with provided credentials. Credentials set for this session."
                )
                result["auth_modes_tried"] = [
                    f"custom_{'ssl' if use_ssl else 'http'}"
                ]
                return result

        result["status"] = "auth_required"
        result["message"] = (
            f"OpenSearch cluster at {host}:{port} rejected the provided "
            f"credentials. Please verify username/password and try again."
        )
        result["auth_modes_tried"] = ["custom_ssl", "custom_http"]
        return result

    # --- Caller-supplied no-auth mode ---
    if normalized_mode == "none":
        for use_ssl in (False, True):
            client = _build_client(use_ssl=use_ssl, http_auth=None)
            ok, _ = _can_connect(client)
            if ok:
                os.environ[OPENSEARCH_AUTH_MODE_ENV] = OPENSEARCH_AUTH_MODE_NONE
                os.environ.pop(OPENSEARCH_USER_ENV, None)
                os.environ.pop(OPENSEARCH_PASSWORD_ENV, None)
                ssl_label = "SSL" if use_ssl else "HTTP"
                result["status"] = "available"
                result["auth_mode"] = "none"
                result["message"] = (
                    f"Connected to OpenSearch at {host}:{port} ({ssl_label}) "
                    f"with no authentication. Auth mode set for this session."
                )
                result["auth_modes_tried"] = [
                    f"none_{'ssl' if use_ssl else 'http'}"
                ]
                return result

        result["status"] = "auth_required"
        result["message"] = (
            f"Could not connect to OpenSearch at {host}:{port} without "
            f"authentication. The cluster may require credentials."
        )
        result["auth_modes_tried"] = ["none_http", "none_ssl"]
        return result

    # --- Auto-detect (no auth args supplied) ---
    auth_modes_tried: list[str] = []
    saw_auth_failure = False

    default_auth = (OPENSEARCH_DEFAULT_USER, OPENSEARCH_DEFAULT_PASSWORD)
    probes: list[tuple[bool, tuple[str, str] | None, str]] = [
        (False, None, "none_http"),
        (True, None, "none_ssl"),
        (True, default_auth, "default_ssl"),
        (False, default_auth, "default_http"),
    ]

    for use_ssl, http_auth, label in probes:
        auth_modes_tried.append(label)
        client = _build_client(use_ssl=use_ssl, http_auth=http_auth)
        ok, auth_fail = _can_connect(client)
        if ok:
            mode_name = "none" if http_auth is None else "default"
            ssl_label = "SSL" if use_ssl else "HTTP"
            if http_auth is None:
                detail = f"with security disabled (no auth, {ssl_label})"
                os.environ[OPENSEARCH_AUTH_MODE_ENV] = OPENSEARCH_AUTH_MODE_NONE
                os.environ.pop(OPENSEARCH_USER_ENV, None)
                os.environ.pop(OPENSEARCH_PASSWORD_ENV, None)
            else:
                detail = f"({ssl_label}) using default credentials"
                os.environ[OPENSEARCH_AUTH_MODE_ENV] = OPENSEARCH_AUTH_MODE_DEFAULT
                os.environ.pop(OPENSEARCH_USER_ENV, None)
                os.environ.pop(OPENSEARCH_PASSWORD_ENV, None)
            result["status"] = "available"
            result["auth_mode"] = mode_name
            result["message"] = (
                f"OpenSearch cluster detected at {host}:{port} {detail}."
            )
            result["auth_modes_tried"] = auth_modes_tried
            return result
        if auth_fail:
            saw_auth_failure = True

    result["auth_modes_tried"] = auth_modes_tried
    if saw_auth_failure:
        result["status"] = "auth_required"
        result["message"] = (
            f"OpenSearch cluster detected at {host}:{port} but authentication "
            f"failed with all attempted credentials. Please provide your "
            f"credentials or allow bootstrapping a new local cluster."
        )
    else:
        result["status"] = "no_cluster"
        result["message"] = (
            f"No OpenSearch cluster detected at {host}:{port}. "
            f"A local cluster can be bootstrapped via Docker."
        )
    return result


def clear_cluster_credentials() -> None:
    """Remove in-process cluster credentials set by ``preflight_check_cluster``.

    Call this at the end of a session (e.g. from ``cleanup()``) so credentials
    do not linger in the MCP server process environment.
    """
    os.environ.pop(OPENSEARCH_AUTH_MODE_ENV, None)
    os.environ.pop(OPENSEARCH_USER_ENV, None)
    os.environ.pop(OPENSEARCH_PASSWORD_ENV, None)


def _get_backend_info() -> dict[str, object]:
    """Return connection metadata describing whether the backend is local or cloud."""
    override_host = _search_ui.endpoint_override_host
    if override_host:
        host = override_host
        port = _search_ui.endpoint_override_port or 443
    else:
        host = OPENSEARCH_HOST
        port = OPENSEARCH_PORT
    is_local = _is_local_host(host)
    backend_type = "local" if is_local else "cloud"
    endpoint_label = f"{host}:{port}" if is_local else host

    connected = False
    try:
        # Probe connectivity without triggering Docker bootstrap.
        # _create_client() falls through to _start_local_opensearch_container()
        # when direct connections fail, which would hang or start an unwanted
        # container during a lightweight health-check.  Instead, replicate the
        # same SSL/non-SSL probe logic used by _create_client() but stop after
        # the connection attempts.  A short timeout prevents the probe from
        # blocking the UI health endpoint (which itself has a 2 s budget).
        if override_host:
            client = _create_client()
            ok, _ = _can_connect(client)
            connected = ok
        else:
            http_auth = _resolve_http_auth_from_env()
            for use_ssl in (True, False):
                probe = _build_client(use_ssl=use_ssl, http_auth=http_auth)
                ok, _ = _can_connect(probe)
                if ok:
                    connected = True
                    break
    except Exception:
        pass

    return {
        "backend_type": backend_type,
        "endpoint": endpoint_label,
        "host": host,
        "port": port,
        "connected": connected,
    }


def _docker_cli_candidate_paths() -> list[str]:
    system_name = platform.system().lower()
    if system_name == "darwin":
        return [
            "/usr/local/bin/docker",
            "/opt/homebrew/bin/docker",
            "/Applications/Docker.app/Contents/Resources/bin/docker",
        ]
    if system_name == "linux":
        return [
            "/usr/bin/docker",
            "/usr/local/bin/docker",
            "/snap/bin/docker",
        ]
    if system_name == "windows":
        program_files = os.getenv("ProgramFiles", r"C:\Program Files")
        program_files_x86 = os.getenv("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local_app_data = os.getenv("LOCALAPPDATA", "")
        candidates = [
            os.path.join(program_files, "Docker", "Docker", "resources", "bin", "docker.exe"),
            os.path.join(program_files_x86, "Docker", "Docker", "resources", "bin", "docker.exe"),
        ]
        if local_app_data:
            candidates.append(
                os.path.join(
                    local_app_data,
                    "Programs",
                    "Docker",
                    "Docker",
                    "resources",
                    "bin",
                    "docker.exe",
                )
            )
        return candidates
    return []


def _resolve_docker_executable() -> str:
    configured_path = str(os.getenv(OPENSEARCH_DOCKER_CLI_PATH_ENV, "") or "").strip()
    system_name = platform.system().lower()
    candidates: list[str] = []
    if configured_path:
        candidates.append(configured_path)
    discovered = shutil.which("docker")
    if discovered:
        candidates.append(discovered)
    if system_name == "windows":
        discovered_exe = shutil.which("docker.exe")
        if discovered_exe:
            candidates.append(discovered_exe)
    candidates.extend(_docker_cli_candidate_paths())

    seen: set[str] = set()
    for candidate in candidates:
        candidate_path = str(candidate or "").strip()
        if not candidate_path:
            continue
        normalized = os.path.expandvars(os.path.expanduser(candidate_path))
        dedupe_key = normalized.lower() if os.name == "nt" else normalized
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        is_windows_abs = bool(re.match(r"^[A-Za-z]:[\\/]", normalized))
        if os.path.isabs(normalized) or (system_name == "windows" and is_windows_abs):
            if os.path.isfile(normalized):
                return normalized
            continue

        resolved = shutil.which(normalized)
        if resolved:
            return resolved

    raise FileNotFoundError("Docker CLI executable was not found.")


def _run_docker_command(command: list[str]) -> subprocess.CompletedProcess:
    if not command:
        raise ValueError("Docker command must not be empty.")
    normalized_command = list(command)
    if normalized_command[0].lower() in {"docker", "docker.exe"}:
        normalized_command[0] = _resolve_docker_executable()
    return subprocess.run(
        normalized_command,
        check=True,
        capture_output=True,
        text=True,
    )


def _docker_install_hint() -> str:
    override_hint = (
        f"If Docker CLI is installed in a non-standard location, set "
        f"{OPENSEARCH_DOCKER_CLI_PATH_ENV} to its full executable path."
    )
    system_name = platform.system().lower()
    if system_name == "darwin":
        if shutil.which("brew"):
            return (
                "Install Docker Desktop with Homebrew: "
                "'brew install --cask docker && open -a Docker'. "
                "Official docs: https://docs.docker.com/desktop/setup/install/mac-install/ "
                f"{override_hint}"
            )
        return (
            "Install Docker Desktop for macOS: "
            "https://docs.docker.com/desktop/setup/install/mac-install/ "
            f"{override_hint}"
        )

    if system_name == "windows":
        return (
            "Install Docker Desktop for Windows: "
            "https://docs.docker.com/desktop/setup/install/windows-install/ "
            f"{override_hint}"
        )

    if system_name == "linux":
        return (
            "Install Docker Engine for Linux: "
            "https://docs.docker.com/engine/install/ "
            f"{override_hint}"
        )

    return (
        "Install Docker: https://docs.docker.com/get-started/get-docker/ "
        f"{override_hint}"
    )


def _docker_start_hint() -> str:
    system_name = platform.system().lower()
    if system_name in {"darwin", "windows"}:
        return "Start Docker Desktop and wait until it reports it is running."
    if system_name == "linux":
        return (
            "Start Docker service (for example: 'sudo systemctl start docker')."
        )
    return "Start the Docker daemon/service and retry."


def _looks_like_model_memory_pressure(error: object) -> bool:
    lowered = normalize_text(error).lower()
    return any(token in lowered for token in _MODEL_MEMORY_SIGNAL_TOKENS)


def _looks_like_local_model_limit(error: object) -> bool:
    lowered = normalize_text(error).lower()
    return any(token in lowered for token in _MODEL_LOCAL_LIMIT_SIGNAL_TOKENS)


def _wait_for_ml_task(
    opensearch_client: OpenSearch,
    task_id: str,
    *,
    max_polls: int = 100,
    poll_interval_seconds: int = 3,
) -> tuple[str, dict]:
    if not task_id:
        return "FAILED", {"error": "Missing task_id from ML task response."}

    for _ in range(max_polls):
        task_res = opensearch_client.transport.perform_request(
            "GET",
            f"/_plugins/_ml/tasks/{task_id}",
        )
        state = normalize_text(task_res.get("state", "")).upper()
        if state in {"COMPLETED", "FAILED"}:
            return state, task_res
        time.sleep(poll_interval_seconds)

    return "TIMEOUT", {}


def _list_model_ids_for_undeploy_recovery(
    opensearch_client: OpenSearch,
    *,
    exclude_model_id: str = "",
    max_models: int = 20,
) -> list[str]:
    size = max(1, max_models * 3)
    try:
        response = opensearch_client.transport.perform_request(
            "POST",
            "/_plugins/_ml/models/_search",
            body={
                "size": size,
                "query": {"match_all": {}},
            },
        )
    except Exception as e:
        print(
            f"Failed to list models for undeploy recovery: {e}",
            file=sys.stderr,
        )
        return []

    hits = response.get("hits", {}).get("hits", [])
    deployed_ids: list[str] = []
    discovered_ids: list[str] = []
    seen_ids: set[str] = set()

    for hit in hits:
        if not isinstance(hit, dict):
            continue
        candidate_id = normalize_text(hit.get("_id") or hit.get("model_id") or hit.get("id"))
        if not candidate_id or candidate_id == exclude_model_id or candidate_id in seen_ids:
            continue
        seen_ids.add(candidate_id)
        discovered_ids.append(candidate_id)

        source = hit.get("_source", {})
        if not isinstance(source, dict):
            source = {}
        state_tokens = [
            normalize_text(source.get("model_state")).lower(),
            normalize_text(source.get("deploy_state")).lower(),
            normalize_text(hit.get("model_state")).lower(),
            normalize_text(hit.get("deploy_state")).lower(),
        ]
        if any("deployed" in token for token in state_tokens if token):
            deployed_ids.append(candidate_id)

    ordered_ids = deployed_ids or discovered_ids
    return ordered_ids[:max_models]


def _undeploy_model_and_wait(
    opensearch_client: OpenSearch,
    model_id: str,
    *,
    max_polls: int = 80,
    poll_interval_seconds: int = 2,
) -> tuple[bool, str]:
    try:
        response = opensearch_client.transport.perform_request(
            "POST",
            f"/_plugins/_ml/models/{model_id}/_undeploy",
        )
    except Exception as e:
        return False, f"Undeploy request failed: {e}"

    task_id = normalize_text(response.get("task_id"))
    if not task_id:
        # Some distributions may return acknowledgement without an async task id.
        return True, "Undeploy acknowledged without task id."

    state, task_res = _wait_for_ml_task(
        opensearch_client,
        task_id,
        max_polls=max_polls,
        poll_interval_seconds=poll_interval_seconds,
    )
    if state == "COMPLETED":
        return True, "Undeploy completed."
    if state == "FAILED":
        return False, f"Undeploy failed: {task_res.get('error')}"
    return False, "Undeploy timed out."


def _resolve_initial_admin_password_for_docker_bootstrap() -> str:
    configured_password = str(os.getenv(OPENSEARCH_PASSWORD_ENV, "") or "").strip()
    if configured_password:
        return configured_password
    return OPENSEARCH_DEFAULT_PASSWORD


def _format_model_failure_message(stage: str, error: object) -> str:
    message = f"Model {stage} failed: {error}"
    if _looks_like_model_memory_pressure(error):
        return (
            f"{message} OpenSearch appears memory constrained. "
            "Please reconnect Docker (restart Docker Desktop/service) and retry."
        )
    return message


def _run_new_local_opensearch_container() -> None:
    """Pull and run a new local OpenSearch container."""
    initial_admin_password = _resolve_initial_admin_password_for_docker_bootstrap()
    _run_docker_command(["docker", "pull", OPENSEARCH_DOCKER_IMAGE])
    _run_docker_command(
        [
            "docker",
            "run",
            "-d",
            "--name",
            OPENSEARCH_DOCKER_CONTAINER,
            "-p",
            f"{OPENSEARCH_PORT}:9200",
            "-p",
            "9600:9600",
            "-e",
            "discovery.type=single-node",
            "-e",
            "plugins.security.disabled=false",
            "-e",
            "DISABLE_INSTALL_DEMO_CONFIG=false",
            "-e",
            f"OPENSEARCH_INITIAL_ADMIN_PASSWORD={initial_admin_password}",
            "-e",
            "OPENSEARCH_JAVA_OPTS=-Xms4g -Xmx4g",
            OPENSEARCH_DOCKER_IMAGE,
        ]
    )


def _start_local_opensearch_container() -> None:
    if not _is_local_host(OPENSEARCH_HOST):
        raise RuntimeError(
            f"Auto-start only supports local hosts. Current OPENSEARCH_HOST='{OPENSEARCH_HOST}'."
        )

    try:
        _run_docker_command(["docker", "--version"])
    except Exception as e:
        raise RuntimeError(
            "Docker is not installed or its CLI executable could not be discovered. "
            f"{_docker_install_hint()}"
        ) from e

    try:
        running = _run_docker_command(
            ["docker", "ps", "-q", "-f", f"name=^{OPENSEARCH_DOCKER_CONTAINER}$"]
        ).stdout.strip()
    except Exception as e:
        raise RuntimeError(
            "Docker CLI is available, but Docker daemon is not reachable. "
            f"{_docker_start_hint()}"
        ) from e
    if running:
        return

    existing = _run_docker_command(
        ["docker", "ps", "-aq", "-f", f"name=^{OPENSEARCH_DOCKER_CONTAINER}$"]
    ).stdout.strip()
    if existing:
        _run_docker_command(["docker", "start", OPENSEARCH_DOCKER_CONTAINER])
        return

    _run_new_local_opensearch_container()


def recover_local_opensearch_container() -> tuple[bool, str]:
    """Recover local OpenSearch runtime state for exception-driven retry flows.

    Returns:
        tuple[bool, str]:
            - bool: True when the cluster is reachable after recovery checks/actions.
            - str: Actionable diagnostics describing the action taken or failure reason.
    """
    if not _is_local_host(OPENSEARCH_HOST):
        return (
            False,
            f"Skip recovery: OPENSEARCH_HOST '{OPENSEARCH_HOST}' is not local.",
        )

    try:
        _run_docker_command(["docker", "--version"])
    except Exception:
        return (
            False,
            "Recovery failed: Docker is not installed or its CLI executable could not be discovered. "
            f"{_docker_install_hint()}",
        )

    try:
        running = _run_docker_command(
            ["docker", "ps", "-q", "-f", f"name=^{OPENSEARCH_DOCKER_CONTAINER}$"]
        ).stdout.strip()
        existing = _run_docker_command(
            ["docker", "ps", "-aq", "-f", f"name=^{OPENSEARCH_DOCKER_CONTAINER}$"]
        ).stdout.strip()
    except Exception:
        return (
            False,
            "Recovery failed: Docker daemon is not reachable. "
            f"{_docker_start_hint()}",
        )

    action = "verified existing running container"
    try:
        if running:
            action = "verified existing running container"
        elif existing:
            _run_docker_command(["docker", "start", OPENSEARCH_DOCKER_CONTAINER])
            action = "started existing stopped container"
        else:
            _run_new_local_opensearch_container()
            action = "created and started new container"

        _wait_for_cluster_after_start()
        return (
            True,
            f"Recovery succeeded: {action} '{OPENSEARCH_DOCKER_CONTAINER}' and cluster is reachable.",
        )
    except Exception as e:
        return (
            False,
            f"Recovery failed after '{action}': {e}",
        )


def _wait_for_cluster_after_start() -> OpenSearch:
    http_auth = _resolve_http_auth_from_env()
    secure_client = _build_client(use_ssl=True, http_auth=http_auth)
    insecure_client = _build_client(use_ssl=False, http_auth=http_auth)
    deadline = time.time() + OPENSEARCH_DOCKER_START_TIMEOUT

    while time.time() < deadline:
        secure_ok, _ = _can_connect(secure_client)
        if secure_ok:
            return secure_client
        insecure_ok, _ = _can_connect(insecure_client)
        if insecure_ok:
            return insecure_client
        time.sleep(2)

    raise RuntimeError(
        f"OpenSearch container did not become ready within {OPENSEARCH_DOCKER_START_TIMEOUT}s."
    )


def _create_client() -> OpenSearch:
    # If a runtime endpoint override is active, connect directly to it.
    override_host = _search_ui.endpoint_override_host
    if override_host:
        override_port = _search_ui.endpoint_override_port or 443
        override_ssl = _search_ui.endpoint_override_use_ssl
        override_auth = _search_ui.endpoint_override_auth
        aws_region = _search_ui.endpoint_override_aws_region
        aws_service = _search_ui.endpoint_override_aws_service

        kwargs: dict[str, object] = {
            "hosts": [{"host": override_host, "port": override_port}],
            "use_ssl": override_ssl,
            "verify_certs": override_ssl,
            "ssl_show_warn": False,
        }

        # Use SigV4 auth for AWS endpoints (AOSS or managed domains).
        if aws_region and aws_service:
            try:
                import boto3
                from opensearchpy import AWSV4SignerAuth, RequestsHttpConnection

                session = boto3.Session()
                credentials = session.get_credentials()
                auth = AWSV4SignerAuth(credentials, aws_region, aws_service)
                kwargs["http_auth"] = auth
                kwargs["connection_class"] = RequestsHttpConnection
            except Exception as e:
                raise RuntimeError(
                    f"Failed to configure AWS SigV4 auth for {override_host}: {e}"
                ) from e
        elif override_auth is not None:
            kwargs["http_auth"] = override_auth

        client = OpenSearch(**kwargs)
        ok, auth_fail = _can_connect(client)
        if ok:
            return client
        raise RuntimeError(
            f"Cannot connect to overridden endpoint {override_host}:{override_port}"
            + (" (authentication failure)" if auth_fail else "")
        )

    http_auth = _resolve_http_auth_from_env()

    secure_client = _build_client(use_ssl=True, http_auth=http_auth)
    secure_ok, secure_auth_failure = _can_connect(secure_client)
    if secure_ok:
        return secure_client

    insecure_client = _build_client(use_ssl=False, http_auth=http_auth)
    insecure_ok, insecure_auth_failure = _can_connect(insecure_client)
    if insecure_ok:
        return insecure_client

    if secure_auth_failure or insecure_auth_failure:
        raise ClusterAuthError(
            host=OPENSEARCH_HOST,
            port=OPENSEARCH_PORT,
        )

    # Direct connection attempts failed without auth errors, so bootstrap local OpenSearch with Docker.
    _start_local_opensearch_container()
    return _wait_for_cluster_after_start()


def _normalize_text(value: object) -> str:
    """Normalize any value into a compact single-line string with collapsed whitespace.

    Delegates to the shared ``normalize_text`` utility.
    """
    return normalize_text(value)


def _normalized_query_key(value: object) -> str:
    return _normalize_text(value).lower()


def _canonical_capability_id(label: str) -> str:
    lowered = label.lower()
    if "exact" in lowered:
        return "exact"
    if "semantic" in lowered:
        return "semantic"
    if "structured" in lowered or "filter" in lowered:
        return "structured"
    if "combined" in lowered:
        return "combined"
    if "autocomplete" in lowered or "prefix" in lowered:
        return "autocomplete"
    if "fuzzy" in lowered or "typo" in lowered:
        return "fuzzy"
    return ""


def _extract_search_capabilities(worker_output: str) -> list[dict[str, object]]:
    """Parse the worker's markdown output and extract the "Search Capabilities" section.

    The worker output is expected to contain a bullet list under a heading that
    includes the phrase "Search Capabilities". Each capability bullet must use
    a canonical prefix (case-insensitive): Exact:, Semantic:, Structured:,
    Combined:, Autocomplete:, or Fuzzy:.

    Args:
        worker_output: Raw markdown text produced by the worker.

    Returns:
        A list of capability dicts, each with the following keys:

        - ``"id"`` (str): Canonical capability identifier derived from the
          bullet text (e.g. ``"exact"``, ``"semantic"``, ``"structured"``,
          ``"combined"``, ``"autocomplete"``, ``"fuzzy"``).
        - ``"label"`` (str): The normalized human-readable text of the bullet
          point (e.g. ``"exact match toyota camry honda civic"``).
        - ``"examples"`` (list[str]): Capability examples populated from
          selected sample documents later in the verification flow.

    Example::

        # If the worker output contains:
        #   ## Search Capabilities
        #   - Exact match: "Toyota Camry", "Honda Civic"
        #   - Semantic search: "fuel efficient family car"
        #
        # The return value would be:
        [
            {
                "id": "exact",
                "label": "exact match toyota camry honda civic",
                "examples": [],
            },
            {
                "id": "semantic",
                "label": "semantic search fuel efficient family car",
                "examples": [],
            },
        ]
    """
    if not worker_output:
        return []

    capabilities: list[dict[str, object]] = []
    seen: set[str] = set()
    in_section = False

    for raw_line in worker_output.splitlines():
        line = raw_line.strip()
        lowered = line.lower()

        if not in_section and "search capabilities" in lowered:
            in_section = True
            continue

        if not in_section:
            continue

        if not line:
            if capabilities:
                break
            continue

        if line.startswith("##") or line.startswith("---"):
            break

        if not (line.startswith("-") or line.startswith("*")):
            if capabilities:
                break
            continue

        bullet = re.sub(r"^[-*]\s*", "", line)
        bullet = re.sub(r"^[^\w]+", "", bullet)
        prefix_match = re.match(
            r"^(exact|semantic|structured|combined|autocomplete|fuzzy)\s*:",
            bullet,
            re.IGNORECASE,
        )
        if not prefix_match:
            continue

        capability_id = _canonical_capability_id(prefix_match.group(1))
        if not capability_id or capability_id in seen:
            continue

        capabilities.append(
            {
                "id": capability_id,
                "label": _normalize_text(bullet),
                "examples": [],
            }
        )
        seen.add(capability_id)

    return capabilities


def _extract_index_field_specs(opensearch_client: OpenSearch, index_name: str) -> dict[str, dict[str, str]]:
    field_specs: dict[str, dict[str, str]] = {}
    try:
        mapping_response = opensearch_client.indices.get_mapping(index=index_name)
    except Exception:
        return field_specs

    index_mapping = {}
    if isinstance(mapping_response, dict):
        index_mapping = next(iter(mapping_response.values()), {})
    mappings = index_mapping.get("mappings", {})

    def _walk(properties: dict, prefix: str = "") -> None:
        if not isinstance(properties, dict):
            return
        for field_name, config in properties.items():
            if not isinstance(config, dict):
                continue
            full_name = f"{prefix}.{field_name}" if prefix else field_name

            field_type = config.get("type")
            if isinstance(field_type, str):
                field_specs[full_name] = {
                    "type": field_type,
                    "normalizer": str(config.get("normalizer", "")).strip(),
                }

            sub_fields = config.get("fields")
            if isinstance(sub_fields, dict):
                for sub_name, sub_config in sub_fields.items():
                    if not isinstance(sub_config, dict):
                        continue
                    sub_type = sub_config.get("type")
                    if not isinstance(sub_type, str):
                        continue
                    field_specs[f"{full_name}.{sub_name}"] = {
                        "type": sub_type,
                        "normalizer": str(sub_config.get("normalizer", "")).strip(),
                    }

            nested_props = config.get("properties")
            if isinstance(nested_props, dict):
                _walk(nested_props, full_name)

    _walk(mappings.get("properties", {}))
    return field_specs


def _extract_declared_field_types_from_index_body(body: dict) -> dict[str, str]:
    declared_field_types: dict[str, str] = {}
    if not isinstance(body, dict):
        return declared_field_types

    mappings = body.get("mappings")
    if not isinstance(mappings, dict):
        return declared_field_types

    properties = mappings.get("properties")
    if not isinstance(properties, dict):
        return declared_field_types

    def _walk(props: dict, prefix: str = "") -> None:
        if not isinstance(props, dict):
            return
        for raw_name, config in props.items():
            if not isinstance(raw_name, str):
                continue
            name = raw_name.strip()
            if not name or not isinstance(config, dict):
                continue

            full_name = f"{prefix}.{name}" if prefix else name
            field_type = str(config.get("type", "")).strip().lower()
            if field_type:
                declared_field_types[full_name] = field_type

            sub_fields = config.get("fields")
            if isinstance(sub_fields, dict):
                for raw_sub_name, sub_config in sub_fields.items():
                    if not isinstance(raw_sub_name, str) or not isinstance(sub_config, dict):
                        continue
                    sub_name = raw_sub_name.strip()
                    if not sub_name:
                        continue
                    sub_type = str(sub_config.get("type", "")).strip().lower()
                    if sub_type:
                        declared_field_types[f"{full_name}.{sub_name}"] = sub_type

            nested_properties = config.get("properties")
            if isinstance(nested_properties, dict):
                _walk(nested_properties, full_name)

    _walk(properties)
    return declared_field_types


def _normalize_knn_method_engines(index_body: dict) -> list[str]:
    """Normalize deprecated/implicit k-NN engines to stable defaults.

    This guardrail prevents LLM-generated index bodies from relying on deprecated
    ``nmslib`` or implicit engine defaults that vary by OpenSearch version.
    """
    if not isinstance(index_body, dict):
        return []

    mappings = index_body.get("mappings")
    if not isinstance(mappings, dict):
        return []

    properties = mappings.get("properties")
    if not isinstance(properties, dict):
        return []

    updates: list[str] = []

    def _preferred_engine(method_name: str, current_engine: str) -> str:
        method = method_name.strip().lower()
        engine = current_engine.strip().lower()

        if method == "ivf":
            return "faiss"
        if method == "hnsw":
            if not engine or engine == "nmslib":
                return "lucene"
            return engine
        if engine == "nmslib":
            return "faiss"
        return engine

    def _walk(props: dict, prefix: str = "") -> None:
        if not isinstance(props, dict):
            return
        for raw_name, config in props.items():
            if not isinstance(raw_name, str) or not isinstance(config, dict):
                continue

            name = raw_name.strip()
            if not name:
                continue

            full_name = f"{prefix}.{name}" if prefix else name
            field_type = str(config.get("type", "")).strip().lower()

            if field_type == "knn_vector":
                method = config.get("method")
                if isinstance(method, dict):
                    method_name = str(method.get("name", "")).strip().lower()
                    current_engine = str(method.get("engine", "")).strip().lower()
                    next_engine = _preferred_engine(method_name, current_engine)
                    if next_engine and next_engine != current_engine:
                        method["engine"] = next_engine
                        before = current_engine or "<empty>"
                        updates.append(
                            f"{full_name}: engine {before} -> {next_engine}"
                        )

            nested_properties = config.get("properties")
            if isinstance(nested_properties, dict):
                _walk(nested_properties, full_name)

    _walk(properties)
    return updates


def _collect_requested_vs_existing_field_type_mismatches(
    requested_field_types: dict[str, str],
    existing_field_types: dict[str, str],
) -> list[str]:
    mismatches: list[str] = []
    if not isinstance(requested_field_types, dict) or not requested_field_types:
        return mismatches
    if not isinstance(existing_field_types, dict):
        existing_field_types = {}

    normalized_existing = {
        str(field_name).strip(): str(field_type).strip().lower()
        for field_name, field_type in existing_field_types.items()
        if str(field_name).strip() and str(field_type).strip()
    }

    def _resolve_existing_type(requested_field_name: str) -> str:
        if requested_field_name in normalized_existing:
            return normalized_existing[requested_field_name]

        requested_lower = requested_field_name.lower()
        for candidate_name, candidate_type in normalized_existing.items():
            if candidate_name.lower() == requested_lower:
                return candidate_type

        requested_leaf = requested_field_name.split(".")[-1].lower()
        leaf_matches = [
            candidate_type
            for candidate_name, candidate_type in normalized_existing.items()
            if candidate_name.split(".")[-1].lower() == requested_leaf
        ]
        if len(leaf_matches) == 1:
            return leaf_matches[0]

        return ""

    def _types_compatible(requested_type: str, existing_type: str) -> bool:
        if requested_type == existing_type:
            return True

        compatibility_groups = [
            {"keyword", "constant_keyword"},
            {"text", "match_only_text"},
            {"byte", "short", "integer", "long"},
            {"half_float", "float", "double", "scaled_float"},
        ]
        for group in compatibility_groups:
            if requested_type in group and existing_type in group:
                return True
        return False

    for requested_field_name, requested_type in sorted(requested_field_types.items()):
        normalized_requested_field = str(requested_field_name).strip()
        normalized_requested_type = str(requested_type).strip().lower()
        if not normalized_requested_field or not normalized_requested_type:
            continue

        existing_type = _resolve_existing_type(normalized_requested_field)
        if not existing_type:
            mismatches.append(
                f"Field '{normalized_requested_field}' is missing in existing index (requested type '{normalized_requested_type}')."
            )
            continue

        if not _types_compatible(normalized_requested_type, existing_type):
            mismatches.append(
                f"Field '{normalized_requested_field}' requested type '{normalized_requested_type}' but existing type is '{existing_type}'."
            )

    return mismatches


def _collect_doc_values_for_field(
    sample_docs: list[dict[str, object]],
    field_name: str,
) -> list[object]:
    normalized_field = _normalize_text(field_name).lower()
    if not normalized_field:
        return []

    values: list[object] = []
    for doc in sample_docs:
        if not isinstance(doc, dict):
            continue
        for raw_key, raw_value in doc.items():
            if _normalize_text(raw_key).lower() != normalized_field:
                continue
            if raw_value is None:
                continue
            values.append(raw_value)
            break
    return values


def _classify_boolean_sample_value(value: object) -> str:
    if isinstance(value, bool):
        return "native_boolean"
    if isinstance(value, str):
        normalized = _normalize_text(value).lower()
        if normalized in _BOOLEAN_STRING_FLAG_VALUES:
            return "string_binary_flag"
    return ""


def _collect_boolean_typing_policy_violations(
    field_types: dict[str, str],
    sample_docs: list[dict[str, object]],
) -> list[str]:
    """Validate producer-driven boolean typing policy against sampled values."""
    if not field_types or not sample_docs:
        return []

    violations: list[str] = []
    for field_name, field_type in sorted(field_types.items()):
        normalized_type = _normalize_text(field_type).lower()
        if normalized_type != "boolean":
            continue

        observed_kinds: set[str] = set()
        for value in _collect_doc_values_for_field(sample_docs, field_name):
            kind = _classify_boolean_sample_value(value)
            if kind:
                observed_kinds.add(kind)

        if "string_binary_flag" not in observed_kinds:
            continue

        observed = ", ".join(sorted(observed_kinds))
        violations.append(
            f"Field '{field_name}' violates producer-driven boolean typing policy "
            f"(observed={observed}). Map this field as keyword when producer sends string flags (0/1/true/false)."
        )
    return violations


def _resolve_field_spec_for_doc_key(
    field_name: str, field_specs: dict[str, dict[str, str]]
) -> tuple[str, dict[str, str]]:
    if field_name in field_specs:
        return field_name, field_specs[field_name]

    lowered = field_name.lower()
    for candidate_name, candidate_spec in field_specs.items():
        if candidate_name.lower() == lowered:
            return candidate_name, candidate_spec

    for candidate_name, candidate_spec in field_specs.items():
        if candidate_name.split(".")[-1].lower() == lowered:
            return candidate_name, candidate_spec

    return "", {}


_INFERRED_NUMERIC_TYPES = {"long", "double"}


def _merge_inferred_field_types(existing_type: str, incoming_type: str) -> str:
    existing = _normalize_text(existing_type).lower()
    incoming = _normalize_text(incoming_type).lower()
    if not incoming:
        return existing
    if not existing:
        return incoming
    if existing == incoming:
        return existing
    if existing in _INFERRED_NUMERIC_TYPES and incoming in _INFERRED_NUMERIC_TYPES:
        return "double" if "double" in {existing, incoming} else "long"
    if "text" in {existing, incoming}:
        return "text"
    if "keyword" in {existing, incoming}:
        return "keyword"
    if existing == incoming == "date":
        return "date"
    if existing == incoming == "boolean":
        return "boolean"
    return "keyword"


def _infer_field_type_from_value(value: object, shape: dict[str, object] | None = None) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "long"
    if isinstance(value, float):
        return "double"

    normalized = _normalize_text(value)
    if not normalized:
        return ""

    value_shape_info = shape if isinstance(shape, dict) else _value_shape(normalized)
    if bool(value_shape_info.get("looks_numeric", False)):
        if re.fullmatch(r"[+-]?\d+", normalized):
            return "long"
        return "double"
    if bool(value_shape_info.get("looks_date", False)):
        return "date"

    token_count = int(value_shape_info.get("token_count", 0))
    alpha_ratio = float(value_shape_info.get("alpha_ratio", 0.0))
    if (
        token_count >= 2
        and alpha_ratio >= 0.35
        and 8 <= len(normalized) <= 220
        and not _looks_like_url_noise(normalized)
    ):
        return "text"
    return "keyword"


def _infer_field_specs_from_sample_docs(
    docs: list[dict[str, object]],
) -> dict[str, dict[str, str]]:
    field_specs: dict[str, dict[str, str]] = {}
    if not isinstance(docs, list):
        return field_specs

    for doc in docs:
        if not isinstance(doc, dict):
            continue
        for raw_key, raw_value in doc.items():
            key = _normalize_text(raw_key)
            if not key or raw_value is None or isinstance(raw_value, (dict, list)):
                continue
            shape = _value_shape(_normalize_text(raw_value))
            inferred_type = _infer_field_type_from_value(raw_value, shape)
            if not inferred_type:
                continue

            current_type = str(field_specs.get(key, {}).get("type", "")).strip().lower()
            merged_type = _merge_inferred_field_types(current_type, inferred_type)
            if not merged_type:
                continue
            field_specs[key] = {
                "type": merged_type,
                "normalizer": "",
            }

    # For inferred text fields, assume a keyword multi-field can be used for exact match demos.
    for field_name, spec in list(field_specs.items()):
        if spec.get("type") != "text":
            continue
        keyword_field = f"{field_name}.keyword"
        if keyword_field not in field_specs:
            field_specs[keyword_field] = {
                "type": "keyword",
                "normalizer": "",
            }

    return field_specs

def _value_shape(text: str) -> dict[str, object]:
    """Compute structural characteristics of a text value.

    Delegates to the shared ``value_shape`` utility.
    """
    return value_shape(text)


def _extract_doc_features(
    source: dict, field_specs: dict[str, dict[str, str]]
) -> dict[str, object]:
    """Analyse a single document and classify its fields into search-demo categories.

    Each scalar (non-null, non-nested) field in *source* is inspected against
    the index mapping (``field_specs``) and its value shape to determine which
    types of search queries it can demonstrate.

    Args:
        source: The raw document dict as returned from OpenSearch
            (e.g. ``{"title": "Toyota Camry", "price": 25000, ...}``).
        field_specs: Mapping of field path to its index mapping metadata,
            as returned by ``_extract_index_field_specs``.  Each value is a
            dict with at least ``"type"`` and optionally ``"normalizer"``.

    Returns:
        A features dict with the following keys:

        - ``"source"`` (dict): The original raw document, kept as-is.
        - ``"scalar_items"`` (list[dict]): All non-null, non-nested fields
          with their resolved mapping info and value shape.  Each entry has:

          - ``"key"`` (str): The original field key in the document.
          - ``"field"`` (str): The resolved mapping field path.
          - ``"type"`` (str): The mapping type (e.g. ``"text"``, ``"keyword"``,
            ``"integer"``).
          - ``"normalizer"`` (str): The normalizer name, if any.
          - ``"shape"`` (dict): Value shape returned by ``_value_shape``.

        - ``"exact_candidates"`` (list[dict]): Fields/values suitable for
          exact-match (``term``) or ``match_phrase`` queries.  Each entry has:

          - ``"text"`` (str): The normalised value text.
          - ``"query_mode"`` (str): ``"term"`` or ``"match_phrase"``.
          - ``"field"`` (str): The field to query against.
          - ``"case_insensitive"`` (bool): Whether a case-insensitive match
            is available (based on the normalizer).

        - ``"phrase_candidates"`` (list[dict]): Fields/values suitable for
          ``match_phrase`` queries (text-type fields).  Same keys as
          ``exact_candidates``.
        - ``"semantic_candidates"`` (list[dict]): Fields/values suitable for
          semantic / neural search (multi-token, mostly alphabetic text).
          Each entry has:

          - ``"text"`` (str): The normalised value text.
          - ``"field"`` (str): The field to query against.

        - ``"structured_candidates"`` (list[dict]): Fields/values suitable for
          structured queries (numeric, date, boolean, keyword).  Each entry has:

          - ``"field"`` (str): The field to query against.
          - ``"value"`` (str): The normalised value text.
          - ``"type"`` (str): The mapping type.

        - ``"anchor_tokens"`` (list[dict]): Individual tokens (>= 2 chars,
          non-digit) extracted only from ``text``/``keyword`` scalar fields,
          with source field info.
          Each entry has:

          - ``"token"`` (str): The token text.
          - ``"field"`` (str): The source field where the token came from.

    Example::

        # For a document:
        #   {"title": "Toyota Camry", "price": 25000,
        #    "description": "A reliable family sedan"}
        #
        # A possible return value:
        {
            "source": {"title": "Toyota Camry", "price": 25000,
                        "description": "A reliable family sedan"},
            "scalar_items": [
                {"key": "title", "field": "title", "type": "text",
                 "normalizer": "",
                 "shape": {"text": "toyota camry", "token_count": 2,
                           "alpha_ratio": 0.92, ...}},
                {"key": "price", "field": "price", "type": "integer",
                 "normalizer": "",
                 "shape": {"text": "25000", "token_count": 1,
                           "looks_numeric": True, ...}},
                {"key": "description", "field": "description", "type": "text",
                 "normalizer": "",
                 "shape": {"text": "a reliable family sedan",
                           "token_count": 4, "alpha_ratio": 0.95, ...}},
            ],
            "exact_candidates": [
                {"text": "toyota camry", "query_mode": "term",
                 "field": "title.keyword", "case_insensitive": False},
            ],
            "phrase_candidates": [
                {"text": "toyota camry", "query_mode": "match_phrase",
                 "field": "title", "case_insensitive": False},
                {"text": "a reliable family sedan",
                 "query_mode": "match_phrase", "field": "description",
                 "case_insensitive": False},
            ],
            "semantic_candidates": [
                {"text": "a reliable family sedan",
                 "field": "description"},
            ],
            "structured_candidates": [
                {"field": "price", "value": "25000", "type": "integer"},
            ],
            "anchor_tokens": [
                {"token": "toyota", "field": "title"},
                {"token": "camry", "field": "title"},
                {"token": "reliable", "field": "description"},
                {"token": "family", "field": "description"},
                {"token": "sedan", "field": "description"},
            ],
        }
    """
    scalar_items: list[dict[str, object]] = []
    for key, value in source.items():
        if value is None or isinstance(value, (dict, list)):
            continue
        compact = _normalize_text(value)
        if not compact:
            continue

        resolved_field, resolved_spec = _resolve_field_spec_for_doc_key(str(key), field_specs)
        shape = _value_shape(compact)
        resolved_type = str(resolved_spec.get("type", "")).strip().lower()
        if not resolved_type:
            resolved_type = _infer_field_type_from_value(value, shape)
        scalar_items.append(
            {
                "key": str(key),
                "field": resolved_field,
                "type": resolved_type,
                "normalizer": resolved_spec.get("normalizer", ""),
                "shape": shape,
            }
        )

    exact_candidates: list[dict[str, object]] = []
    phrase_candidates: list[dict[str, object]] = []
    semantic_candidates: list[dict[str, object]] = []
    structured_candidates: list[dict[str, object]] = []
    anchor_tokens: list[dict[str, str]] = []
    keyword_types = {"keyword", "constant_keyword"}
    structured_types = {"byte", "short", "integer", "long", "float", "half_float", "double", "scaled_float", "date", "boolean", "keyword", "constant_keyword"}

    for item in scalar_items:
        key = str(item["key"])
        field = str(item["field"])
        field_type = str(item["type"])
        normalizer = str(item["normalizer"])
        shape = item["shape"]
        text_value = str(shape["text"])
        token_count = int(shape["token_count"])
        alpha_ratio = float(shape["alpha_ratio"])
        looks_numeric = bool(shape["looks_numeric"])
        looks_date = bool(shape["looks_date"])

        if field_type in keyword_types and 2 <= len(text_value) <= 180:
            exact_candidates.append(
                {
                    "text": text_value,
                    "query_mode": "term",
                    "field": field or key,
                    "case_insensitive": bool(normalizer),
                }
            )

        if field_type == "text":
            keyword_alias = f"{field}.keyword" if field else ""
            keyword_spec = field_specs.get(keyword_alias, {})
            if keyword_spec.get("type", "") in keyword_types and 2 <= len(text_value) <= 180:
                exact_candidates.append(
                    {
                        "text": text_value,
                        "query_mode": "term",
                        "field": keyword_alias,
                        "case_insensitive": bool(keyword_spec.get("normalizer", "")),
                    }
                )
            if token_count >= 1 and 2 <= len(text_value) <= 220:
                phrase_candidates.append(
                    {
                        "text": text_value,
                        "query_mode": "match_phrase",
                        "field": field or key,
                        "case_insensitive": False,
                    }
                )

        if (
            token_count >= 2
            and alpha_ratio >= 0.35
            and 8 <= len(text_value) <= 220
            and not _looks_like_url_noise(text_value)
        ):
            semantic_candidates.append(
                {
                    "text": text_value,
                    "field": field or key,
                }
            )

        if (
            field_type in structured_types
            or looks_numeric
            or looks_date
            or field_type == "boolean"
        ):
            structured_candidates.append(
                {
                    "field": field or key,
                    "value": text_value,
                    "type": field_type,
                }
            )

        if field_type in {"text", "keyword", "constant_keyword"}:
            for token in shape["tokens"]:
                if token.isdigit():
                    continue
                if len(token) >= 2:
                    anchor_tokens.append({"token": token, "field": field or key})

    if not exact_candidates:
        for candidate in phrase_candidates:
            exact_candidates.append(
                {
                    "text": candidate["text"],
                    "query_mode": "match_phrase",
                    "field": candidate["field"],
                    "case_insensitive": False,
                }
            )
            break

    if not semantic_candidates:
        for item in scalar_items:
            field_type = str(item.get("type", "")).strip().lower()
            if field_type not in {"text", "keyword", "constant_keyword"}:
                continue
            shape = item["shape"]
            text_value = str(shape["text"])
            if (
                len(text_value) >= 4
                and float(shape.get("alpha_ratio", 0.0)) >= 0.35
                and not bool(shape.get("looks_numeric", False))
                and not bool(shape.get("looks_date", False))
                and not _looks_like_url_noise(text_value)
            ):
                semantic_candidates.append(
                    {
                        "text": text_value,
                        "field": item["field"] or item["key"],
                    }
                )
                break

    return {
        "source": source,
        "scalar_items": scalar_items,
        "exact_candidates": exact_candidates,
        "phrase_candidates": phrase_candidates,
        "semantic_candidates": semantic_candidates,
        "structured_candidates": structured_candidates,
        "anchor_tokens": anchor_tokens,
    }


def _anchor_token_text(entry: object) -> str:
    if isinstance(entry, dict):
        return _normalize_text(entry.get("token", ""))
    return _normalize_text(entry)


def _anchor_token_field(entry: object) -> str:
    if isinstance(entry, dict):
        return _normalize_text(entry.get("field", ""))
    return ""


def _first_anchor_token(anchor_tokens: list[object], min_len: int) -> tuple[str, str]:
    for entry in anchor_tokens:
        token = _anchor_token_text(entry)
        if len(token) >= min_len:
            return token, _anchor_token_field(entry)
    return "", ""


def _score_doc_for_capability(features: dict[str, object], capability_id: str) -> float:
    exact_candidates = features.get("exact_candidates", [])
    semantic_candidates = features.get("semantic_candidates", [])
    structured_candidates = features.get("structured_candidates", [])
    anchor_tokens = features.get("anchor_tokens", [])
    if not isinstance(anchor_tokens, list):
        anchor_tokens = []

    if capability_id == "exact":
        if not exact_candidates:
            return 0.0
        best = next(iter(exact_candidates), {})
        mode = str(best.get("query_mode", ""))
        mode_bonus = 120.0 if mode == "term" else 80.0
        return mode_bonus + min(len(str(best.get("text", ""))), 100) / 100.0

    if capability_id == "semantic":
        if not semantic_candidates:
            return 0.0
        best_text = _best_semantic_text_from_candidates(semantic_candidates)
        if not best_text:
            return 0.0
        return 60.0 + min(len(best_text), 200) / 10.0

    if capability_id == "structured":
        return float(len(structured_candidates) * 20)

    if capability_id == "combined":
        if not structured_candidates:
            return 0.0
        score = 80.0 + float(len(structured_candidates) * 12)
        best_text = _best_semantic_text_from_candidates(semantic_candidates)
        if best_text:
            score += min(len(best_text), 200) / 40.0
        return score

    if capability_id == "autocomplete":
        longest = max((len(_anchor_token_text(token)) for token in anchor_tokens), default=0)
        return float(longest if longest >= 3 else 0)

    if capability_id == "fuzzy":
        longest = max((len(_anchor_token_text(token)) for token in anchor_tokens), default=0)
        return float(40 + longest) if longest >= 5 else 0.0

    return 0.0


def _capability_has_sample_support(
    features_list: list[dict[str, object]],
    capability_id: str,
) -> bool:
    normalized_id = _normalize_text(capability_id).lower()
    if not normalized_id:
        return False

    for features in features_list:
        if _score_doc_for_capability(features, normalized_id) > 0:
            return True
    return False


def _split_capabilities_by_sample_support(
    features_list: list[dict[str, object]],
    capabilities: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    """Partition capabilities into applicable vs unsupported for sampled docs."""
    applicable: list[dict[str, object]] = []
    skipped: list[dict[str, str]] = []

    for capability in capabilities:
        capability_id = _normalize_text(capability.get("id", "")).lower()
        if not capability_id:
            continue
        if _capability_has_sample_support(features_list, capability_id):
            applicable.append(capability)
            continue
        skipped.append(
            {
                "id": capability_id,
                "reason": "No compatible fields/values were found in sampled documents.",
            }
        )

    return applicable, skipped


def _select_docs_by_capability(
    features_list: list[dict[str, object]],
    capabilities: list[dict[str, object]],
) -> tuple[dict[str, int], list[str]]:
    """Assign the best sample document to each capability using a "prefer unique, allow reuse" strategy.

    For every capability the function scores each document (via
    ``_score_doc_for_capability``) and picks the highest-scoring one that has
    **not** already been claimed by a previous capability.  If all compatible
    documents are already taken, the highest-scoring document is reused as a
    fallback so the capability still has a demo document.

    Args:
        features_list: One entry per candidate document, as returned by
            ``_extract_doc_features``.  Each dict contains categorised field
            candidates (``exact_candidates``, ``semantic_candidates``,
            ``structured_candidates``, ``anchor_tokens``, etc.) that
            ``_score_doc_for_capability`` uses to compute a relevance score.

            See ``_extract_doc_features`` for the full schema and an example.

        capabilities: One entry per search capability, as returned by
            ``_extract_search_capabilities``.  Each dict has the following keys:

            - ``"id"`` (str): Canonical capability identifier (e.g.
              ``"exact"``, ``"semantic"``, ``"structured"``, ``"combined"``,
              ``"autocomplete"``, ``"fuzzy"``).
            - ``"label"`` (str): Normalised human-readable description.
            - ``"examples"`` (list[str]): Example query strings.

            See ``_extract_search_capabilities`` for the full schema and an
            example.

    Returns:
        A 2-tuple of:

        - ``selected`` (dict[str, int]): Mapping of capability id to the
          chosen document index in *features_list*.
        - ``notes`` (list[str]): Human-readable notes about selection issues,
          such as when no compatible document was found for a capability or
          when a document had to be reused.
    """
    selected: dict[str, int] = {}
    used_indexes: set[int] = set()
    notes: list[str] = []

    for capability in capabilities:
        capability_id = str(capability.get("id", "")).strip()
        if not capability_id:
            continue
        
        # tracks the highest-scoring document index that has not yet been
        # assigned to another capability (i.e., idx not in used_indexes).
        # This is the preferred pick because it maximizes diversity:
        # each capability ideally gets its own unique sample document.
        best_unused = (-1, -1.0)

        # tracks the highest-scoring document index regardless of whether
        # it has already been used. This is the fallback: if every compatible
        # document has already been claimed by another capability,
        # the function can still reuse one rather than having no document at all.

        best_any = (-1, -1.0)
        for idx, features in enumerate(features_list):
            score = _score_doc_for_capability(features, capability_id)
            if score <= 0:
                continue
            if score > best_any[1]:
                best_any = (idx, score)
            if idx not in used_indexes and score > best_unused[1]:
                best_unused = (idx, score)

        chosen_idx = best_unused[0] if best_unused[0] >= 0 else best_any[0]
        if chosen_idx < 0:
            notes.append(f"{capability_id}: no compatible sample document found")
            continue
        if chosen_idx in used_indexes and best_unused[0] < 0:
            notes.append(f"{capability_id}: reused a document due to limited sample coverage")

        selected[capability_id] = chosen_idx
        used_indexes.add(chosen_idx)

    return selected, notes


def _trim_words(text: str, max_words: int = 8) -> str:
    words = _normalize_text(text).split()
    return " ".join(words[:max_words]).strip()


def _mutate_token_for_typo(token: str) -> str:
    if len(token) <= 2:
        return token
    pivot = len(token) // 2
    return token[:pivot] + token[pivot + 1 :]


def _structured_candidate_parts(candidate: object) -> tuple[str, str]:
    if not isinstance(candidate, dict):
        return "", ""
    field_name = _normalize_text(str(candidate.get("field", "")).split(".")[-1])
    value_text = _normalize_text(str(candidate.get("value", "")))
    if not field_name or not value_text:
        return "", ""
    return field_name, value_text


def _select_structured_source_candidate(
    structured_candidates: list[dict[str, object]],
) -> dict[str, object]:
    for candidate in structured_candidates:
        field_name, value_text = _structured_candidate_parts(candidate)
        if field_name and value_text:
            return dict(candidate)
    return {}


def _format_structured_value_for_query(value_text: str) -> str:
    normalized = _normalize_text(value_text)
    if not normalized:
        return ""
    safe = normalized.replace('"', "'")
    needs_quotes = (
        bool(re.search(r"\s", safe))
        or ":" in safe
        or " and " in safe.lower()
        or " or " in safe.lower()
    )
    return f"\"{safe}\"" if needs_quotes else safe


def _unique_structured_pairs(
    structured_candidates: list[dict[str, object]],
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in structured_candidates:
        field_name, value_text = _structured_candidate_parts(candidate)
        if not field_name or not value_text:
            continue
        key = (field_name.lower(), value_text.lower())
        if key in seen:
            continue
        seen.add(key)
        pairs.append((field_name, value_text))
    return pairs


def _select_text_pair_for_combined_query(
    phrase_candidates: list[dict[str, object]],
    exact_candidates: list[dict[str, object]],
    excluded_fields: set[str] | None = None,
) -> tuple[str, str]:
    blocked = set(excluded_fields or set())

    def _candidate_pairs(candidates: list[dict[str, object]]) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for candidate in candidates:
            field_name = _normalize_text(str(candidate.get("field", "")).split(".")[-1])
            value_text = _normalize_text(candidate.get("text", ""))
            if not field_name or not value_text:
                continue
            field_key = field_name.lower()
            if field_key in blocked:
                continue
            pairs.append((field_name, _trim_words(value_text, 8)))
        return pairs

    phrase_pairs = _candidate_pairs(
        [item for item in phrase_candidates if isinstance(item, dict)]
    )
    if phrase_pairs:
        return phrase_pairs[0]

    exact_phrase_pairs = _candidate_pairs(
        [
            item
            for item in exact_candidates
            if isinstance(item, dict)
            and _normalize_text(item.get("query_mode", "")).lower() == "match_phrase"
        ]
    )
    if exact_phrase_pairs:
        return exact_phrase_pairs[0]

    return "", ""


def _compose_combined_structured_example(structured_candidates: list[dict[str, object]]) -> str:
    pairs = _unique_structured_pairs(structured_candidates)
    if len(pairs) < 2:
        return ""
    first_field, first_value = pairs[0]
    second_field, second_value = pairs[1]
    return (
        f"{first_field}: {_format_structured_value_for_query(first_value)} "
        f"and {second_field}: {_format_structured_value_for_query(second_value)}"
    )


def _compose_combined_text_structured_example(
    phrase_candidates: list[dict[str, object]],
    exact_candidates: list[dict[str, object]],
    structured_candidates: list[dict[str, object]],
) -> str:
    pairs = _unique_structured_pairs(structured_candidates)
    if not pairs:
        return ""

    structured_field, structured_value = pairs[0]
    text_field, text_value = _select_text_pair_for_combined_query(
        phrase_candidates=phrase_candidates,
        exact_candidates=exact_candidates,
        excluded_fields={structured_field.lower()},
    )
    if not text_field or not text_value:
        text_field, text_value = _select_text_pair_for_combined_query(
            phrase_candidates=phrase_candidates,
            exact_candidates=exact_candidates,
            excluded_fields=set(),
        )
    if not text_field or not text_value:
        return ""

    return (
        f"{text_field}: {_format_structured_value_for_query(text_value)} "
        f"and {structured_field}: {_format_structured_value_for_query(structured_value)}"
    )


_SEMANTIC_REWRITE_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
    "may",
    "refer",
    "refers",
    "also",
    "other",
    "uses",
    "use",
    "see",
    "list",
    "including",
    "include",
    "various",
    "named",
    "name",
    "names",
}
_SEMANTIC_REWRITE_URL_TOKENS = {
    "http",
    "https",
    "www",
    "com",
    "org",
    "net",
    "edu",
    "gov",
    "wiki",
    "wikipedia",
}


def _is_truthy_flag(raw_value: str) -> bool:
    return str(raw_value or "").strip().lower() in {"1", "true", "yes", "on", "y"}


def _semantic_query_rewrite_llm_enabled() -> bool:
    runtime_mode = str(os.getenv(RUNTIME_MODE_ENV, "")).strip().lower()
    if runtime_mode == RUNTIME_MODE_MCP:
        return False
    explicit = os.getenv(SEMANTIC_QUERY_REWRITE_FLAG, "").strip()
    if explicit:
        return _is_truthy_flag(explicit)
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    return bool(os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"))


def _looks_like_url_noise(text: str) -> bool:
    normalized = _normalize_text(text).lower()
    if not normalized:
        return False
    if "http://" in normalized or "https://" in normalized or "www." in normalized:
        return True
    if normalized.count("/") >= 3:
        return True
    tokens = re.findall(r"[a-z0-9_]+", normalized)
    if not tokens:
        return False
    domain_token_hits = sum(1 for token in tokens if token in _SEMANTIC_REWRITE_URL_TOKENS)
    return domain_token_hits >= 2


def _sanitize_semantic_rewrite_output(text: str) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return ""
    first_line = normalized.splitlines()[0].strip()
    first_line = re.sub(r"^[-*]\s+", "", first_line)
    first_line = re.sub(r"^(?:semantic\s+query|query)\s*:\s*", "", first_line, flags=re.IGNORECASE)
    first_line = first_line.strip().strip("`").strip("'").strip('"').strip()
    if not first_line:
        return ""
    return _normalize_text(first_line)[:120]


_DISAMBIGUATION_LEAD_PATTERN = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9 .'\-]{0,80})\s+may\s+refer\s+to\b",
    re.IGNORECASE,
)


def _extract_disambiguation_subject(text: str) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return ""
    match = _DISAMBIGUATION_LEAD_PATTERN.match(normalized)
    if not match:
        return ""
    subject = _normalize_text(match.group(1))
    if not subject:
        return ""
    words = subject.split()
    if len(words) > 5:
        return ""
    return " ".join(words)


def _rewrite_semantic_example_with_llm(text: str) -> str:
    if not _semantic_query_rewrite_llm_enabled():
        return ""

    normalized = _normalize_text(text)
    if len(normalized) < 8:
        return ""

    model_id = (
        os.getenv(SEMANTIC_QUERY_REWRITE_MODEL_ID_ENV, DEFAULT_SEMANTIC_QUERY_REWRITE_MODEL_ID).strip()
        or DEFAULT_SEMANTIC_QUERY_REWRITE_MODEL_ID
    )
    source_excerpt = normalized[:1800]

    try:
        from strands import Agent
        from strands.models import BedrockModel

        model = BedrockModel(
            model_id=model_id,
            max_tokens=120,
        )
        agent = Agent(
            model=model,
            system_prompt=(
                "You rewrite document snippets into one concise semantic search query.\n"
                "Rules:\n"
                "- Output only one single-line query.\n"
                "- Keep it natural and specific (about 4-12 words).\n"
                "- Do not include URLs, domain fragments, or boilerplate words.\n"
                "- Do not add explanations, labels, bullets, or quotes.\n"
                "- Prefer core topic/entities and user intent."
            ),
        )
        response = agent(
            "Rewrite this snippet into one semantic search query only:\n"
            f"{source_excerpt}"
        )
    except Exception:
        return ""

    rewritten = _sanitize_semantic_rewrite_output(str(response))
    if len(rewritten.split()) < 2:
        return ""
    if _looks_like_url_noise(rewritten):
        return ""
    return rewritten


def _select_semantic_source_candidate(
    semantic_candidates: list[dict[str, object]],
) -> dict[str, object]:
    best: dict[str, object] = {}
    best_score = -1.0

    for candidate in semantic_candidates:
        if not isinstance(candidate, dict):
            continue
        text = _normalize_text(candidate.get("text", ""))
        if not text:
            continue

        shape = _value_shape(text)
        token_count = int(shape.get("token_count", 0))
        tokens = [str(token).lower() for token in shape.get("tokens", [])]
        unique_token_ratio = (len(set(tokens)) / token_count) if token_count else 0.0
        score = text_richness_score(text)
        score += min(token_count, 12) * 1.5
        score += unique_token_ratio * 8.0
        if token_count <= 2:
            score -= 15.0
        if bool(shape.get("looks_numeric", False)) or bool(shape.get("looks_date", False)):
            score -= 20.0
        if _looks_like_url_noise(text):
            score -= 60.0

        if score > best_score:
            best_score = score
            best = dict(candidate)
            best["text"] = text
    return best


def _best_semantic_text_from_candidates(semantic_candidates: list[dict[str, object]]) -> str:
    selected = _select_semantic_source_candidate(semantic_candidates)
    return _normalize_text(selected.get("text", ""))


def _extract_concept_tokens(text: str) -> list[str]:
    shape = _value_shape(_normalize_text(text))
    concepts: list[str] = []
    seen: set[str] = set()

    for raw_token in shape.get("tokens", []):
        token = str(raw_token).strip().lower()
        token = re.sub(r"^\d+", "", token)
        token = re.sub(r"\d+$", "", token)
        if not token or len(token) < 3 or token.isdigit():
            continue
        if any(ch.isdigit() for ch in token):
            continue
        if token in _SEMANTIC_REWRITE_URL_TOKENS:
            continue
        if token in _SEMANTIC_REWRITE_STOPWORDS or token in seen:
            continue
        seen.add(token)
        concepts.append(token)
        if len(concepts) >= 6:
            break
    return concepts


def _compose_semantic_query(
    original_text: str,
    concept_tokens: list[str],
) -> tuple[str, bool]:
    base_tokens = concept_tokens[:6]
    if not base_tokens:
        fallback_tokens = [
            str(token).lower()
            for token in _value_shape(original_text).get("tokens", [])
            if (
                len(str(token)) >= 3
                and not str(token).isdigit()
                and str(token).lower() not in _SEMANTIC_REWRITE_STOPWORDS
                and str(token).lower() not in _SEMANTIC_REWRITE_URL_TOKENS
                and not any(ch.isdigit() for ch in str(token))
            )
        ]
        base_tokens = fallback_tokens[:6]

    base = _normalize_text(" ".join(base_tokens))
    if not base:
        return "", False

    base_shape = _value_shape(base)
    token_count = int(base_shape.get("token_count", 0))
    if token_count >= 4 and len(base) >= 12:
        return base, True
    return "", False


def _rewrite_semantic_example(text: str) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return ""

    llm_rewritten = _rewrite_semantic_example_with_llm(normalized)
    if llm_rewritten:
        return llm_rewritten[:120]

    disambiguation_subject = _extract_disambiguation_subject(normalized)
    if disambiguation_subject:
        return _normalize_text(f"{disambiguation_subject} disambiguation")[:120]

    concept_tokens = _extract_concept_tokens(normalized)
    rewritten, confidence_high = _compose_semantic_query(
        original_text=normalized,
        concept_tokens=concept_tokens,
    )
    if not confidence_high:
        if _looks_like_url_noise(normalized):
            fallback_tokens = _extract_concept_tokens(normalized)
            fallback_text = _normalize_text(" ".join(fallback_tokens[:6]))
            if fallback_text:
                return fallback_text[:120]
        return normalized[:120]

    final_text = _normalize_text(rewritten)
    return final_text[:120] if final_text else normalized[:120]


def _infer_capability_examples_from_features(
    capability_id: str,
    features: dict[str, object],
) -> list[str]:
    exact_candidates = features.get("exact_candidates", [])
    phrase_candidates = features.get("phrase_candidates", [])
    semantic_candidates = features.get("semantic_candidates", [])
    structured_candidates = features.get("structured_candidates", [])
    anchor_tokens = features.get("anchor_tokens", [])
    if not isinstance(exact_candidates, list):
        exact_candidates = []
    if not isinstance(phrase_candidates, list):
        phrase_candidates = []
    if not isinstance(semantic_candidates, list):
        semantic_candidates = []
    if not isinstance(structured_candidates, list):
        structured_candidates = []
    if not isinstance(anchor_tokens, list):
        anchor_tokens = []

    if capability_id == "exact":
        if not exact_candidates:
            return []
        best = next(
            iter(
                sorted(
                    exact_candidates,
                    key=lambda item: (
                        0 if str(item.get("query_mode", "")) == "term" else 1,
                        -len(str(item.get("text", ""))),
                    ),
                )
            ),
            {},
        )
        text = _normalize_text(best.get("text", ""))
        return [text] if text else []

    if capability_id == "semantic":
        selected = _select_semantic_source_candidate(semantic_candidates)
        source_text = _normalize_text(selected.get("text", ""))
        if not source_text:
            return []
        rewritten = _rewrite_semantic_example(source_text)
        if rewritten:
            return [rewritten]
        if _looks_like_url_noise(source_text):
            return []
        return [source_text]

    if capability_id == "structured":
        structured = _select_structured_source_candidate(structured_candidates)
        if not structured:
            return []
        field_name, value_text = _structured_candidate_parts(structured)
        if not field_name or not value_text:
            return []
        return [f"{field_name}: {value_text}"]

    if capability_id == "combined":
        combined_text = _compose_combined_structured_example(structured_candidates)
        if not combined_text:
            combined_text = _compose_combined_text_structured_example(
                phrase_candidates=phrase_candidates,
                exact_candidates=exact_candidates,
                structured_candidates=structured_candidates,
            )
        return [combined_text] if combined_text else []

    if capability_id == "autocomplete":
        token_text, _ = _first_anchor_token(anchor_tokens, min_len=3)
        if not token_text:
            return []
        if len(token_text) > 4:
            prefix_len = min(6, len(token_text) - 1)
            return [token_text[:prefix_len]]
        return [token_text]

    if capability_id == "fuzzy":
        token_text, _ = _first_anchor_token(anchor_tokens, min_len=5)
        if not token_text:
            return []
        return [_mutate_token_for_typo(token_text)]

    return []


def _build_suggestion_entry(capability: dict[str, object], features: dict[str, object]) -> dict[str, object] | None:
    capability_id = str(capability.get("id", "")).strip().lower()
    exact_candidates = features.get("exact_candidates", [])
    phrase_candidates = features.get("phrase_candidates", [])
    semantic_candidates = features.get("semantic_candidates", [])
    structured_candidates = features.get("structured_candidates", [])
    anchor_tokens = features.get("anchor_tokens", [])
    if not isinstance(anchor_tokens, list):
        anchor_tokens = []
    capability_examples = capability.get("examples", [])

    def _first_capability_example() -> str:
        if not isinstance(capability_examples, list):
            return ""
        for item in capability_examples:
            normalized = _normalize_text(item)
            if normalized:
                return normalized
        return ""

    def _prefer_text_field() -> str:
        """Pick a single field from the document's feature candidates for text-like search.

        Prefer fields that support phrase/semantic/full-text search over keyword-only
        fields.  Order: phrase > semantic > exact (non-.keyword) > exact (any).

        .keyword subfields are for exact match/aggregation; we prefer the parent
        text field when it exists, and only fall back to .keyword if nothing else.
        Returns the first non-empty field found, or "" if none.
        """
        # 1. phrase_candidates: match_phrase fields (usually text-type, full-text searchable)
        for candidate in phrase_candidates:
            field = _normalize_text(candidate.get("field", ""))
            if field:
                return field
        # 2. semantic_candidates: fields used for semantic/neural search (typically longer text)
        for candidate in semantic_candidates:
            field = _normalize_text(candidate.get("field", ""))
            if field:
                return field
        # 3. exact_candidates: prefer fields NOT ending with .keyword (avoid keyword subfield)
        for candidate in exact_candidates:
            field = _normalize_text(candidate.get("field", ""))
            if field and not field.endswith(".keyword"):
                return field
        # 4. exact_candidates: fallback to any field, including .keyword subfields
        for candidate in exact_candidates:
            field = _normalize_text(candidate.get("field", ""))
            if field:
                return field
        return ""

    def _prefer_keyword_field() -> str:
        for candidate in exact_candidates:
            field = _normalize_text(candidate.get("field", ""))
            if field.endswith(".keyword"):
                return field
        for candidate in exact_candidates:
            field = _normalize_text(candidate.get("field", ""))
            if field:
                return field
        return ""

    if capability_id == "exact":
        if not exact_candidates:
            return None
        best = next(
            iter(
                sorted(
                    exact_candidates,
                    key=lambda item: (
                        0 if str(item.get("query_mode", "")) == "term" else 1,
                        -len(str(item.get("text", ""))),
                    ),
                )
            ),
            {},
        )
        suggestion_text = _normalize_text(str(best.get("text", "")))
        if len(suggestion_text) < 2:
            return None
        return {
            "text": suggestion_text[:120],
            "capability": "exact",
            "query_mode": str(best.get("query_mode", "default")),
            "field": str(best.get("field", "")),
            "value": "",
            "case_insensitive": bool(best.get("case_insensitive", False)),
        }

    if capability_id == "semantic":
        planner_example = _first_capability_example()
        if planner_example:
            suggestion_text = planner_example
        else:
            best_text = _best_semantic_text_from_candidates(semantic_candidates)
            if not best_text:
                return None
            suggestion_text = _trim_words(best_text, 7)
        field = _prefer_text_field()
        query_mode = "hybrid"
        value = ""
    elif capability_id == "structured":
        structured = _select_structured_source_candidate(structured_candidates)
        if not structured:
            return None
        field_name, value_text = _structured_candidate_parts(structured)
        if not field_name or not value_text:
            return None
        suggestion_text = f"{field_name}: {value_text}"
        field = _normalize_text(structured.get("field", ""))
        query_mode = "structured_filter"
        value = value_text
    elif capability_id == "combined":
        planner_example = _first_capability_example()
        structured = _select_structured_source_candidate(structured_candidates)
        if not structured:
            return None
        field_name, value_text = _structured_candidate_parts(structured)
        if not field_name or not value_text:
            return None
        if planner_example:
            suggestion_text = planner_example
        else:
            suggestion_text = _compose_combined_structured_example(structured_candidates)
            if not suggestion_text:
                suggestion_text = _compose_combined_text_structured_example(
                    phrase_candidates=phrase_candidates,
                    exact_candidates=exact_candidates,
                    structured_candidates=structured_candidates,
                )
                if not suggestion_text:
                    return None
        field = _normalize_text(structured.get("field", ""))
        query_mode = "hybrid_structured"
        value = value_text
    elif capability_id == "autocomplete":
        token, token_field = _first_anchor_token(anchor_tokens, min_len=3)
        if not token:
            return None
        if len(token) > 4:
            prefix_len = min(6, len(token) - 1)
            suggestion_text = token[:prefix_len]
        else:
            suggestion_text = token
        field = token_field or _prefer_keyword_field() or _prefer_text_field()
        query_mode = "prefix"
        value = ""
    elif capability_id == "fuzzy":
        token, token_field = _first_anchor_token(anchor_tokens, min_len=5)
        if not token:
            return None
        suggestion_text = _mutate_token_for_typo(token)
        field = token_field or _prefer_text_field()
        query_mode = "fuzzy"
        value = ""
    else:
        return None

    normalized = _normalize_text(suggestion_text)
    if len(normalized) < 2:
        return None
    return {
        "text": normalized[:120],
        "capability": capability_id,
        "query_mode": query_mode,
        "field": field,
        "value": value,
        "case_insensitive": False,
    }


def _dedupe_suggestion_meta(entries: list[dict[str, object]]) -> list[dict[str, object]]:
    deduped: list[dict[str, object]] = []
    seen: set[str] = set()
    for entry in entries:
        text = _normalize_text(entry.get("text", ""))
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        item = dict(entry)
        item["text"] = text
        deduped.append(item)
    return deduped


def _find_suggestion_meta(index_name: str, query_text: str) -> dict[str, object] | None:
    query_key = _normalized_query_key(query_text)
    if not query_key:
        return None
    for entry in _search_ui.suggestion_meta_by_index.get(index_name, []):
        if _normalized_query_key(entry.get("text", "")) == query_key:
            return entry
    return None


def _evaluate_capability_driven_selection(
    worker_output: str,
    count: int = 10,
    sample_doc_json: str = "",
    source_local_file: str = "",
    source_index_name: str = "",
    field_specs: dict[str, dict[str, str]] | None = None,
) -> dict[str, object]:
    effective_count = max(1, min(count, 100))
    result: dict[str, object] = {
        "capabilities": [],
        "capability_items": [],
        "applicable_capabilities": [],
        "applicable_capability_items": [],
        "skipped_capabilities": [],
        "suggestion_meta": [],
        "selected_indexes_for_indexing": [],
        "features_list": [],
        "notes": [],
        "used_inferred_field_specs": False,
    }

    capabilities = _extract_search_capabilities(worker_output)
    result["capability_items"] = capabilities
    result["capabilities"] = [
        str(item.get("id", "")).strip().lower()
        for item in capabilities
        if str(item.get("id", "")).strip()
    ]
    if not capabilities:
        result["notes"] = ["worker output has no Search Capabilities section"]
        return result

    candidate_limit = max(60, min(200, effective_count * 20))
    candidate_docs, sample_note = _load_sample_docs_with_note(
        limit=candidate_limit,
        sample_doc_json=sample_doc_json,
        source_local_file=source_local_file,
        source_index_name=source_index_name,
    )
    if not candidate_docs:
        notes = ["no sample documents available for capability-driven selection"]
        if sample_note:
            notes.append(sample_note)
        result["notes"] = notes
        return result

    resolved_field_specs = dict(field_specs) if isinstance(field_specs, dict) else {}
    notes: list[str] = []
    if not resolved_field_specs:
        resolved_field_specs = _infer_field_specs_from_sample_docs(candidate_docs)
        if resolved_field_specs:
            result["used_inferred_field_specs"] = True
            notes.append(
                "index mapping unavailable; inferred field specs from sampled documents for capability precheck."
            )

    features_list = [_extract_doc_features(doc, resolved_field_specs) for doc in candidate_docs]
    applicable_capabilities, skipped_capabilities = _split_capabilities_by_sample_support(
        features_list=features_list,
        capabilities=capabilities,
    )
    result["features_list"] = features_list
    result["applicable_capability_items"] = applicable_capabilities
    result["applicable_capabilities"] = [
        str(item.get("id", "")).strip().lower()
        for item in applicable_capabilities
        if str(item.get("id", "")).strip()
    ]
    result["skipped_capabilities"] = skipped_capabilities

    selected_by_capability, selection_notes = _select_docs_by_capability(
        features_list,
        applicable_capabilities,
    )

    # Infer examples from sampled docs to keep planner/worker suggestions grounded in data.
    for capability in applicable_capabilities:
        capability_id = _normalize_text(capability.get("id", "")).lower()
        if not capability_id:
            continue
        idx = selected_by_capability.get(capability_id)
        if idx is None or idx < 0 or idx >= len(features_list):
            continue
        inferred_examples = _infer_capability_examples_from_features(capability_id, features_list[idx])
        capability["examples"] = inferred_examples

    selected_indexes_for_indexing: list[int] = []
    for capability in applicable_capabilities:
        capability_id = str(capability.get("id", "")).strip()
        if not capability_id:
            continue
        idx = selected_by_capability.get(capability_id)
        if idx is None:
            continue
        if idx not in selected_indexes_for_indexing:
            selected_indexes_for_indexing.append(idx)

    for idx in range(len(features_list)):
        if len(selected_indexes_for_indexing) >= effective_count:
            break
        if idx in selected_indexes_for_indexing:
            continue
        selected_indexes_for_indexing.append(idx)

    suggestion_entries: list[dict[str, object]] = []
    for capability in applicable_capabilities:
        capability_id = str(capability.get("id", "")).strip()
        if not capability_id:
            continue
        idx = selected_by_capability.get(capability_id)
        if idx is None:
            continue
        entry = _build_suggestion_entry(capability, features_list[idx])
        if entry is not None:
            suggestion_entries.append(entry)

    result["suggestion_meta"] = _dedupe_suggestion_meta(suggestion_entries) if suggestion_entries else []
    result["selected_indexes_for_indexing"] = selected_indexes_for_indexing
    notes.extend(selection_notes)
    if not selected_indexes_for_indexing:
        notes.append("no documents selected for indexing")
    result["notes"] = notes
    return result


def preview_cap_driven_verification(
    worker_output: str,
    count: int = 10,
    sample_doc_json: str = "",
    source_local_file: str = "",
    source_index_name: str = "",
) -> dict[str, object]:
    evaluation = _evaluate_capability_driven_selection(
        worker_output=worker_output,
        count=count,
        sample_doc_json=sample_doc_json,
        source_local_file=source_local_file,
        source_index_name=source_index_name,
        field_specs=None,
    )
    return {
        "capabilities": evaluation.get("capabilities", []),
        "applicable_capabilities": evaluation.get("applicable_capabilities", []),
        "skipped_capabilities": evaluation.get("skipped_capabilities", []),
        "suggestion_meta": evaluation.get("suggestion_meta", []),
        "selected_doc_count": len(evaluation.get("selected_indexes_for_indexing", [])),
        "notes": evaluation.get("notes", []),
    }



def apply_capability_driven_verification(
    worker_output: str,
    index_name: str = "",
    count: int = 10,
    id_prefix: str = "verification",
    sample_doc_json: str = "",
    source_local_file: str = "",
    source_index_name: str = "",
    existing_verification_doc_ids: str = "",
) -> dict[str, object]:
    effective_count = max(1, min(count, 100))
    target_index = (index_name or "").strip()
    result: dict[str, object] = {
        "applied": False,
        "index_name": target_index,
        "capabilities": [],
        "applicable_capabilities": [],
        "skipped_capabilities": [],
        "indexed_count": 0,
        "doc_ids": [],
        "suggestion_meta": [],
        "notes": [],
    }

    if not target_index:
        result["notes"] = [
            "index_name is required for apply_capability_driven_verification; "
            "fallback index resolution is disabled."
        ]
        return result

    try:
        opensearch_client = _create_client()
    except Exception as e:
        result["notes"] = [f"failed to connect to OpenSearch: {e}"]
        return result

    field_specs = _extract_index_field_specs(opensearch_client, target_index)
    field_types_for_policy = {
        field_name: str(spec.get("type", "")).strip().lower()
        for field_name, spec in field_specs.items()
        if isinstance(spec, dict)
    }
    sample_docs_for_policy = _load_sample_docs(
        limit=200,
        sample_doc_json=sample_doc_json,
        source_local_file=source_local_file,
        source_index_name=source_index_name,
    )
    policy_violations = _collect_boolean_typing_policy_violations(
        field_types=field_types_for_policy,
        sample_docs=sample_docs_for_policy,
    )
    if policy_violations:
        result["notes"] = ["Error: preflight failed. " + " ".join(policy_violations)]
        return result

    evaluation = _evaluate_capability_driven_selection(
        worker_output=worker_output,
        count=effective_count,
        sample_doc_json=sample_doc_json,
        source_local_file=source_local_file,
        source_index_name=source_index_name,
        field_specs=field_specs,
    )
    result["capabilities"] = list(evaluation.get("capabilities", []))
    result["applicable_capabilities"] = list(evaluation.get("applicable_capabilities", []))
    result["skipped_capabilities"] = list(evaluation.get("skipped_capabilities", []))
    result["suggestion_meta"] = list(evaluation.get("suggestion_meta", []))
    result["notes"] = list(evaluation.get("notes", []))

    selected_indexes_for_indexing = list(evaluation.get("selected_indexes_for_indexing", []))
    features_list = list(evaluation.get("features_list", []))
    if not selected_indexes_for_indexing:
        return result

    for existing_id in _parse_id_list(existing_verification_doc_ids):
        try:
            opensearch_client.delete(index=target_index, id=existing_id, ignore=[404])
        except Exception:
            continue

    indexed_ids: list[str] = []
    index_errors: list[str] = []
    for offset, doc_idx in enumerate(selected_indexes_for_indexing, start=1):
        doc_id = f"{id_prefix}-{offset}"
        doc_source = features_list[doc_idx]["source"]
        try:
            opensearch_client.index(index=target_index, body=doc_source, id=doc_id)
            indexed_ids.append(doc_id)
        except Exception as e:
            index_errors.append(f"{doc_id}: {e}")

    if indexed_ids:
        opensearch_client.indices.refresh(index=target_index)

    notes = list(evaluation.get("notes", []))
    notes.extend(index_errors)
    result["notes"] = notes
    result["indexed_count"] = len(indexed_ids)
    result["doc_ids"] = indexed_ids
    result["applied"] = bool(indexed_ids)
    return result


def _suggestion_candidates_from_doc(source: dict) -> list[str]:
    """Extract search suggestion candidates from a document, ranked by value quality.

    Instead of relying on hardcoded field name hints (which don't generalize
    across unknown customer schemas), we score each scalar value by its
    characteristics: multi-word, mostly-alphabetic strings in a reasonable
    length range make the best suggestions.
    """
    if not isinstance(source, dict):
        return []

    scored: list[tuple[float, str]] = []

    for value in source.values():
        if value is None or isinstance(value, (dict, list)):
            continue
        shape = _value_shape(str(value))
        compact = str(shape["text"])
        length = int(shape["length"])

        if length < 4 or length > 80:
            continue
        if shape["looks_numeric"] or shape["looks_date"]:
            continue

        # Use shared text_richness_score for consistent scoring across modules
        score = text_richness_score(str(value))
        scored.append((score, compact))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [text for _, text in scored]

def _search_ui_suggestions(
    index_name: str,
    max_count: int = 6,
    sample_doc_json: str = "",
    source_local_file: str = "",
    source_index_name: str = "",
) -> tuple[list[str], list[dict[str, object]]]:
    """Generate search suggestions for the given index.
    
    Parameters:
    - index_name: the name of the index to generate suggestions for
    - max_count: Show only a handful of top suggestions to the user.

    Returns:
    - A tuple containing a list of suggestions and a list of suggestion metadata
    """
    deduped: list[str] = []
    deduped_meta: list[dict[str, object]] = []
    seen: set[str] = set()
    explicit_meta_entries = _search_ui.suggestion_meta_by_index.get(index_name, [])
    has_explicit_meta = bool(index_name) and index_name in _search_ui.suggestion_meta_by_index

    def _append(text_value: object, meta_entry: dict[str, object] | None = None) -> None:
        # _normalize_text collapses whitespace; if the result is an empty string, it's skipped.
        text = _normalize_text(text_value)
        if not text:
            return
        key = text.lower()
        # The lowercased text is checked against seen. If it's already there, it's a duplicate and skipped.
        if key in seen:
            return
        seen.add(key)
        deduped.append(text)
        if meta_entry is not None:
            merged = dict(meta_entry)
            merged["text"] = text
            merged["capability"] = _normalize_text(merged.get("capability", "")).lower()
            merged["query_mode"] = _normalize_text(merged.get("query_mode", "")) or "default"
            merged["field"] = _normalize_text(merged.get("field", ""))
            merged["value"] = _normalize_text(merged.get("value", ""))
            merged["case_insensitive"] = bool(merged.get("case_insensitive", False))
        else:
            merged = {
                "text": text,
                "capability": "",
                "query_mode": "default",
                "field": "",
                "value": "",
                "case_insensitive": False,
            }
        deduped_meta.append(merged)

    for entry in explicit_meta_entries:
        _append(entry.get("text", ""), entry)
        if len(deduped) >= max_count:
            return deduped, deduped_meta

    # If suggestions were explicitly set for this index (even an empty list),
    # do not mix in fallback suggestions from existing index documents.
    if has_explicit_meta:
        return deduped[:max_count], deduped_meta[:max_count]

    if index_name:
        try:
            opensearch_client = _create_client()
            response = opensearch_client.search(
                index=index_name,
                # fetch more than needed to ensure we get enough unique suggestions.
                body={"size": max_count * 4, "query": {"match_all": {}}},
            )
            for hit in response.get("hits", {}).get("hits", []):
                source = hit.get("_source", {})
                for suggestion in _suggestion_candidates_from_doc(source):
                    _append(suggestion)
                    # early return: the function stops processing well before all max_count * 4 docs are examined. 
                    if len(deduped) >= max_count:
                        return deduped, deduped_meta
        except Exception:
            pass

    if not deduped:
        # If we still don't have enough unique suggestions, fetch some more sample documents.
        for doc in _load_sample_docs(
            limit=max_count * 2,
            sample_doc_json=sample_doc_json,
            source_local_file=source_local_file,
            source_index_name=source_index_name,
        ):
            for suggestion in _suggestion_candidates_from_doc(doc):
                _append(suggestion)
                # early return: the function stops processing well before all max_count * 2 docs are examined. 
                if len(deduped) >= max_count:
                    return deduped, deduped_meta

    # If there's truly no data to draw from, returning an empty list
    # is more honest than showing fake suggestions that won't match anything.
    return deduped[:max_count], deduped_meta[:max_count]


def _search_ui_preview_text(source: dict) -> str:
    candidates = _suggestion_candidates_from_doc(source)
    if candidates:
        return candidates[0]
    if source:
        for value in source.values():
            if value is None or isinstance(value, (dict, list)):
                continue
            text = " ".join(str(value).split())
            if text:
                return text[:180]
    return "(No preview text)"


def _resolve_autocomplete_fields(
    field_specs: dict[str, dict[str, str]],
    preferred_field: str = "",
    limit: int = 4,
) -> list[str]:
    """Resolve queryable fields for autocomplete prefix matching."""
    selected: list[str] = []
    seen: set[str] = set()

    def _append(field_name: str) -> None:
        normalized = _normalize_text(field_name)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        selected.append(normalized)

    preferred = _normalize_text(preferred_field)
    if preferred:
        resolved_field, resolved_spec = _resolve_field_spec_for_doc_key(preferred, field_specs)
        candidate = resolved_field or preferred
        candidate_type = str(resolved_spec.get("type", "")).strip().lower()
        if candidate_type in {"keyword", "constant_keyword", "text"}:
            _append(candidate)

    keyword_fields = [
        field_name
        for field_name, spec in field_specs.items()
        if str(spec.get("type", "")).strip().lower() in {"keyword", "constant_keyword"}
    ]
    text_fields = [
        field_name
        for field_name, spec in field_specs.items()
        if str(spec.get("type", "")).strip().lower() == "text"
    ]

    def _rank(field_name: str) -> tuple[int, int, str]:
        return (field_name.count("."), len(field_name), field_name)

    for field_name in sorted(keyword_fields, key=_rank):
        _append(field_name)
        if len(selected) >= max(1, limit):
            return selected

    for field_name in sorted(text_fields, key=_rank):
        _append(field_name)
        if len(selected) >= max(1, limit):
            return selected

    return selected


def _extract_values_from_source_by_path(source: object, field_path: str) -> list[object]:
    """Extract scalar values from source for a dotted field path."""
    path = _normalize_text(field_path)
    if not path:
        return []
    segments = [segment for segment in path.split(".") if segment]
    if not segments:
        return []

    values: list[object] = []

    def _walk(node: object, idx: int) -> None:
        if idx >= len(segments):
            if isinstance(node, list):
                for item in node:
                    _walk(item, idx)
                return
            if isinstance(node, dict) or node is None:
                return
            values.append(node)
            return

        if isinstance(node, list):
            for item in node:
                _walk(item, idx)
            return

        if not isinstance(node, dict):
            return

        key = segments[idx]
        if key not in node:
            return
        _walk(node.get(key), idx + 1)

    _walk(source, 0)
    return values


def _source_field_variants(field_name: str) -> list[str]:
    normalized = _normalize_text(field_name)
    if not normalized:
        return []
    variants = [normalized]
    if normalized.endswith(".keyword"):
        base_field = normalized[:-8]
        if base_field:
            variants.insert(0, base_field)
    return variants


def _search_ui_autocomplete(
    index_name: str,
    prefix_text: str,
    size: int = 8,
    preferred_field: str = "",
) -> dict[str, object]:
    """Return distinct autocomplete options for a prefix."""
    target_index = _normalize_text(index_name)
    prefix = _normalize_text(prefix_text)
    effective_size = max(1, min(size, 20))
    if not target_index or not prefix:
        return {
            "index": target_index,
            "prefix": prefix,
            "field": "",
            "options": [],
            "error": "",
        }

    try:
        opensearch_client = _create_client()
        field_specs = _extract_index_field_specs(opensearch_client, target_index)
        fields = _resolve_autocomplete_fields(
            field_specs=field_specs,
            preferred_field=preferred_field,
            limit=4,
        )
        if not fields:
            return {
                "index": target_index,
                "prefix": prefix,
                "field": "",
                "options": [],
                "error": "No suitable autocomplete fields found.",
            }

        should_clauses = [
            {"prefix": {field_name: {"value": prefix}}}
            for field_name in fields
        ]
        body = {
            "size": max(effective_size * 8, 24),
            "query": {
                "bool": {
                    "should": should_clauses,
                    "minimum_should_match": 1,
                }
            },
        }
        response = opensearch_client.search(index=target_index, body=body)

        options: list[str] = []
        seen: set[str] = set()
        prefix_lower = prefix.lower()

        for hit in response.get("hits", {}).get("hits", []):
            source = hit.get("_source", {})
            if not isinstance(source, dict):
                continue

            for field_name in fields:
                for variant in _source_field_variants(field_name):
                    raw_values = _extract_values_from_source_by_path(source, variant)
                    for raw_value in raw_values:
                        candidate = _normalize_text(raw_value)
                        if not candidate:
                            continue
                        if not candidate.lower().startswith(prefix_lower):
                            continue
                        key = candidate.lower()
                        if key in seen:
                            continue
                        seen.add(key)
                        options.append(candidate[:120])
                        if len(options) >= effective_size:
                            return {
                                "index": target_index,
                                "prefix": prefix,
                                "field": fields[0],
                                "options": options,
                                "error": "",
                            }

        return {
            "index": target_index,
            "prefix": prefix,
            "field": fields[0],
            "options": options,
            "error": "",
        }
    except Exception as e:
        return {
            "index": target_index,
            "prefix": prefix,
            "field": "",
            "options": [],
            "error": str(e),
        }


_NUMERIC_FIELD_TYPES = {
    "byte",
    "short",
    "integer",
    "long",
    "float",
    "half_float",
    "double",
    "scaled_float",
}
_KEYWORD_FIELD_TYPES = {"keyword", "constant_keyword"}
_EXACT_TERM_FIELD_TYPES = _KEYWORD_FIELD_TYPES | _NUMERIC_FIELD_TYPES | {
    "boolean",
    "date",
    "date_nanos",
    "ip",
    "version",
    "unsigned_long",
}
_STRUCTURED_QUERY_PAIR_PATTERN = re.compile(
    r"""
    (?P<field>[A-Za-z0-9_.-]+)\s*:\s*
    (?P<value>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|.*?)
    (?:(?:\s+and\s+)(?=[A-Za-z0-9_.-]+\s*:)|$)
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _resolve_text_query_fields(field_specs: dict[str, dict[str, str]], limit: int = 6) -> list[str]:
    """Select the best text/keyword fields from the index mapping for query targeting.

    Instead of hardcoded field-name hints (which are data-specific and don't
    generalise across unknown customer schemas), we rank fields by structural
    signals that work for any mapping:

    - Nesting depth: top-level fields (no dots) are usually primary content
      fields; deeply nested ones are less likely to be main search targets.
    - Name length: shorter names tend to be the core fields (e.g. "title")
      vs. generated/auxiliary ones (e.g. "metadata_extracted_title_v2").

    Prefers text-type fields; falls back to keyword-type if no text fields
    exist.  Returns ["*"] as a last resort to match all fields.

    Args:
        field_specs: Mapping of field path to its index mapping metadata.
        limit: Maximum number of fields to return (default 6).

    Returns:
        A list of field names, ordered by relevance, up to *limit* entries.
    """
    # Collect text-type fields (exclude .keyword sub-fields)
    text_fields = [
        field
        for field, spec in field_specs.items()
        if spec.get("type") == "text" and not field.endswith(".keyword")
    ]
    # Fallback: keyword-type fields (exclude .keyword sub-fields)
    keyword_fields = [
        field
        for field, spec in field_specs.items()
        if spec.get("type") in _KEYWORD_FIELD_TYPES and not field.endswith(".keyword")
    ]

    def _score(field_name: str) -> tuple[int, int]:
        # Primary sort: nesting depth (fewer dots = more top-level = better)
        depth = field_name.count(".")
        # Secondary sort: name length (shorter = more likely a core field)
        return depth, len(field_name)

    ranked = sorted(text_fields, key=_score)
    if not ranked:
        ranked = sorted(keyword_fields, key=_score)
    selected = ranked[: max(1, limit)]
    return selected if selected else ["*"]


def _resolve_exact_field_from_hint(
    field_specs: dict[str, dict[str, str]],
    field_hint: str,
) -> tuple[str, str, bool]:
    """Resolve the best exact-match field and query mode from a hint.

    Returns ``(field_name, query_mode, case_insensitive)`` where query_mode is
    ``"term"`` or ``"match_phrase"``.
    """
    hint = _normalize_text(field_hint)
    if not hint:
        return "", "", False

    resolved_field, resolved_spec = _resolve_field_spec_for_doc_key(hint, field_specs)
    candidate_field = resolved_field or hint
    field_type = str((resolved_spec or {}).get("type", "")).strip().lower()
    normalizer = str((resolved_spec or {}).get("normalizer", "")).strip().lower()

    def _pick_keyword_subfield(base_field: str) -> tuple[str, dict[str, str]]:
        prefix = f"{base_field}."
        keyword_candidates: list[tuple[str, dict[str, str]]] = []
        for name, spec in field_specs.items():
            normalized_type = str(spec.get("type", "")).strip().lower()
            if not name.startswith(prefix):
                continue
            if normalized_type not in _KEYWORD_FIELD_TYPES:
                continue
            keyword_candidates.append((name, spec))
        if not keyword_candidates:
            return "", {}
        keyword_candidates.sort(
            key=lambda item: (
                0 if item[0].endswith(".keyword") else 1,
                item[0].count("."),
                len(item[0]),
                item[0],
            )
        )
        return keyword_candidates[0]

    if field_type == "text":
        keyword_field, keyword_spec = _pick_keyword_subfield(candidate_field)
        if keyword_field:
            keyword_normalizer = str(keyword_spec.get("normalizer", "")).strip().lower()
            return keyword_field, "term", "lower" in keyword_normalizer
        return candidate_field, "match_phrase", False

    if field_type in _EXACT_TERM_FIELD_TYPES:
        return candidate_field, "term", "lower" in normalizer

    if candidate_field.endswith(".keyword"):
        return candidate_field, "term", "lower" in normalizer

    keyword_field, keyword_spec = _pick_keyword_subfield(candidate_field)
    if keyword_field:
        keyword_normalizer = str(keyword_spec.get("normalizer", "")).strip().lower()
        return keyword_field, "term", "lower" in keyword_normalizer

    if field_type in {"match_only_text"}:
        return candidate_field, "match_phrase", False

    if field_type:
        return candidate_field, "term", "lower" in normalizer
    return "", "", False


def _resolve_semantic_runtime_hints(
    opensearch_client: OpenSearch,
    index_name: str,
    field_specs: dict[str, dict[str, str]],
) -> dict[str, str]:
    vector_fields = [
        field
        for field, spec in field_specs.items()
        if spec.get("type") == "knn_vector"
    ]
    vector_field = ""
    if vector_fields:
        preferred = sorted(
            vector_fields,
            key=lambda item: (
                0 if ("embedding" in item.lower() or "vector" in item.lower()) else 1,
                len(item),
                item,
            ),
        )
        vector_field = preferred[0]

    default_pipeline = ""
    search_pipeline = ""
    model_id = ""
    source_field = ""
    has_agentic_pipeline = False

    try:
        settings_response = opensearch_client.indices.get_settings(index=index_name)
        index_settings = next(iter(settings_response.values()), {})
        default_pipeline = _normalize_text(
            index_settings.get("settings", {}).get("index", {}).get("default_pipeline", "")
        )
        search_pipeline = _normalize_text(
            index_settings.get("settings", {}).get("index", {}).get("search", {}).get("default_pipeline", "")
        )
    except Exception:
        default_pipeline = ""
        search_pipeline = ""

    # Check if search pipeline is agentic
    if search_pipeline:
        try:
            pipeline_response = opensearch_client.transport.perform_request("GET", f"/_search/pipeline/{search_pipeline}")
            pipeline = pipeline_response.get(search_pipeline, {})
            request_processors = pipeline.get("request_processors", [])
            for processor in request_processors:
                if isinstance(processor, dict) and "agentic_query_translator" in processor:
                    has_agentic_pipeline = True
                    break
        except Exception:
            pass

    if default_pipeline:
        try:
            pipeline_response = opensearch_client.ingest.get_pipeline(id=default_pipeline)
            pipeline = pipeline_response.get(default_pipeline, {})
            processors = pipeline.get("processors", [])
            for processor in processors:
                if not isinstance(processor, dict):
                    continue
                embedding = processor.get("text_embedding")
                if not isinstance(embedding, dict):
                    continue
                candidate_model = _normalize_text(embedding.get("model_id", ""))
                field_map = embedding.get("field_map", {})
                if not candidate_model:
                    continue
                if isinstance(field_map, dict) and field_map:
                    if vector_field:
                        for source, target in field_map.items():
                            if _normalize_text(target) == vector_field:
                                model_id = candidate_model
                                source_field = _normalize_text(source)
                                break
                        if model_id:
                            break
                    if not model_id:
                        first_source, first_target = next(iter(field_map.items()))
                        model_id = candidate_model
                        source_field = _normalize_text(first_source)
                        if not vector_field:
                            vector_field = _normalize_text(first_target)
                else:
                    model_id = candidate_model
                    break
        except Exception:
            pass

    return {
        "vector_field": vector_field,
        "model_id": model_id,
        "default_pipeline": default_pipeline,
        "search_pipeline": search_pipeline,
        "has_agentic_pipeline": str(has_agentic_pipeline).lower(),
        "source_field": source_field,
    }


def _build_default_lexical_query(query: str, fields: list[str]) -> dict:
    body: dict[str, object] = {
        "query": query,
        "fields": fields or ["*"],
    }
    if any(field != "*" for field in fields):
        body["fuzziness"] = "AUTO"
    return {"multi_match": body}


def _build_default_lexical_body(query: str, size: int, fields: list[str]) -> dict:
    return {
        "size": size,
        "query": _build_default_lexical_query(query=query, fields=fields),
    }


def _coerce_structured_value(raw_value: str, field_type: str) -> object:
    normalized = _normalize_text(raw_value)
    lowered = normalized.lower()
    if field_type in _NUMERIC_FIELD_TYPES:
        if field_type in {"byte", "short", "integer", "long"}:
            try:
                return int(float(normalized))
            except Exception:
                return normalized
        try:
            return float(normalized)
        except Exception:
            return normalized

    if field_type == "boolean":
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return normalized


def _strip_wrapping_quotes(value_text: str) -> str:
    normalized = _normalize_text(value_text)
    if len(normalized) >= 2 and (
        (normalized[0] == normalized[-1] == '"')
        or (normalized[0] == normalized[-1] == "'")
    ):
        return _normalize_text(normalized[1:-1])
    return normalized


def _parse_structured_pairs(query_text: str) -> list[tuple[str, str]]:
    normalized_query = _normalize_text(query_text)
    if ":" not in normalized_query:
        return []

    pairs: list[tuple[str, str]] = []
    cursor = 0
    for match in _STRUCTURED_QUERY_PAIR_PATTERN.finditer(normalized_query):
        gap = normalized_query[cursor:match.start()].strip()
        if gap:
            return []

        field_name = _normalize_text(match.group("field"))
        value_text = _strip_wrapping_quotes(match.group("value"))
        if not field_name or not value_text:
            return []

        pairs.append((field_name, value_text))
        cursor = match.end()

    if not pairs:
        return []
    if normalized_query[cursor:].strip():
        return []
    return pairs


def _split_structured_clauses(
    clauses: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    text_clauses: list[dict[str, object]] = []
    filter_clauses: list[dict[str, object]] = []
    for clause in clauses:
        if "match_phrase" in clause:
            text_clauses.append(clause)
        else:
            filter_clauses.append(clause)
    return text_clauses, filter_clauses


def _parse_structured_clauses(
    query_text: str,
    suggestion_meta: dict[str, object] | None,
    field_specs: dict[str, dict[str, str]],
) -> tuple[list[dict[str, object]] | None, str]:
    parsed_pairs = _parse_structured_pairs(query_text)

    if not parsed_pairs:
        fallback_field = _normalize_text((suggestion_meta or {}).get("field", ""))
        fallback_value = _normalize_text((suggestion_meta or {}).get("value", ""))
        if fallback_field and fallback_value:
            parsed_pairs = [(fallback_field, fallback_value)]

    if not parsed_pairs:
        return None, "structured query missing field/value"

    clauses: list[dict[str, object]] = []
    for field_name, value_text in parsed_pairs:
        resolved_field, resolved_spec = _resolve_field_spec_for_doc_key(field_name, field_specs)
        target_field = resolved_field or field_name
        field_type = str(resolved_spec.get("type", "")).strip()

        if field_type == "text":
            clauses.append({"match_phrase": {target_field: value_text}})
            continue

        coerced_value = _coerce_structured_value(value_text, field_type)
        clauses.append({"term": {target_field: {"value": coerced_value}}})

    return clauses, ""


def _build_neural_clause(query: str, vector_field: str, model_id: str, size: int) -> dict:
    return {
        "neural": {
            vector_field: {
                "query_text": query,
                "model_id": model_id,
                "k": max(size, 10),
            }
        }
    }


def _search_ui_search(
    index_name: str,
    query_text: str,
    size: int = 10,
    debug: bool = False,
    search_intent: str = "",
    field_hint: str = "",
) -> dict:
    if not index_name:
        return {
            "error": "Missing index name.",
            "hits": [],
            "took_ms": 0,
            "query_mode": "",
            "capability": "",
            "used_semantic": False,
            "fallback_reason": "",
        }

    opensearch_client = _create_client()
    query = query_text.strip()
    capability = "manual" if query else ""
    query_mode = "match_all"
    used_semantic = False
    fallback_reason = ""
    executed_body: dict[str, object] = {"size": size, "query": {"match_all": {}}}

    field_specs = _extract_index_field_specs(opensearch_client, index_name)
    lexical_fields = _resolve_text_query_fields(field_specs)
    normalized_intent = _normalize_text(search_intent).lower()
    resolved_field_hint = _normalize_text(field_hint)

    suggestion_meta = _find_suggestion_meta(index_name, query) if query else None
    if query and normalized_intent == "autocomplete_selection" and resolved_field_hint:
        exact_field, exact_mode, case_insensitive = _resolve_exact_field_from_hint(
            field_specs=field_specs,
            field_hint=resolved_field_hint,
        )
        if exact_field and exact_mode:
            suggestion_meta = {
                "capability": "exact",
                "query_mode": exact_mode,
                "field": exact_field,
                "value": "",
                "case_insensitive": case_insensitive,
            }
    if suggestion_meta is not None:
        resolved_capability = _normalize_text(suggestion_meta.get("capability", "")).lower()
        if resolved_capability:
            capability = resolved_capability

    if query:
        runtime_hints = _resolve_semantic_runtime_hints(opensearch_client, index_name, field_specs)
        vector_field = runtime_hints.get("vector_field", "")
        model_id = runtime_hints.get("model_id", "")
        has_agentic_pipeline = runtime_hints.get("has_agentic_pipeline", "false") == "true"
        semantic_ready = bool(vector_field and model_id)
        lexical_query = _build_default_lexical_query(query=query, fields=lexical_fields)
        manual_structured_clauses: list[dict[str, object]] | None = None

        # Check if this is an agentic search query (multi-step question)
        if has_agentic_pipeline and capability == "manual":
            # Detect multi-step questions that should use agentic search
            query_lower = query.lower()
            agentic_indicators = [
                " and ",
                " or ",
                "why",
                "how",
                "what are",
                "show me",
                "find",
                "compare",
                "top",
                "best",
                "under",
                "over",
                "between",
                "?",
            ]
            if any(indicator in query_lower for indicator in agentic_indicators):
                # Use agentic search with correct query format
                executed_body = {
                    "size": size,
                    "query": {
                        "agentic": {
                            "query_text": query
                        }
                    }
                }
                query_mode = "agentic_search"
                capability = "agentic"
                used_semantic = True
                
                try:
                    response = opensearch_client.search(index=index_name, body=executed_body)
                    
                    # Debug: Log the raw response
                    if debug:
                        print(f"[DEBUG] Agentic search response: {json.dumps(response, indent=2)}")
                    
                    hits_out: list[dict] = []
                    for hit in response.get("hits", {}).get("hits", []):
                        source = hit.get("_source", {})
                        hits_out.append(
                            {
                                "id": hit.get("_id"),
                                "score": hit.get("_score"),
                                "preview": _search_ui_preview_text(source),
                                "source": source,
                            }
                        )
                    return {
                        "error": "",
                        "hits": hits_out,
                        "total": response.get("hits", {}).get("total", {}).get("value", len(hits_out)),
                        "took_ms": response.get("took", 0),
                        "query_mode": query_mode,
                        "capability": capability,
                        "used_semantic": used_semantic,
                        "fallback_reason": fallback_reason,
                        **({"query_body": executed_body} if debug else {}),
                    }
                except Exception as e:
                    fallback_reason = f"agentic search failed: {e}"
                    # Fall through to regular search logic

        if capability == "manual":
            manual_structured_clauses, _ = _parse_structured_clauses(
                query_text=query,
                suggestion_meta=None,
                field_specs=field_specs,
            )
            if manual_structured_clauses is not None:
                capability = "structured"

        # builds term / match_phrase DSL for exact-match queries
        if capability == "exact" and suggestion_meta is not None:
            exact_mode = _normalize_text(suggestion_meta.get("query_mode", ""))
            field = _normalize_text(suggestion_meta.get("field", ""))
            query_value = query.lower() if bool(suggestion_meta.get("case_insensitive", False)) else query
            if exact_mode == "term" and field:
                executed_body = {
                    "size": size,
                    "query": {
                        "term": {
                            field: {
                                "value": query_value,
                            }
                        }
                    },
                }
                query_mode = "exact_term"
            elif exact_mode == "match_phrase" and field:
                executed_body = {
                    "size": size,
                    "query": {
                        "match_phrase": {
                            field: query,
                        }
                    },
                }
                query_mode = "exact_match_phrase"
            else:
                executed_body = _build_default_lexical_body(query=query, size=size, fields=lexical_fields)
                query_mode = "exact_bm25_fallback"
        elif capability == "structured":
            structured_clauses = manual_structured_clauses
            structured_error = ""
            if structured_clauses is None:
                structured_clauses, structured_error = _parse_structured_clauses(
                    query_text=query,
                    suggestion_meta=suggestion_meta,
                    field_specs=field_specs,
                )
            if structured_clauses is None:
                fallback_reason = structured_error
                executed_body = _build_default_lexical_body(query=query, size=size, fields=lexical_fields)
                query_mode = "structured_bm25_fallback"
            else:
                text_clauses, filter_clauses = _split_structured_clauses(structured_clauses)
                bool_query: dict[str, object] = {}
                if text_clauses:
                    bool_query["must"] = text_clauses
                if filter_clauses:
                    bool_query["filter"] = filter_clauses
                executed_body = {
                    "size": size,
                    "query": {"bool": bool_query} if bool_query else {"match_all": {}},
                }
                query_mode = "structured_filter"
        elif capability == "autocomplete":
            field = _normalize_text((suggestion_meta or {}).get("field", ""))
            if field:
                executed_body = {
                    "size": size,
                    "query": {
                        "prefix": {
                            field: {
                                "value": query,
                            }
                        }
                    },
                }
                query_mode = "autocomplete_prefix"
            else:
                executed_body = _build_default_lexical_body(query=query, size=size, fields=lexical_fields)
                query_mode = "autocomplete_bm25_fallback"
                fallback_reason = "autocomplete field unresolved"
        elif capability == "fuzzy":
            field = _normalize_text((suggestion_meta or {}).get("field", ""))
            if field:
                executed_body = {
                    "size": size,
                    "query": {
                        "match": {
                            field: {
                                "query": query,
                                "fuzziness": "AUTO",
                            }
                        }
                    },
                }
                query_mode = "fuzzy_match"
            else:
                executed_body = _build_default_lexical_body(query=query, size=size, fields=lexical_fields)
                query_mode = "fuzzy_bm25_fallback"
                fallback_reason = "fuzzy field unresolved"
        elif capability in {"semantic", "combined", "manual"}:
            structured_clauses: list[dict[str, object]] | None = None
            if capability == "combined":
                structured_clauses, structured_error = _parse_structured_clauses(
                    query_text=query,
                    suggestion_meta=suggestion_meta,
                    field_specs=field_specs,
                )
                if structured_clauses is None:
                    fallback_reason = structured_error

            # builds hybrid query for combined/semantic capabilities
            # semantic is transformed to hybrid of lexical and neural queries
            if semantic_ready:
                neural_query = _build_neural_clause(
                    query=query,
                    vector_field=vector_field,
                    model_id=model_id,
                    size=size,
                )
                base_hybrid = {
                    "hybrid": {
                        "queries": [
                            lexical_query,
                            neural_query,
                        ]
                    }
                }
                used_semantic_now = True
                if capability == "combined" and structured_clauses is not None:
                    text_clauses, filter_clauses = _split_structured_clauses(structured_clauses)
                    if text_clauses:
                        must_clauses: list[dict[str, object]] = [base_hybrid]
                        must_clauses.extend(text_clauses)
                        bool_query: dict[str, object] = {"must": must_clauses}
                        if filter_clauses:
                            bool_query["filter"] = filter_clauses
                        executed_body = {
                            "size": size,
                            "query": {
                                "bool": bool_query,
                            },
                        }
                        query_mode = "combined_hybrid"
                    elif filter_clauses:
                        # Structured-only combined queries should not require lexical/semantic must clauses.
                        executed_body = {
                            "size": size,
                            "query": {
                                "bool": {
                                    "filter": filter_clauses,
                                }
                            },
                        }
                        query_mode = "combined_structured_filter"
                        used_semantic_now = False
                    else:
                        executed_body = {
                            "size": size,
                            "query": base_hybrid,
                        }
                        query_mode = "combined_hybrid"
                elif capability == "semantic":
                    executed_body = {
                        "size": size,
                        "query": base_hybrid,
                    }
                    query_mode = "semantic_hybrid"
                else:
                    executed_body = {
                        "size": size,
                        "query": base_hybrid,
                    }
                    query_mode = "hybrid_default"
                used_semantic = used_semantic_now
            else:
                missing_parts: list[str] = []
                if not vector_field:
                    missing_parts.append("vector_field")
                if not model_id:
                    missing_parts.append("model_id")
                missing_text = ", ".join(missing_parts) if missing_parts else "semantic runtime unavailable"
                fallback_reason = (
                    f"{fallback_reason}; semantic runtime unresolved ({missing_text})"
                    if fallback_reason
                    else f"semantic runtime unresolved ({missing_text})"
                )
                if capability == "combined" and structured_clauses is not None:
                    text_clauses, filter_clauses = _split_structured_clauses(structured_clauses)
                    bool_query: dict[str, object] = {}
                    if text_clauses:
                        must_clauses: list[dict[str, object]] = [lexical_query]
                        must_clauses.extend(text_clauses)
                        bool_query["must"] = must_clauses
                    if filter_clauses:
                        bool_query["filter"] = filter_clauses
                    executed_body = {
                        "size": size,
                        "query": {
                            "bool": bool_query if bool_query else {"must": [lexical_query]},
                        },
                    }
                    query_mode = "combined_lexical_filter"
                else:
                    executed_body = _build_default_lexical_body(query=query, size=size, fields=lexical_fields)
                    query_mode = f"{capability}_bm25_fallback"
        else:
            executed_body = _build_default_lexical_body(query=query, size=size, fields=lexical_fields)
            query_mode = "bm25_default"
    else:
        executed_body = {"size": size, "query": {"match_all": {}}}

    try:
        response = opensearch_client.search(index=index_name, body=executed_body)
    except Exception as query_error:
        if query:
            fallback_reason = (
                f"{fallback_reason}; primary query failed: {query_error}"
                if fallback_reason
                else f"primary query failed: {query_error}"
            )
            executed_body = _build_default_lexical_body(query=query, size=size, fields=lexical_fields)
            response = opensearch_client.search(index=index_name, body=executed_body)
            used_semantic = False
            query_mode = f"{query_mode}_fallback_bm25"
        else:
            raise

    hits_out: list[dict] = []
    for hit in response.get("hits", {}).get("hits", []):
        source = hit.get("_source", {})
        hits_out.append(
            {
                "id": hit.get("_id"),
                "score": hit.get("_score"),
                "preview": _search_ui_preview_text(source),
                "source": source,
            }
        )
    return {
        "error": "",
        "hits": hits_out,
        "total": response.get("hits", {}).get("total", {}).get("value", len(hits_out)),
        "took_ms": response.get("took", 0),
        "query_mode": query_mode,
        "capability": capability,
        "used_semantic": used_semantic,
        "fallback_reason": fallback_reason,
        **({"query_body": executed_body} if debug else {}),
    }


def _resolve_default_index(preferred_index: str = "") -> str:
    if preferred_index:
        return preferred_index
    try:
        opensearch_client = _create_client()
        indices = opensearch_client.cat.indices(format="json")
        names = [
            item.get("index", "")
            for item in indices
            if item.get("index") and not item.get("index", "").startswith(".")
        ]
        if names:
            return names[0]
    except Exception:
        pass
    return ""


def _search_ui_public_url() -> str:
    public_host = "localhost" if SEARCH_UI_HOST in {"0.0.0.0", "::"} else SEARCH_UI_HOST
    return f"http://{public_host}:{SEARCH_UI_PORT}"


def _coerce_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _format_epoch(epoch: float) -> str:
    if epoch <= 0:
        return "-"
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))
    except Exception:
        return "-"


def _format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes > 0:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _search_ui_status_snapshot() -> dict[str, object]:
    now = time.time()
    lock = _read_ui_lock()

    status: dict[str, object] = {
        "status": "stopped",
        "url": _search_ui_public_url(),
        "pid": None,
        "last_active_epoch": 0.0,
        "last_active": "-",
        "idle_timeout_seconds": SEARCH_UI_IDLE_TIMEOUT_SECONDS,
        "auto_stop_in_seconds": None,
        "owned": False,
        "note": "UI server is not running.",
    }

    if not lock:
        listeners = _list_listener_pids_on_ui_port()
        if listeners:
            status["note"] = (
                f"Port {SEARCH_UI_PORT} is occupied by pid {listeners[0]} "
                "(ownership check failed)."
            )
        return status

    pid = _get_lock_pid(lock)
    owned, owned_reason = _is_owned_ui_process(lock)

    last_active_epoch = _coerce_float(lock.get("last_active_epoch"), 0.0)
    idle_timeout_seconds = max(
        60,
        _coerce_int(lock.get("idle_timeout_seconds"), SEARCH_UI_IDLE_TIMEOUT_SECONDS),
    )
    auto_stop_in_seconds: int | None = None
    if last_active_epoch > 0:
        auto_stop_in_seconds = max(0, idle_timeout_seconds - int(now - last_active_epoch))

    status.update(
        {
            "pid": pid if pid > 0 else None,
            "instance_id": str(lock.get("instance_id", "")).strip(),
            "last_active_epoch": last_active_epoch,
            "last_active": _format_epoch(last_active_epoch),
            "idle_timeout_seconds": idle_timeout_seconds,
            "auto_stop_in_seconds": auto_stop_in_seconds,
            "owned": owned,
        }
    )

    if owned:
        status["status"] = "running"
        status["note"] = "Owned UI server is running."
        return status

    if owned_reason == "lock pid not running":
        status["note"] = "UI lock file is stale."
    else:
        status["note"] = f"UI lock ownership check failed: {owned_reason}."
    return status


def _format_ui_status_line(status: dict[str, object]) -> str:
    state = str(status.get("status", "stopped"))
    pid = status.get("pid")
    last_active = str(status.get("last_active", "-"))
    auto_stop = _format_duration(status.get("auto_stop_in_seconds"))
    return (
        f"UI server status: {state} | pid: {pid if pid else '-'} | "
        f"last_active: {last_active} | auto-stop in: {auto_stop}"
    )


def _terminate_process(pid: int, timeout_seconds: float = 3.0) -> tuple[bool, bool]:
    """Terminate process by pid. Returns (stopped, used_sigkill)."""
    import signal

    if pid <= 0:
        return False, False
    if not _is_pid_running(pid):
        return True, False

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return (not _is_pid_running(pid), False)

    deadline = time.time() + max(0.1, timeout_seconds)
    while time.time() < deadline:
        if not _is_pid_running(pid):
            return True, False
        time.sleep(0.1)

    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return not _is_pid_running(pid), False

    deadline = time.time() + 1.0
    while time.time() < deadline:
        if not _is_pid_running(pid):
            return True, True
        time.sleep(0.05)
    return False, True


def _resolve_search_ui_asset(path: str) -> Path | None:
    normalized = path.strip()
    if normalized in {"", "/"}:
        normalized = "/index.html"

    relative_path = normalized.lstrip("/")
    candidate = (SEARCH_UI_STATIC_DIR / relative_path).resolve()
    root = SEARCH_UI_STATIC_DIR.resolve()

    try:
        candidate.relative_to(root)
    except ValueError:
        return None

    if not candidate.exists() or not candidate.is_file():
        return None
    return candidate


def _search_ui_content_type(path: Path) -> str:
    return _SEARCH_UI_CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


class _SearchUIRequestHandler(BaseHTTPRequestHandler):
    def _write_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_file(self, path: Path, status: int = 200) -> None:
        payload = path.read_bytes()
        self.send_response(status)
        self.send_header("Content-Type", _search_ui_content_type(path))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        _maybe_reload_ui_state()
        _record_ui_activity()
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/api/health":
            status = _search_ui_status_snapshot()
            backend = _get_backend_info()
            self._write_json(
                {
                    "ok": True,
                    "service": _SEARCH_UI_SERVICE_NAME,
                    "default_index": _search_ui.default_index,
                    "instance_id": _ui_instance_id or status.get("instance_id", ""),
                    "pid": os.getpid(),
                    "status": status.get("status", "running"),
                    "last_active_epoch": status.get("last_active_epoch", 0.0),
                    "idle_timeout_seconds": status.get("idle_timeout_seconds", SEARCH_UI_IDLE_TIMEOUT_SECONDS),
                    "auto_stop_in_seconds": status.get("auto_stop_in_seconds"),
                    "backend_type": backend["backend_type"],
                    "endpoint": backend["endpoint"],
                    "connected": backend["connected"],
                }
            )
            return

        if parsed.path == "/api/config":
            backend = _get_backend_info()
            self._write_json({
                "default_index": _search_ui.default_index,
                "backend_type": backend["backend_type"],
                "endpoint": backend["endpoint"],
                "connected": backend["connected"],
            })
            return

        if parsed.path == "/api/suggestions":
            index_name = params.get("index", [""])[0] or _search_ui.default_index
            suggestions, suggestion_meta = _search_ui_suggestions(index_name, max_count=6)
            self._write_json(
                {
                    "suggestions": suggestions,
                    "suggestion_meta": suggestion_meta,
                    "index": index_name,
                }
            )
            return

        if parsed.path == "/api/autocomplete":
            index_name = params.get("index", [""])[0] or _search_ui.default_index
            prefix_text = params.get("q", [""])[0]
            field_name = params.get("field", [""])[0]
            try:
                size = int(params.get("size", ["8"])[0])
            except ValueError:
                size = 8
            size = max(1, min(size, 20))
            result = _search_ui_autocomplete(
                index_name=index_name,
                prefix_text=prefix_text,
                size=size,
                preferred_field=field_name,
            )
            self._write_json(result)
            return

        if parsed.path == "/api/search":
            index_name = params.get("index", [""])[0] or _search_ui.default_index
            query_text = params.get("q", [""])[0]
            search_intent = params.get("intent", [""])[0]
            field_hint = params.get("field", [""])[0]
            debug_param = params.get("debug", ["0"])[0].strip().lower()
            debug_mode = debug_param in {"1", "true", "yes", "on"}
            try:
                size = int(params.get("size", ["20"])[0])
            except ValueError:
                size = 20
            size = max(1, min(size, 50))

            try:
                result = _search_ui_search(
                    index_name=index_name,
                    query_text=query_text,
                    size=size,
                    debug=debug_mode,
                    search_intent=search_intent,
                    field_hint=field_hint,
                )
                self._write_json(result)
            except Exception as e:
                self._write_json(
                    {
                        "error": str(e),
                        "hits": [],
                        "took_ms": 0,
                        "query_mode": "",
                        "capability": "",
                        "used_semantic": False,
                        "fallback_reason": "",
                    },
                    status=500,
                )
            return

        static_asset = _resolve_search_ui_asset(parsed.path)
        if static_asset is not None:
            self._write_file(static_asset)
            return

        self._write_json({"error": "Not found"}, status=404)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


def _kill_stale_ui_on_port() -> bool:
    """Kill stale UI process only when lock ownership verification passes."""
    lock = _read_ui_lock()
    owned, _ = _is_owned_ui_process(lock)
    if not owned or not lock:
        return False

    pid = _get_lock_pid(lock)
    listeners = _list_listener_pids_on_ui_port()
    if listeners and pid not in listeners:
        return False

    stopped, _ = _terminate_process(pid)
    if stopped:
        _remove_ui_lock()
        return True
    return False


def _stop_ui_process_on_port() -> bool:
    """Stop the current listener on the configured UI port."""
    listeners = _list_listener_pids_on_ui_port()
    if not listeners:
        return False

    stopped_any = False
    for pid in listeners:
        stopped, _ = _terminate_process(pid)
        if stopped:
            stopped_any = True
    if stopped_any:
        _remove_ui_lock()
    return stopped_any


def _ensure_search_ui_server(preferred_index: str = "") -> dict[str, object]:
    with _search_ui.lock:
        if not SEARCH_UI_STATIC_DIR.exists():
            raise RuntimeError(f"Search UI static directory not found: {SEARCH_UI_STATIC_DIR}")
        if _resolve_search_ui_asset("/index.html") is None:
            raise RuntimeError("Search UI entry file missing: index.html")

        resolved_index = _resolve_default_index(preferred_index)
        if resolved_index:
            _search_ui.default_index = resolved_index

        _write_ui_state()

        _cleanup_stale_ui_lock()

        lock = _read_ui_lock()
        owned, _ = _is_owned_ui_process(lock)
        if owned and lock:
            instance_id = str(lock.get("instance_id", "")).strip()
            if _is_ui_server_responsive(expected_instance_id=instance_id):
                status = _search_ui_status_snapshot()
                status["note"] = "Reused existing owned UI server."
                return {
                    "url": _search_ui_public_url(),
                    "action": "reused",
                    "status": status,
                }

        if _is_ui_server_responsive() and not _stop_ui_process_on_port():
            listeners = _list_listener_pids_on_ui_port()
            pid_note = f"pid {listeners[0]}" if listeners else "an existing process"
            raise RuntimeError(
                f"Search UI port {SEARCH_UI_PORT} is already serving requests via {pid_note}. "
                "Failed to stop that process automatically."
            )

        _kill_stale_ui_on_port()

        listeners = _list_listener_pids_on_ui_port()
        if listeners:
            _stop_ui_process_on_port()
            listeners = _list_listener_pids_on_ui_port()
            if listeners:
                # Last resort: force-kill any listener whose command looks like
                # our UI server module.  This handles orphaned processes from
                # previous MCP sessions where the lock file was already cleaned
                # but the process survived.
                for pid in list(listeners):
                    cmd = _get_process_command(pid)
                    if cmd and (_UI_SERVER_MODULE in cmd or _UI_SERVER_SCRIPT_NAME in cmd):
                        _terminate_process(pid)
                listeners = _list_listener_pids_on_ui_port()

            if listeners:
                conflict_pid = listeners[0]
                conflict_cmd = _get_process_command(conflict_pid)
                raise RuntimeError(
                    f"Search UI port {SEARCH_UI_PORT} is in use by pid {conflict_pid} "
                    f"({conflict_cmd or 'unknown command'}). Failed to stop that process automatically."
                )

        instance_id = uuid.uuid4().hex
        _ensure_ui_runtime_dir()

        # Ensure the subprocess can locate the opensearch_orchestrator package
        # even when sys.executable points to an isolated venv (e.g. uv-managed
        # MCP environment) that doesn't have the package installed.
        _project_root = str(Path(__file__).resolve().parent.parent)
        _sub_env = os.environ.copy()
        _existing_pp = _sub_env.get("PYTHONPATH", "")
        _sub_env["PYTHONPATH"] = (
            f"{_project_root}{os.pathsep}{_existing_pp}" if _existing_pp else _project_root
        )

        subprocess.Popen(
            [
                sys.executable,
                "-m",
                _UI_SERVER_MODULE,
                "--instance-id",
                instance_id,
                "--idle-timeout-seconds",
                str(SEARCH_UI_IDLE_TIMEOUT_SECONDS),
            ],
            env=_sub_env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )

        _search_ui.server = None
        _search_ui.thread = None

        started = False
        for _ in range(24):
            time.sleep(0.25)
            if _is_ui_server_responsive(expected_instance_id=instance_id):
                started = True
                break

        if not started:
            raise RuntimeError(
                f"Search UI failed to become healthy on port {SEARCH_UI_PORT}. "
                "Check whether another process is occupying the port, then retry."
            )

        status = _search_ui_status_snapshot()
        status["note"] = "Started new owned UI server."
        return {
            "url": _search_ui_public_url(),
            "action": "started",
            "status": status,
        }


def _start_search_ui_server(preferred_index: str = "") -> str:
    return str(_ensure_search_ui_server(preferred_index).get("url", _search_ui_public_url()))

PRETRAINED_MODELS = {
    "huggingface/cross-encoders/ms-marco-MiniLM-L-12-v2": "1.0.2",
    "huggingface/cross-encoders/ms-marco-MiniLM-L-6-v2": "1.0.2",
    "huggingface/sentence-transformers/all-MiniLM-L12-v2": "1.0.2",
    "huggingface/sentence-transformers/all-MiniLM-L6-v2": "1.0.2",
    "huggingface/sentence-transformers/all-distilroberta-v1": "1.0.2",
    "huggingface/sentence-transformers/all-mpnet-base-v2": "1.0.2",
    "huggingface/sentence-transformers/distiluse-base-multilingual-cased-v1": "1.0.2",
    "huggingface/sentence-transformers/msmarco-distilbert-base-tas-b": "1.0.3",
    "huggingface/sentence-transformers/multi-qa-MiniLM-L6-cos-v1": "1.0.2",
    "huggingface/sentence-transformers/multi-qa-mpnet-base-dot-v1": "1.0.2",
    "huggingface/sentence-transformers/paraphrase-MiniLM-L3-v2": "1.0.2",
    "huggingface/sentence-transformers/paraphrase-mpnet-base-v2": "1.0.1",
    "huggingface/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": "1.0.2",
    "amazon/neural-sparse/opensearch-neural-sparse-encoding-doc-v1": "1.0.1",
    "amazon/neural-sparse/opensearch-neural-sparse-encoding-doc-v2-distill": "1.0.0",
    "amazon/neural-sparse/opensearch-neural-sparse-encoding-doc-v2-mini": "1.0.0",
    "amazon/neural-sparse/opensearch-neural-sparse-encoding-v2-distill": "1.0.0",
    "amazon/neural-sparse/opensearch-neural-sparse-encoding-doc-v3-distill": "1.0.0",
    "amazon/neural-sparse/opensearch-neural-sparse-encoding-doc-v3-gte": "1.0.0",
    "amazon/neural-sparse/opensearch-neural-sparse-tokenizer-v1": "1.0.1",
    "amazon/neural-sparse/opensearch-neural-sparse-tokenizer-multilingual-v1": "1.0.0",
    "amazon/sentence-highlighting/opensearch-semantic-highlighter-v1": "1.0.0",
    "amazon/metrics_correlation": "1.0.0b2"
}

def set_ml_settings(opensearch_client: OpenSearch | None = None) -> None:
    """Set the ML settings for the OpenSearch cluster.
    
    Returns:
        str: Success message or error.
    """
    if opensearch_client is None:
        opensearch_client = _create_client()

    body = {
        "persistent":{
            "plugins.ml_commons.native_memory_threshold": 95,
            "plugins.ml_commons.only_run_on_ml_node": False,
            "plugins.ml_commons.allow_registering_model_via_url" : True,
            "plugins.ml_commons.model_access_control_enabled" : True,
            "plugins.ml_commons.trusted_connector_endpoints_regex": [
                "^https://runtime\\.sagemaker\\..*[a-z0-9-]\\.amazonaws\\.com/.*$",
                "^https://bedrock-runtime\\..*[a-z0-9-]\\.amazonaws\\.com/.*$"
            ]
        }
    }
    opensearch_client.transport.perform_request("PUT", "/_cluster/settings", body=body)



def create_index(
    index_name: str,
    body: dict = None,
    replace_if_exists: bool = True,
    sample_doc_json: str = "",
    source_local_file: str = "",
    source_index_name: str = "",
) -> str:
    """Create an OpenSearch index with the specified configuration.

    Args:
        index_name: The name of the index to create.
        body: The configuration body for the index (settings and mappings).
        replace_if_exists: Delete and recreate the index if it already exists.
        sample_doc_json: JSON string of a single sample document for type guardrails.
        source_local_file: Path to the local file the sample was loaded from.
        source_index_name: Localhost OpenSearch index name used as sample source.

    Returns:
        str: Success message or error.
    """
    if body is None:
        body = {}
    if not isinstance(body, dict):
        body = {}
    knn_engine_updates = _normalize_knn_method_engines(body)

    sample_docs = _load_sample_docs(
        limit=200,
        sample_doc_json=sample_doc_json,
        source_local_file=source_local_file,
        source_index_name=source_index_name,
    )
    requested_field_types = _extract_declared_field_types_from_index_body(body)
    requested_boolean_policy_violations = _collect_boolean_typing_policy_violations(
        field_types=requested_field_types,
        sample_docs=sample_docs,
    )
    if requested_boolean_policy_violations:
        return (
            "Error: preflight failed. "
            + " ".join(requested_boolean_policy_violations)
        )

    def _index_exists(opensearch_client: OpenSearch, target_index: str) -> bool:
        try:
            exists = opensearch_client.indices.exists(index=target_index)
            if isinstance(exists, bool):
                return exists
            return bool(exists)
        except Exception:
            try:
                mapping = opensearch_client.indices.get_mapping(index=target_index)
                return isinstance(mapping, dict) and bool(mapping)
            except Exception:
                return False

    try:
        opensearch_client = _create_client()
    except Exception as e:
        return f"Failed to create index '{index_name}': {e}"

    existed_before = _index_exists(opensearch_client, index_name)
    if existed_before and replace_if_exists:
        try:
            opensearch_client.indices.delete(index=index_name, ignore=[404])
        except Exception as e:
            return f"Failed to recreate index '{index_name}': failed to delete existing index: {e}"

    if existed_before and not replace_if_exists:
        existing_field_specs = _extract_index_field_specs(opensearch_client, index_name)
        existing_field_types = {
            field_name: str(spec.get("type", "")).strip().lower()
            for field_name, spec in existing_field_specs.items()
            if isinstance(spec, dict)
        }

        mapping_mismatches = _collect_requested_vs_existing_field_type_mismatches(
            requested_field_types=requested_field_types,
            existing_field_types=existing_field_types,
        )
        if mapping_mismatches:
            existing_boolean_policy_violations = _collect_boolean_typing_policy_violations(
                field_types=existing_field_types,
                sample_docs=sample_docs,
            )
            policy_note = ""
            if existing_boolean_policy_violations:
                policy_note = (
                    " This violates producer-driven boolean typing policy. "
                    + " ".join(existing_boolean_policy_violations)
                )
            return (
                f"Error: Index '{index_name}' already exists with mappings incompatible with the requested schema. "
                "Delete and recreate the index, or use replace_if_exists=true. "
                + " ".join(mapping_mismatches)
                + policy_note
            )
        return f"Index '{index_name}' already exists."

    try:
        opensearch_client.indices.create(index=index_name, body=body)
        normalized_note = ""
        if knn_engine_updates:
            normalized_note = (
                " Normalized k-NN method engine settings: "
                + "; ".join(knn_engine_updates)
                + "."
            )
        if existed_before and replace_if_exists:
            return f"Index '{index_name}' recreated successfully.{normalized_note}"
        return f"Index '{index_name}' created successfully.{normalized_note}"
    except Exception as e:
        error = str(e)
        lowered = error.lower()
        if "resource_already_exists_exception" in lowered or "already exists" in lowered:
            if replace_if_exists:
                try:
                    opensearch_client.indices.delete(index=index_name, ignore=[404])
                    opensearch_client.indices.create(index=index_name, body=body)
                    normalized_note = ""
                    if knn_engine_updates:
                        normalized_note = (
                            " Normalized k-NN method engine settings: "
                            + "; ".join(knn_engine_updates)
                            + "."
                        )
                    return f"Index '{index_name}' recreated successfully.{normalized_note}"
                except Exception as recreate_error:
                    return f"Failed to recreate index '{index_name}': {recreate_error}"

            existing_field_specs = _extract_index_field_specs(opensearch_client, index_name)
            existing_field_types = {
                field_name: str(spec.get("type", "")).strip().lower()
                for field_name, spec in existing_field_specs.items()
                if isinstance(spec, dict)
            }

            mapping_mismatches = _collect_requested_vs_existing_field_type_mismatches(
                requested_field_types=requested_field_types,
                existing_field_types=existing_field_types,
            )
            if mapping_mismatches:
                existing_boolean_policy_violations = _collect_boolean_typing_policy_violations(
                    field_types=existing_field_types,
                    sample_docs=sample_docs,
                )
                policy_note = ""
                if existing_boolean_policy_violations:
                    policy_note = (
                        " This violates producer-driven boolean typing policy. "
                        + " ".join(existing_boolean_policy_violations)
                    )
                return (
                    f"Error: Index '{index_name}' already exists with mappings incompatible with the requested schema. "
                    "Delete and recreate the index, or use replace_if_exists=true. "
                    + " ".join(mapping_mismatches)
                    + policy_note
                )
            return f"Index '{index_name}' already exists."
        return f"Failed to create index '{index_name}': {e}"


def create_bedrock_embedding_model(model_name: str) -> str:
    """Create a Bedrock embedding model with the specified configuration.
    
    Args:
        model_name: The Bedrock model ID (e.g., "amazon.titan-embed-text-v2:0").
        
    Returns:
        str: The model ID of the created and deployed model, or error message.
    """
    if model_name != "amazon.titan-embed-text-v2:0":
        return "Error: Only amazon.titan-embed-text-v2:0 is supported for now."
    
    region = os.getenv("AWS_REGION", "us-east-1")
    access_key = os.getenv("AWS_ACCESS_KEY_ID")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    session_token = os.getenv("AWS_SESSION_TOKEN")

    if not access_key or not secret_key:
        return "Error: AWS credentials (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY) are missing from environment variables."

    credentials = {
        "access_key": access_key,
        "secret_key": secret_key
    }
    if session_token:
        credentials["session_token"] = session_token

    # 1. Create Connector
    connector_body = {
        "name": f"Bedrock Connector for {model_name}",
        "description": f"Connector for Bedrock model {model_name}",
        "version": 1,
        "protocol": "aws_sigv4",
        "parameters": {
            "region": region,
            "service_name": "bedrock"
        },
        "credential": credentials,
        "actions": [
            {
                "action_type": "predict",
                "method": "POST",
                "url": f"https://bedrock-runtime.{region}.amazonaws.com/model/{model_name}/invoke",
                "headers": {
                    "content-type": "application/json",
                    "x-amz-content-sha256": "required"
                },
                "request_body": "{ \"inputText\": \"${parameters.inputText}\", \"embeddingTypes\": [\"float\"] }",
                "pre_process_function": "connector.pre_process.bedrock.embedding",
                "post_process_function": "connector.post_process.bedrock_v2.embedding.float"
            }
        ]
    }
    
    try:
        opensearch_client = _create_client()
        set_ml_settings(opensearch_client)
        response = opensearch_client.transport.perform_request("POST", "/_plugins/_ml/connectors/_create", body=connector_body)
        connector_id = response.get("connector_id")
        if not connector_id:
            return f"Failed to create connector: {response}"
        print(f"Connector created: {connector_id}", file=sys.stderr)

        # 2. Register Model
        register_body = {
            "name": f"Bedrock Model {model_name}",
            "function_name": "remote",
            "description": f"Bedrock embedding model {model_name}",
            "connector_id": connector_id
        }
        response = opensearch_client.transport.perform_request("POST", "/_plugins/_ml/models/_register", body=register_body)
        task_id = response.get("task_id")
        print(f"Model registration task started: {task_id}", file=sys.stderr)
        
        # Poll for model ID
        model_id = None
        for _ in range(100):
            task_res = opensearch_client.transport.perform_request("GET", f"/_plugins/_ml/tasks/{task_id}")
            state = task_res.get("state")
            if state == "COMPLETED":
                model_id = task_res.get("model_id")
                break
            elif state == "FAILED":
                return _format_model_failure_message("registration", task_res.get("error"))
            time.sleep(2)
            
        if not model_id:
            return "Model registration timed out or failed."
        print(f"Model registered: {model_id}", file=sys.stderr)

        # 3. Deploy Model
        response = opensearch_client.transport.perform_request("POST", f"/_plugins/_ml/models/{model_id}/_deploy")
        deploy_task_id = response.get("task_id")
        print(f"Model deployment task started: {deploy_task_id}", file=sys.stderr)
        
        # Poll for deployment
        for _ in range(100):
            task_res = opensearch_client.transport.perform_request("GET", f"/_plugins/_ml/tasks/{deploy_task_id}")
            state = task_res.get("state")
            if state == "COMPLETED":
                return f"Model '{model_name}' (ID: {model_id}) created and deployed successfully."
            elif state == "FAILED":
                return _format_model_failure_message("deployment", task_res.get("error"))
            time.sleep(2)

        return f"Model deployment timed out. Model ID: {model_id}"

    except Exception as e:
        return f"Error creating Bedrock model: {e}"



def create_and_attach_pipeline(
    pipeline_name: str,
    pipeline_body: dict | None = None,
    index_name: str = "",
    pipeline_type: str = "ingest",
    replace_if_exists: bool = True,
    is_hybrid_search: bool = False,
    hybrid_weights: list[float] | None = None,
    body: dict | None = None,
) -> str:
    """Create a pipeline (ingest or search) and attach it to an index.

    Usage Examples:
    1. Create and attach an ingest pipeline with dense and sparse vector embedding
    create_and_attach_pipeline("my_pipeline", {
        "processors": "processors" : [
            {
                "text_embedding": {
                    "model_id": "WKvvsJsB93bus0FT62-y",
                    "field_map": {
                        "text": "dense_embedding"
                    }
                }
            },
            {
                "sparse_encoding": {
                    "model_id": "wOB545cBlvLkxPs_jDLT",
                    "field_map": {
                        "text": "sparse_embedding"
                    }
                }
            }
        ]
    }, "my_index", "ingest")


    Args:
        pipeline_name: The name of the pipeline to create.
        pipeline_body: The configuration of the pipeline (processors, etc.).
            Optional for hybrid search pipelines where default normalization can be generated.
        index_name: The name of the index to attach the pipeline to.
        pipeline_type: The type of pipeline, either 'ingest' or 'search'. Defaults to 'ingest'.
        replace_if_exists: Delete and recreate pipeline when it already exists.
        is_hybrid_search: Whether the search pipeline is for hybrid lexical+semantic score blending.
        hybrid_weights: Weight array in [lexical, semantic] order.
        body: Backward-compatible alias for `pipeline_body`.

    Returns:
        str: Success message or error.
    """
    resolved_pipeline_body = pipeline_body if pipeline_body is not None else body
    if resolved_pipeline_body is None:
        resolved_pipeline_body = {}
    if not isinstance(resolved_pipeline_body, dict):
        return "Error: pipeline_body must be a JSON object."

    resolved_index_name = str(index_name or "").strip()
    if not resolved_index_name:
        return "Error: index_name is required."

    if pipeline_type == "ingest" and not resolved_pipeline_body:
        return (
            "Error: pipeline_body is required for ingest pipelines. "
            "Provide processors/field_map configuration."
        )

    def _extract_pipeline_source_fields(body: dict) -> list[str]:
        if not isinstance(body, dict):
            return []

        processors = body.get("processors")
        if not isinstance(processors, list):
            return []

        source_fields: list[str] = []
        seen: set[str] = set()
        for processor in processors:
            if not isinstance(processor, dict):
                continue
            for processor_config in processor.values():
                if not isinstance(processor_config, dict):
                    continue
                field_map = processor_config.get("field_map")
                if not isinstance(field_map, dict):
                    continue
                for source_field in field_map.keys():
                    name = str(source_field).strip().lower()
                    if not name or name in seen:
                        continue
                    seen.add(name)
                    source_fields.append(name)
        return source_fields

    def _extract_mapped_fields(opensearch_client: OpenSearch, target_index: str) -> dict[str, str]:
        mapped_fields: dict[str, str] = {}
        response = opensearch_client.indices.get_mapping(index=target_index)
        if not isinstance(response, dict) or not response:
            return mapped_fields

        selected_mapping = response.get(target_index)
        if not isinstance(selected_mapping, dict):
            selected_mapping = next(iter(response.values()), {})
        mappings = selected_mapping.get("mappings", {}) if isinstance(selected_mapping, dict) else {}
        properties = mappings.get("properties", {}) if isinstance(mappings, dict) else {}

        def _walk(props: dict, prefix: str = "") -> None:
            for field_name, config in props.items():
                if not isinstance(config, dict):
                    continue
                full_name = f"{prefix}.{field_name}" if prefix else field_name
                field_type = config.get("type")
                if isinstance(field_type, str):
                    mapped_fields[full_name] = field_type
                nested_props = config.get("properties")
                if isinstance(nested_props, dict):
                    _walk(nested_props, full_name)

        if isinstance(properties, dict):
            _walk(properties)
        return mapped_fields

    def _choose_best_source_field(requested_field: str, mapped_fields: dict[str, str]) -> str:
        # Strict matching only: exact match, case-insensitive full path, then case-insensitive leaf-name.
        # No heuristic fallback to arbitrary ranked candidates.
        requested = requested_field.strip()
        if not requested:
            return ""
        if requested in mapped_fields:
            return requested

        requested_lower = requested.lower()
        for candidate in mapped_fields.keys():
            if candidate.lower() == requested_lower:
                return candidate

        leaf_matches = [
            candidate for candidate in mapped_fields.keys()
            if candidate.split(".")[-1].lower() == requested_lower
        ]
        if len(leaf_matches) == 1:
            return leaf_matches[0]
        return ""

    def _normalize_ingest_pipeline_body(body: dict, mapped_fields: dict[str, str]) -> tuple[dict, list[str], list[str]]:
        if not isinstance(body, dict):
            return body, [], []

        normalized = json.loads(json.dumps(body))
        processors = normalized.get("processors")
        if not isinstance(processors, list):
            return normalized, [], []

        remap_notes: list[str] = []
        unresolved: list[str] = []
        for processor in processors:
            if not isinstance(processor, dict):
                continue

            for processor_name, processor_config in processor.items():
                if not isinstance(processor_config, dict):
                    continue
                field_map = processor_config.get("field_map")
                if not isinstance(field_map, dict) or not field_map:
                    continue

                rewritten_map: dict[str, str] = {}
                for source_field, target_field in field_map.items():
                    source_text = str(source_field).strip()
                    chosen_source = _choose_best_source_field(source_text, mapped_fields)
                    if not chosen_source:
                        unresolved.append(f"{processor_name}: '{source_text}'")
                        continue
                    rewritten_map[chosen_source] = target_field
                    if chosen_source.lower() != source_text.lower():
                        remap_notes.append(
                            f"{processor_name}: '{source_text}' -> '{chosen_source}'"
                        )

                processor_config["field_map"] = rewritten_map

        return normalized, remap_notes, unresolved

    def _resolve_hybrid_weights(weights: list[float] | None) -> tuple[list[float], str]:
        default_weights = [0.5, 0.5]
        if weights is None:
            return default_weights, ""
        if not isinstance(weights, list) or len(weights) != 2:
            return default_weights, (
                "hybrid_weights must be a list with exactly two numeric values "
                "in [lexical, semantic] order."
            )
        try:
            lexical = float(weights[0])
            semantic = float(weights[1])
        except Exception:
            return default_weights, (
                "hybrid_weights must be numeric in [lexical, semantic] order."
            )
        if lexical < 0 or semantic < 0:
            return default_weights, "hybrid_weights values must be non-negative."
        total = lexical + semantic
        if total <= 0:
            return default_weights, "hybrid_weights sum must be greater than zero."
        return [lexical / total, semantic / total], ""

    def _build_default_hybrid_search_pipeline_body(weights: list[float]) -> dict:
        return {
            "phase_results_processors": [
                {
                    "normalization-processor": {
                        "normalization": {"technique": "min_max"},
                        "combination": {
                            "technique": "arithmetic_mean",
                            "parameters": {"weights": weights},
                        },
                    }
                }
            ]
        }

    def _find_normalization_processor(body: dict) -> dict[str, object] | None:
        processors = body.get("phase_results_processors")
        if not isinstance(processors, list):
            return None
        for processor in processors:
            if not isinstance(processor, dict):
                continue
            normalization = processor.get("normalization-processor")
            if isinstance(normalization, dict):
                return normalization
        return None

    def _normalize_hybrid_search_pipeline_body(body: dict, weights: list[float]) -> tuple[dict, str]:
        if not isinstance(body, dict) or not body:
            return _build_default_hybrid_search_pipeline_body(weights), ""

        normalized = json.loads(json.dumps(body))
        normalization_processor = _find_normalization_processor(normalized)
        if normalization_processor is None:
            return {}, (
                "Hybrid search pipeline must include 'phase_results_processors' "
                "with a 'normalization-processor' entry."
            )

        normalization = normalization_processor.get("normalization")
        if not isinstance(normalization, dict):
            normalization = {}
        technique = _normalize_text(normalization.get("technique", ""))
        if not technique:
            normalization["technique"] = "min_max"
        normalization_processor["normalization"] = normalization

        combination = normalization_processor.get("combination")
        if not isinstance(combination, dict):
            combination = {}
        combination_technique = _normalize_text(combination.get("technique", ""))
        if not combination_technique:
            combination["technique"] = "arithmetic_mean"
        parameters = combination.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {}
        parameters["weights"] = weights
        combination["parameters"] = parameters
        normalization_processor["combination"] = combination
        return normalized, ""

    try:
        opensearch_client = _create_client()
        normalized_pipeline_body = resolved_pipeline_body
        remap_notes: list[str] = []
        existed_before = False

        if pipeline_type == "ingest":
            mapped_fields = _extract_mapped_fields(opensearch_client, resolved_index_name)
            normalized_pipeline_body, remap_notes, unresolved = _normalize_ingest_pipeline_body(
                resolved_pipeline_body,
                mapped_fields,
            )
            if unresolved:
                requested_fields = _extract_pipeline_source_fields(resolved_pipeline_body)
                available_fields = sorted(mapped_fields.keys())
                return (
                    "Error: Ingest pipeline field_map source fields are invalid for this index mapping. "
                    f"Requested source fields: {requested_fields or ['(none)']}. "
                    f"Unresolved mappings: {unresolved}. "
                    f"Available mapped fields: {available_fields or ['(none)']}. "
                    "Please rerun planning/execution and update the pipeline field_map to use existing source fields."
                )

            try:
                existing = opensearch_client.ingest.get_pipeline(id=pipeline_name)
                existed_before = isinstance(existing, dict) and bool(existing)
            except Exception:
                existed_before = False

            if existed_before and replace_if_exists:
                try:
                    opensearch_client.ingest.delete_pipeline(id=pipeline_name)
                except Exception as e:
                    return f"Failed to recreate ingest pipeline '{pipeline_name}': {e}"

            if existed_before and not replace_if_exists:
                settings = {"index.default_pipeline": pipeline_name}
                opensearch_client.indices.put_settings(index=resolved_index_name, body=settings)
                source_fields = _extract_pipeline_source_fields(normalized_pipeline_body)
                hints_csv = ",".join(normalize_ingest_source_field_hints(source_fields))
                return (
                    f"Ingest pipeline '{pipeline_name}' already exists and is attached to index "
                    f"'{resolved_index_name}'. ingest_source_field_hints: {hints_csv}"
                )

            opensearch_client.ingest.put_pipeline(id=pipeline_name, body=normalized_pipeline_body)
            settings = {"index.default_pipeline": pipeline_name}
        elif pipeline_type == "search":
            resolved_weights = [0.5, 0.5]
            normalized_search_pipeline_body = resolved_pipeline_body
            if is_hybrid_search:
                resolved_weights, weights_error = _resolve_hybrid_weights(hybrid_weights)
                if weights_error:
                    return f"Error: Invalid hybrid search weights: {weights_error}"
                normalized_search_pipeline_body, hybrid_error = _normalize_hybrid_search_pipeline_body(
                    resolved_pipeline_body,
                    resolved_weights,
                )
                if hybrid_error:
                    return f"Error: {hybrid_error}"

            try:
                opensearch_client.transport.perform_request("GET", f"/_search/pipeline/{pipeline_name}")
                existed_before = True
            except Exception:
                existed_before = False

            if existed_before and replace_if_exists:
                try:
                    opensearch_client.transport.perform_request("DELETE", f"/_search/pipeline/{pipeline_name}")
                except Exception as e:
                    return f"Failed to recreate search pipeline '{pipeline_name}': {e}"

            if existed_before and not replace_if_exists:
                settings = {"index.search.default_pipeline": pipeline_name}
                opensearch_client.indices.put_settings(index=resolved_index_name, body=settings)
                return (
                    f"Search pipeline '{pipeline_name}' already exists and is attached to index "
                    f"'{resolved_index_name}'."
                )

            # Use low-level client for search pipeline to ensure compatibility
            opensearch_client.transport.perform_request(
                "PUT",
                f"/_search/pipeline/{pipeline_name}",
                body=normalized_search_pipeline_body,
            )
            settings = {"index.search.default_pipeline": pipeline_name}
        else:
            return f"Error: Invalid pipeline_type '{pipeline_type}'. Must be 'ingest' or 'search'."

        # Always re-attach after create/recreate so index settings are guaranteed.
        opensearch_client.indices.put_settings(index=resolved_index_name, body=settings)
        action = "recreated" if (existed_before and replace_if_exists) else "created"
        if pipeline_type == "ingest":
            source_fields = _extract_pipeline_source_fields(normalized_pipeline_body)
            hints_csv = ",".join(normalize_ingest_source_field_hints(source_fields))
            suffix = f" ingest_source_field_hints: {hints_csv}"
            if remap_notes:
                return (
                    f"{pipeline_type.capitalize()} pipeline '{pipeline_name}' {action} and attached to index '{resolved_index_name}' successfully. "
                    f"field remap: {'; '.join(remap_notes)}.{suffix}"
                )
            return (
                f"{pipeline_type.capitalize()} pipeline '{pipeline_name}' {action} and attached to index "
                f"'{resolved_index_name}' successfully.{suffix}"
            )
        return (
            f"{pipeline_type.capitalize()} pipeline '{pipeline_name}' {action} and attached to index "
            f"'{resolved_index_name}' successfully."
        )

    except Exception as e:
        return f"Failed to create and attach pipeline: {e}"



def create_local_pretrained_model(model_name: str) -> str:
    """Create a local pretrained model in OpenSearch.

    Usage Examples:
    1. Create and deploy a local pretrained model
    create_local_pretrained_model("amazon/neural-sparse/opensearch-neural-sparse-encoding-doc-v3-gte")
    2. Create and deploy a local pretrained tokenizer
    create_local_pretrained_model("amazon/neural-sparse/opensearch-neural-sparse-tokenizer-v1")

    Args:
        model_name: The name of the pretrained model (e.g., 'amazon/neural-sparse/opensearch-neural-sparse-encoding-doc-v3-gte').

    Returns:
        str: The model ID of the created and deployed model, or error message.
    """
    try:
        opensearch_client = _create_client()
        # use red font to print the model_name
        print(f"\033[91m[create_local_pretrained_model] Model name: {model_name}\033[0m", file=sys.stderr)
        if model_name not in PRETRAINED_MODELS:
            return f"Error: Model '{model_name}' not supported. Supported models: {list(PRETRAINED_MODELS.keys())}"
            
        model_version = PRETRAINED_MODELS[model_name]
        model_format = "TORCH_SCRIPT"
        
        set_ml_settings(opensearch_client)
        
        # 1. Register Model
        register_body = {
            "name": model_name,
            "version": model_version,
            "model_format": model_format
        }
        response = opensearch_client.transport.perform_request("POST", "/_plugins/_ml/models/_register", body=register_body)
        task_id = response.get("task_id")
        print(f"Model registration task started: {task_id}", file=sys.stderr)
        
        # Poll for model ID
        model_id = None
        register_state, register_task_res = _wait_for_ml_task(
            opensearch_client,
            task_id,
            max_polls=100,
            poll_interval_seconds=5,
        )
        if register_state == "COMPLETED":
            model_id = register_task_res.get("model_id")
        elif register_state == "FAILED":
            return _format_model_failure_message("registration", register_task_res.get("error"))
            
        if not model_id:
            return "Model registration timed out or failed."
        print(f"Model registered: {model_id}", file=sys.stderr)

        # 2. Deploy Model with capacity recovery for local-model slot limits.
        undeployed_model_ids: list[str] = []
        undeploy_candidates: list[str] = []

        while True:
            response = opensearch_client.transport.perform_request("POST", f"/_plugins/_ml/models/{model_id}/_deploy")
            deploy_task_id = response.get("task_id")
            print(f"Model deployment task started: {deploy_task_id}", file=sys.stderr)

            deploy_state, deploy_task_res = _wait_for_ml_task(
                opensearch_client,
                deploy_task_id,
                max_polls=100,
                poll_interval_seconds=3,
            )
            if deploy_state == "COMPLETED":
                if undeployed_model_ids:
                    recovered = ", ".join(undeployed_model_ids)
                    return (
                        f"Model '{model_name}' (ID: {model_id}) created and deployed successfully "
                        f"after undeploying model(s): {recovered}."
                    )
                return f"Model '{model_name}' (ID: {model_id}) created and deployed successfully."
            if deploy_state == "TIMEOUT":
                return f"Model deployment timed out. Model ID: {model_id}"

            deploy_error = deploy_task_res.get("error")
            if not _looks_like_local_model_limit(deploy_error):
                return _format_model_failure_message("deployment", deploy_error)

            if not undeploy_candidates:
                undeploy_candidates = _list_model_ids_for_undeploy_recovery(
                    opensearch_client,
                    exclude_model_id=model_id,
                    max_models=20,
                )
            if not undeploy_candidates:
                return (
                    f"{_format_model_failure_message('deployment', deploy_error)} "
                    "Automatic recovery could not find a model to undeploy."
                )

            undeploy_succeeded = False
            while undeploy_candidates:
                candidate_model_id = undeploy_candidates.pop(0)
                undeploy_ok, undeploy_note = _undeploy_model_and_wait(
                    opensearch_client,
                    candidate_model_id,
                )
                if undeploy_ok:
                    undeployed_model_ids.append(candidate_model_id)
                    print(
                        f"Undeployed model '{candidate_model_id}' to recover local model capacity.",
                        file=sys.stderr,
                    )
                    undeploy_succeeded = True
                    break
                print(
                    f"Undeploy skipped for model '{candidate_model_id}': {undeploy_note}",
                    file=sys.stderr,
                )

            if not undeploy_succeeded:
                return (
                    f"{_format_model_failure_message('deployment', deploy_error)} "
                    "Automatic recovery attempted undeploy, but all candidates failed."
                )

    except Exception as e:
        return f"Error creating local pretrained model: {e}"


def index_doc(index_name: str, doc: dict, doc_id: str) -> str:
    """Index a document into an OpenSearch index.

    Args:
        index_name: The name of the index to index the document into.
        doc: The document to index.
        doc_id: The ID of the document.

    Usage Examples:
    1. Index a document into an OpenSearch index
    index_doc("my_index", {"content": "The quick brown fox jumps over the lazy dog."}, "1")

    Returns:
        str: Document after ingest pipeline.
    """
    opensearch_client = _create_client()
    try:
        opensearch_client.index(index=index_name, body=doc, id=doc_id)
    except Exception as e:
        return f"Failed to index document: {e}"

    opensearch_client.indices.refresh(index=index_name)

    try:
        return opensearch_client.get(index=index_name, id=doc_id)
    except Exception as e:
        return f"Failed to get document after ingest pipeline: {e}"



def index_verification_docs(
    index_name: str,
    count: int = 10,
    id_prefix: str = "verification",
    sample_doc_json: str = "",
    source_local_file: str = "",
    source_index_name: str = "",
) -> str:
    """Index verification docs from collected sample data for UI testing.

    Args:
        index_name: Target index name.
        count: Number of docs to index (default 10, max 100).
        id_prefix: Prefix for generated doc IDs.
        sample_doc_json: JSON string of a single sample document.
        source_local_file: Path to the local file the sample was loaded from.
        source_index_name: Localhost OpenSearch index name used as sample source.

    Returns:
        str: JSON summary of indexed IDs and any errors.
    """
    effective_count = max(1, min(count, 100))
    docs = _load_sample_docs(
        limit=effective_count,
        sample_doc_json=sample_doc_json,
        source_local_file=source_local_file,
        source_index_name=source_index_name,
    )
    if not docs:
        return (
            "Failed to index verification docs: no sample docs available. "
            "Collect sample data first."
        )

    opensearch_client = _create_client()
    indexed_ids: list[str] = []
    errors: list[str] = []

    for i, doc in enumerate(docs, start=1):
        doc_id = f"{id_prefix}-{i}"
        try:
            opensearch_client.index(index=index_name, body=doc, id=doc_id)
            indexed_ids.append(doc_id)
        except Exception as e:
            errors.append(f"{doc_id}: {e}")

    if indexed_ids:
        opensearch_client.indices.refresh(index=index_name)

    result = {
        "index_name": index_name,
        "requested_count": effective_count,
        "indexed_count": len(indexed_ids),
        "doc_ids": indexed_ids,
        "errors": errors,
        "cleanup_hint": "Verification docs were kept. Run cleanup_docs when user asks.",
    }
    return json.dumps(result, ensure_ascii=False)



def launch_search_ui(index_name: str = "") -> str:
    """Launch local React Search Builder UI for interactive testing."""
    try:
        launch = _ensure_search_ui_server(index_name)
        url = str(launch.get("url", _search_ui_public_url()))
        action = str(launch.get("action", "started"))
        status = launch.get("status")
        if not isinstance(status, dict):
            status = _search_ui_status_snapshot()
        selected_index = _resolve_default_index(index_name)
    except Exception as e:
        return f"Failed to launch Search Builder UI: {e}"

    action_line = (
        f"Reusing existing server at: {url}"
        if action == "reused"
        else f"Started new Search Builder UI server at: {url}"
    )
    status_line = _format_ui_status_line(status)

    if selected_index:
        return (
            f"{action_line}\n"
            f"Default index: {selected_index}\n"
            f"{status_line}\n"
            "You can run queries immediately and inspect returned documents."
        )
    return (
        f"{action_line}\n"
        f"{status_line}\n"
        "No default index selected. Enter an index name in the UI to start searching."
    )


def connect_search_ui_to_endpoint(
    endpoint: str,
    port: int = 443,
    use_ssl: bool = True,
    username: str = "",
    password: str = "",
    aws_region: str = "",
    aws_service: str = "",
    index_name: str = "",
) -> str:
    """Switch the Search UI to query a different OpenSearch endpoint (e.g. AWS).

    Args:
        endpoint: OpenSearch host (e.g. 'search-my-domain.us-east-1.es.amazonaws.com').
        port: Port number (default 443 for AWS).
        use_ssl: Whether to use SSL/TLS (default True).
        username: Optional master user for fine-grained access control.
        password: Optional password for fine-grained access control.
        aws_region: AWS region for SigV4 auth (e.g. 'us-east-1'). Required for AOSS/managed domains.
        aws_service: AWS service name for SigV4 auth ('aoss' for serverless, 'es' for managed domains).
                     Auto-detected from endpoint if not provided.
        index_name: Optional default index to use in the UI.
    """
    endpoint = str(endpoint or "").strip()
    if not endpoint:
        return "Error: endpoint is required."

    # Auto-detect aws_service from endpoint if region is provided but service is not.
    if not aws_service and aws_region:
        if ".aoss." in endpoint:
            aws_service = "aoss"
        elif ".es." in endpoint or ".aos." in endpoint:
            aws_service = "es"

    # Auto-detect region from endpoint if not provided.
    if not aws_region and (".aoss." in endpoint or ".es." in endpoint or ".aos." in endpoint):
        import re
        region_match = re.search(r"\.([a-z]{2}-[a-z]+-\d+)\.", endpoint)
        if region_match:
            aws_region = region_match.group(1)
            if not aws_service:
                if ".aoss." in endpoint:
                    aws_service = "aoss"
                else:
                    aws_service = "es"

    _search_ui.endpoint_override_host = endpoint
    _search_ui.endpoint_override_port = port
    _search_ui.endpoint_override_use_ssl = use_ssl
    _search_ui.endpoint_override_aws_region = aws_region
    _search_ui.endpoint_override_aws_service = aws_service
    if username and password:
        _search_ui.endpoint_override_auth = (username, password)
    else:
        _search_ui.endpoint_override_auth = None

    backend = _get_backend_info()
    if not backend["connected"]:
        # Roll back override on failure so local still works.
        _search_ui.endpoint_override_host = ""
        _search_ui.endpoint_override_port = 0
        _search_ui.endpoint_override_auth = None
        _search_ui.endpoint_override_aws_region = ""
        _search_ui.endpoint_override_aws_service = ""
        return (
            f"Error: Could not connect to {endpoint}:{port}. "
            "Verify the endpoint is active and credentials are correct. "
            "Search UI remains connected to the previous endpoint."
        )

    if index_name:
        _search_ui.default_index = index_name.strip()
    _write_ui_state()

    label = "AWS Cloud" if backend["backend_type"] == "cloud" else "Remote"
    auth_mode = f"SigV4 ({aws_service}/{aws_region})" if aws_region else "basic auth" if username else "no auth"
    lines = [
        f"Search UI now connected to {label} endpoint: {endpoint}",
        f"Backend type: {backend['backend_type']}",
        f"Auth: {auth_mode}",
        f"Connected: {backend['connected']}",
    ]
    if _search_ui.default_index:
        lines.append(f"Default index: {_search_ui.default_index}")
    lines.append("Refresh the Search UI in your browser to see the updated connection badge.")
    return "\n".join(lines)



def cleanup_ui_server() -> str:
    """Stop the standalone Search Builder UI server if ownership checks pass."""
    with _search_ui.lock:
        _cleanup_stale_ui_lock()
        lock = _read_ui_lock()
        owned, owned_reason = _is_owned_ui_process(lock)

        if owned and lock:
            pid = _get_lock_pid(lock)
            stopped, used_sigkill = _terminate_process(pid)
            if stopped:
                _remove_ui_lock()
                status = _search_ui_status_snapshot()
                mode = "force-killed" if used_sigkill else "stopped"
                return (
                    f"Search Builder UI server {mode} successfully.\n"
                    f"{_format_ui_status_line(status)}"
                )
            status = _search_ui_status_snapshot()
            return (
                f"Failed to stop Search Builder UI server pid {pid}.\n"
                f"{_format_ui_status_line(status)}"
            )

        listeners = _list_listener_pids_on_ui_port()
        if listeners:
            _stop_ui_process_on_port()
            listeners = _list_listener_pids_on_ui_port()
            status = _search_ui_status_snapshot()
            if not listeners:
                return (
                    "Search Builder UI server stopped successfully.\n"
                    f"{_format_ui_status_line(status)}"
                )
            conflict_pid = listeners[0]
            conflict_cmd = _get_process_command(conflict_pid)
            return (
                f"Failed to stop process on port {SEARCH_UI_PORT} "
                f"(ownership detail: {owned_reason or 'lock missing'}).\n"
                f"Port owner: pid {conflict_pid} ({conflict_cmd or 'unknown command'}).\n"
                f"{_format_ui_status_line(status)}"
            )

        _remove_ui_lock()
        status = _search_ui_status_snapshot()
        return (
            "Search Builder UI server is already stopped.\n"
            f"{_format_ui_status_line(status)}"
        )


def set_search_ui_suggestions(index_name: str, suggestion_meta_json: str) -> str:
    """Set search UI suggestion metadata for an index.

    Call this after apply_capability_driven_verification to populate the
    search UI with capability-driven suggestions.

    Args:
        index_name: Target index name.
        suggestion_meta_json: JSON array of suggestion entries, each with
            keys: text, capability, query_mode, field, value, case_insensitive.
    """
    target = (index_name or "").strip()
    if not target:
        return "index_name is required for set_search_ui_suggestions."
    try:
        parsed = json.loads(suggestion_meta_json)
    except (json.JSONDecodeError, TypeError) as e:
        return f"Invalid suggestion_meta_json: {e}"
    if not isinstance(parsed, list):
        return "suggestion_meta_json must be a JSON array."
    _search_ui.suggestion_meta_by_index[target] = parsed
    _write_ui_state()
    return f"Set {len(parsed)} suggestions for index '{target}'."


def delete_doc(index_name: str, doc_id: str) -> str:
    """Delete a document from an OpenSearch index.

    Args:
        index_name: The name of the index to delete the document from.
        doc_id: The ID of the document to delete.

    Returns:
        str: Success message or error.
    """
    try:
        opensearch_client = _create_client()
        opensearch_client.delete(index=index_name, id=doc_id)
        return f"Document '{doc_id}' deleted from index '{index_name}' successfully."
    except Exception as e:
        return f"Failed to delete document: {e}"



def cleanup_docs(index_name: str = "", doc_ids: str = "") -> str:
    """Delete verification docs from an index.

    Args:
        index_name: Target index name. When empty, scans all non-system indices.
        doc_ids: Comma-separated or JSON list of doc IDs to delete.
            When empty, scans for docs matching the 'verification-*' ID pattern.

    Returns:
        str: JSON summary with deleted_docs count and errors.
    """
    ids_to_delete = _parse_id_list(doc_ids)

    opensearch_client = _create_client()

    target = (index_name or "").strip()
    targets: list[str] = [target] if target else []
    if not targets:
        try:
            cat_resp = opensearch_client.cat.indices(format="json")
            targets = [
                item.get("index", "")
                for item in cat_resp
                if item.get("index") and not item.get("index", "").startswith(".")
            ]
        except Exception:
            pass
    if not targets:
        return json.dumps({"deleted_docs": 0, "errors": []})

    deleted_count = 0
    errors: list[str] = []

    for idx_name in targets:
        scan_ids = list(ids_to_delete) if ids_to_delete else []
        if not scan_ids:
            try:
                resp = opensearch_client.search(
                    index=idx_name,
                    body={
                        "size": 500,
                        "query": {"wildcard": {"_id": {"value": "verification-*"}}},
                        "_source": False,
                    },
                )
                scan_ids = [
                    hit["_id"] for hit in resp.get("hits", {}).get("hits", []) if hit.get("_id")
                ]
            except Exception:
                continue

        for doc_id in scan_ids:
            try:
                opensearch_client.delete(index=idx_name, id=doc_id, ignore=[404])
                deleted_count += 1
            except Exception as e:
                errors.append(f"{idx_name}/{doc_id}: {e}")

    summary = {"deleted_docs": deleted_count, "errors": errors}
    return json.dumps(summary, ensure_ascii=False)


def create_bedrock_agentic_model(
    model_name: str = "us.anthropic.claude-sonnet-4-20250514-v1:0",
    role_arn: str = ""
) -> str:
    """Create a Bedrock Claude model for agentic search with the specified configuration.
    
    Args:
        model_name: The Bedrock Claude model ID. Defaults to Claude 4 Sonnet.
        role_arn: AWS IAM role ARN with Bedrock permissions. If not provided, uses AWS credentials from environment.
        
    Returns:
        str: The model ID of the created and deployed model, or error message.
    """
    # Validate model is a Claude model for agentic search
    if "claude" not in model_name.lower():
        return "Error: Agentic search requires a Claude model (e.g., us.anthropic.claude-sonnet-4-20250514-v1:0)."
    
    region = os.getenv("AWS_REGION", "us-east-1")
    
    # Build credential configuration
    credential_config = {}
    if role_arn and role_arn.strip():
        # Use IAM role ARN (for managed OpenSearch)
        credential_config = {"roleArn": role_arn.strip()}
    else:
        # Use AWS credentials from environment (for self-managed)
        access_key = os.getenv("AWS_ACCESS_KEY_ID")
        secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        session_token = os.getenv("AWS_SESSION_TOKEN")

        if not access_key or not secret_key:
            return "Error: Either role_arn or AWS credentials (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY) are required."

        credential_config = {
            "access_key": access_key,
            "secret_key": secret_key
        }
        if session_token:
            credential_config["session_token"] = session_token

    # Use single-step register with deploy=true (simpler and more reliable)
    register_body = {
        "name": f"agentic-search-model-{int(time.time())}",
        "function_name": "remote",
        "connector": {
            "name": f"Bedrock Claude 4 Sonnet Connector {int(time.time())}",
            "description": "Amazon Bedrock connector for Claude 4 Sonnet - Agentic Search",
            "version": 1,
            "protocol": "aws_sigv4",
            "parameters": {
                "region": region,
                "service_name": "bedrock",
                "model": model_name
            },
            "credential": credential_config,
            "actions": [
                {
                    "action_type": "predict",
                    "method": "POST",
                    "url": f"https://bedrock-runtime.{region}.amazonaws.com/model/{model_name}/converse",
                    "headers": {
                        "content-type": "application/json"
                    },
                    "request_body": '{ "system": [{"text": "${parameters.system_prompt}"}], "messages": [${parameters._chat_history:-}{"role":"user","content":[{"text":"${parameters.user_prompt}"}]}${parameters._interactions:-}]${parameters.tool_configs:-} }'
                }
            ]
        }
    }
    
    try:
        opensearch_client = _create_client()
        set_ml_settings(opensearch_client)
        
        # Single-step register and deploy
        response = opensearch_client.transport.perform_request(
            "POST", 
            "/_plugins/_ml/models/_register?deploy=true", 
            body=register_body
        )
        
        model_id = response.get("model_id") or response.get("modelId")
        if not model_id:
            return f"Model registration failed - no model_id returned: {response}"
        
        print(f"Agentic model '{model_name}' (ID: {model_id}) registered and deployed successfully.")
        return model_id

    except Exception as e:
        return f"Error creating Bedrock agentic model: {e}"


def create_bedrock_agentic_model_with_creds(
    access_key: str,
    secret_key: str,
    region: str = "us-east-1",
    session_token: str = "",
    model_name: str = "us.anthropic.claude-sonnet-4-20250514-v1:0"
) -> str:
    """Create a Bedrock Claude model for agentic search with explicit AWS credentials.
    
    This function accepts AWS credentials as parameters instead of reading from environment variables.
    Use this when credentials are provided by the orchestrator for agentic search setup.
    
    Args:
        access_key: AWS Access Key ID
        secret_key: AWS Secret Access Key
        region: AWS region (default: us-east-1)
        session_token: AWS Session Token (optional, for temporary credentials)
        model_name: The Bedrock Claude model ID. Defaults to Claude 4 Sonnet.
        
    Returns:
        str: The model ID of the created and deployed model, or error message.
    """
    # Validate inputs
    if not access_key or not access_key.strip():
        return "Error: AWS Access Key ID is required."
    if not secret_key or not secret_key.strip():
        return "Error: AWS Secret Access Key is required."
    if not region or not region.strip():
        return "Error: AWS region is required."
    
    # Validate model is a Claude model for agentic search
    if "claude" not in model_name.lower():
        return "Error: Agentic search requires a Claude model (e.g., us.anthropic.claude-sonnet-4-20250514-v1:0)."
    
    # Build credential configuration
    credential_config = {
        "access_key": access_key.strip(),
        "secret_key": secret_key.strip()
    }
    if session_token and session_token.strip():
        credential_config["session_token"] = session_token.strip()

    # Use single-step register with deploy=true (simpler and more reliable)
    register_body = {
        "name": f"agentic-search-model-{int(time.time())}",
        "function_name": "remote",
        "connector": {
            "name": f"Bedrock Claude 4 Sonnet Connector {int(time.time())}",
            "description": "Amazon Bedrock connector for Claude 4 Sonnet - Agentic Search",
            "version": 1,
            "protocol": "aws_sigv4",
            "parameters": {
                "region": region.strip(),
                "service_name": "bedrock",
                "model": "us.anthropic.claude-sonnet-4-20250514-v1:0"
            },
            "credential": credential_config,
            "actions": [
                {
                    "action_type": "predict",
                    "method": "POST",
                    "url": f"https://bedrock-runtime.{region.strip()}.amazonaws.com/model/us.anthropic.claude-sonnet-4-20250514-v1:0/converse",
                    "headers": {
                        "content-type": "application/json"
                    },
                    "request_body": '{ "system": [{"text": "${parameters.system_prompt}"}], "messages": [${parameters._chat_history:-}{"role":"user","content":[{"text":"${parameters.user_prompt}"}]}${parameters._interactions:-}]${parameters.tool_configs:-} }'
                }
            ]
        }
    }
    
    try:
        opensearch_client = _create_client()
        set_ml_settings(opensearch_client)
        
        # Single-step register and deploy
        response = opensearch_client.transport.perform_request(
            "POST", 
            "/_plugins/_ml/models/_register?deploy=true", 
            body=register_body
        )
        
        model_id = response.get("model_id") or response.get("modelId")
        if not model_id:
            return f"Model registration failed - no model_id returned: {response}"
        
        print(f"Agentic model '{model_name}' (ID: {model_id}) registered and deployed successfully.")
        return model_id

    except Exception as e:
        return f"Error creating Bedrock agentic model: {e}"


def create_agentic_search_conversational_agent(
    agent_name: str,
    model_id: str,
    max_iterations: int = 10
) -> str:
    """Create a conversational agentic search agent with memory and multiple tools.
    
    Conversational agents support:
    - Multi-turn conversations with memory
    - Multiple tools (ListIndex, IndexMapping, WebSearch, QueryPlanning)
    - Detailed reasoning traces
    - Higher latency and cost
    
    Args:
        agent_name: Name for the agent (e.g., "my-conversational-agent").
        model_id: The deployed LLM model ID from create_bedrock_agentic_model.
        max_iterations: Maximum LLM iterations for query planning. Defaults to 10.
        
    Returns:
        str: The agent ID of the created agent, or error message.
    """
    if not model_id or not model_id.strip():
        return "Error: model_id is required. Use create_bedrock_agentic_model first."
    
    try:
        opensearch_client = _create_client()
        
        agent_body = {
            "name": agent_name,
            "type": "conversational",
            "description": f"Conversational agentic search agent with memory for multi-turn queries",
            "llm": {
                "model_id": model_id,
                "parameters": {
                    "max_iteration": max_iterations
                }
            },
            "tools": [
                {
                    "type": "ListIndexTool",
                    "name": "ListIndexTool"
                },
                {
                    "type": "IndexMappingTool",
                    "name": "IndexMappingTool"
                },
                {
                    "type": "WebSearchTool",
                    "name": "DuckduckgoWebSearchTool",
                    "parameters": {
                        "engine": "duckduckgo"
                    }
                },
                {
                    "type": "QueryPlanningTool",
                    "name": "QueryPlanningTool"
                }
            ],
            "memory": {
                "type": "conversation_index"
            },
            "app_type": "os_chat",
            "parameters": {
                "_llm_interface": "bedrock/converse/claude"
            }
        }
        
        response = opensearch_client.transport.perform_request(
            "POST",
            "/_plugins/_ml/agents/_register",
            body=agent_body
        )
        
        agent_id = response.get("agent_id")
        if not agent_id:
            return f"Failed to create conversational agent: {response}"
        
        print(f"Conversational agentic search agent created: {agent_id}")
        return f"Conversational agent '{agent_name}' (ID: {agent_id}) created successfully."

    except Exception as e:
        return f"Error creating conversational agentic search agent: {e}"


def create_agentic_search_flow_agent(
    agent_name: str,
    model_id: str
) -> str:
    """Create a flow agentic search agent for stateless query planning.
    
    Flow agents are optimized for:
    - Single-turn stateless queries
    - Lower latency (fewer LLM calls)
    - Lower cost
    - Query planning with IndexMappingTool and QueryPlanningTool
    
    Args:
        agent_name: Name for the agent (e.g., "my-flow-agent").
        model_id: The deployed LLM model ID from create_bedrock_agentic_model.
        
    Returns:
        str: The agent ID of the created agent, or error message.
    """
    if not model_id or not model_id.strip():
        return "Error: model_id is required. Use create_bedrock_agentic_model first."
    
    try:
        opensearch_client = _create_client()
        
        agent_body = {
            "name": agent_name,
            "type": "flow",
            "description": "Flow agent for agentic search with index mapping awareness",
            "tools": [
                {
                    "type": "IndexMappingTool",
                    "name": "IndexMappingTool"
                },
                {
                    "type": "QueryPlanningTool",
                    "parameters": {
                        "model_id": model_id,
                        "response_filter": "$.output.message.content[0].text"
                    }
                }
            ]
        }
        
        response = opensearch_client.transport.perform_request(
            "POST",
            "/_plugins/_ml/agents/_register",
            body=agent_body
        )
        
        agent_id = response.get("agent_id")
        if not agent_id:
            return f"Failed to create flow agent: {response}"
        
        print(f"Flow agentic search agent created: {agent_id}")
        return f"Flow agent '{agent_name}' (ID: {agent_id}) created successfully."

    except Exception as e:
        return f"Error creating flow agentic search agent: {e}"


def create_agentic_search_agent(
    agent_name: str,
    model_id: str,
    agent_type: str = "conversational",
    max_iterations: int = 10
) -> str:
    """Create an agentic search agent with query planning capabilities.
    
    DEPRECATED: Use create_agentic_search_conversational_agent() or create_agentic_search_flow_agent() instead.
    
    Args:
        agent_name: Name for the agent (e.g., "my-search-agent").
        model_id: The deployed LLM model ID from create_bedrock_agentic_model.
        agent_type: Agent type - "conversational" (with memory) or "flow" (stateless). Defaults to conversational.
        max_iterations: Maximum LLM iterations for query planning. Defaults to 10.
        
    Returns:
        str: The agent ID of the created agent, or error message.
    """
    if agent_type == "flow":
        return create_agentic_search_flow_agent(agent_name, model_id)
    else:
        return create_agentic_search_conversational_agent(agent_name, model_id, max_iterations)


def create_agentic_search_pipeline(
    pipeline_name: str,
    agent_id: str,
    index_name: str,
    replace_if_exists: bool = True
) -> str:
    """Create and attach an agentic search pipeline to an index.
    
    This creates a search pipeline with:
    - Request processor: agentic_query_translator (translates natural language to DSL)
    - Response processor: agentic_context (includes agent reasoning and generated DSL)
    
    Args:
        pipeline_name: Name for the search pipeline (e.g., "my-agentic-pipeline").
        agent_id: The agent ID from create_agentic_search_agent.
        index_name: The index to attach the pipeline to.
        replace_if_exists: Delete and recreate pipeline if it already exists. Defaults to True.
        
    Returns:
        str: Success message or error.
    """
    if not agent_id or not agent_id.strip():
        return "Error: agent_id is required. Use create_agentic_search_agent first."
    
    if not index_name or not index_name.strip():
        return "Error: index_name is required."
    
    # Create search pipeline with agentic processors
    pipeline_body = {
        "request_processors": [
            {
                "agentic_query_translator": {
                    "agent_id": agent_id
                }
            }
        ],
        "response_processors": [
            {
                "agentic_context": {
                    "agent_steps_summary": True,
                    "dsl_query": True
                }
            }
        ]
    }
    
    # Use the standard create_and_attach_pipeline function
    return create_and_attach_pipeline(
        pipeline_name=pipeline_name,
        pipeline_body=pipeline_body,
        index_name=index_name,
        pipeline_type="search",
        replace_if_exists=replace_if_exists,
        is_hybrid_search=False,
        hybrid_weights=None
    )
