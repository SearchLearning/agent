"""
OpenSearch Client Management

Provides a shared OpenSearch client with auto-start capabilities.
"""

import os
import platform
import shutil
import subprocess
import time
from opensearchpy import OpenSearch


# Configuration from environment
OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "localhost")
OPENSEARCH_PORT = int(os.getenv("OPENSEARCH_PORT", "9200"))
OPENSEARCH_USER = os.getenv("OPENSEARCH_USER", "admin")
OPENSEARCH_PASSWORD = os.getenv("OPENSEARCH_PASSWORD", "myStrongPassword123!")
OPENSEARCH_DOCKER_IMAGE = os.getenv("OPENSEARCH_DOCKER_IMAGE", "opensearchproject/opensearch:latest")
OPENSEARCH_DOCKER_CONTAINER = os.getenv("OPENSEARCH_DOCKER_CONTAINER", "opensearch-local")
OPENSEARCH_DOCKER_START_TIMEOUT = int(os.getenv("OPENSEARCH_DOCKER_START_TIMEOUT", "120"))


def _build_client(use_ssl: bool) -> OpenSearch:
    """Build an OpenSearch client."""
    return OpenSearch(
        hosts=[{"host": OPENSEARCH_HOST, "port": OPENSEARCH_PORT}],
        use_ssl=use_ssl,
        verify_certs=False,
        ssl_show_warn=False,
        http_auth=(OPENSEARCH_USER, OPENSEARCH_PASSWORD),
    )


def _can_connect(client: OpenSearch) -> bool:
    """Check if client can connect to OpenSearch."""
    try:
        client.info()
        return True
    except Exception:
        return False


def _is_local_host(host: str) -> bool:
    """Check if host is localhost."""
    return host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def _run_docker_command(command: list[str]) -> subprocess.CompletedProcess:
    """Run a docker command."""
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )


def _docker_install_hint() -> str:
    """Get Docker installation hint for current platform."""
    system_name = platform.system().lower()
    if system_name == "darwin":
        if shutil.which("brew"):
            return (
                "Install Docker Desktop with Homebrew: "
                "'brew install --cask docker && open -a Docker'. "
                "Official docs: https://docs.docker.com/desktop/setup/install/mac-install/"
            )
        return (
            "Install Docker Desktop for macOS: "
            "https://docs.docker.com/desktop/setup/install/mac-install/"
        )
    
    if system_name == "windows":
        return (
            "Install Docker Desktop for Windows: "
            "https://docs.docker.com/desktop/setup/install/windows-install/"
        )
    
    if system_name == "linux":
        return (
            "Install Docker Engine for Linux: "
            "https://docs.docker.com/engine/install/"
        )
    
    return "Install Docker: https://docs.docker.com/get-started/get-docker/"


def _docker_start_hint() -> str:
    """Get Docker start hint for current platform."""
    system_name = platform.system().lower()
    if system_name in {"darwin", "windows"}:
        return "Start Docker Desktop and wait until it reports it is running."
    if system_name == "linux":
        return "Start Docker service (for example: 'sudo systemctl start docker')."
    return "Start the Docker daemon/service and retry."


def _start_local_opensearch_container() -> None:
    """Start a local OpenSearch container using Docker."""
    if not _is_local_host(OPENSEARCH_HOST):
        raise RuntimeError(
            f"Auto-start only supports local hosts. Current OPENSEARCH_HOST='{OPENSEARCH_HOST}'."
        )
    
    # Check Docker is installed
    try:
        _run_docker_command(["docker", "--version"])
    except Exception as e:
        raise RuntimeError(
            "Docker is not installed or not available in PATH. "
            f"{_docker_install_hint()}"
        ) from e
    
    # Check Docker daemon is running
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
        return  # Already running
    
    # Remove existing container if present
    existing = _run_docker_command(
        ["docker", "ps", "-aq", "-f", f"name=^{OPENSEARCH_DOCKER_CONTAINER}$"]
    ).stdout.strip()
    if existing:
        _run_docker_command(["docker", "rm", "-f", OPENSEARCH_DOCKER_CONTAINER])
    
    # Pull and run OpenSearch
    _run_docker_command(["docker", "pull", OPENSEARCH_DOCKER_IMAGE])
    _run_docker_command([
        "docker", "run", "-d",
        "--name", OPENSEARCH_DOCKER_CONTAINER,
        "-p", f"{OPENSEARCH_PORT}:9200",
        "-p", "9600:9600",
        "-e", "discovery.type=single-node",
        "-e", "plugins.security.disabled=true",
        "-e", "DISABLE_INSTALL_DEMO_CONFIG=true",
        OPENSEARCH_DOCKER_IMAGE,
    ])


def _wait_for_cluster_after_start() -> OpenSearch:
    """Wait for OpenSearch cluster to be ready after starting."""
    secure_client = _build_client(use_ssl=True)
    insecure_client = _build_client(use_ssl=False)
    deadline = time.time() + OPENSEARCH_DOCKER_START_TIMEOUT
    
    while time.time() < deadline:
        if _can_connect(secure_client):
            return secure_client
        if _can_connect(insecure_client):
            return insecure_client
        time.sleep(2)
    
    raise RuntimeError(
        f"OpenSearch container did not become ready within {OPENSEARCH_DOCKER_START_TIMEOUT}s."
    )


def get_opensearch_client() -> OpenSearch:
    """
    Get an OpenSearch client, auto-starting a local container if needed.
    
    Returns:
        OpenSearch: Connected OpenSearch client
        
    Raises:
        RuntimeError: If connection fails or Docker is not available
    """
    # Try secure connection first
    secure_client = _build_client(use_ssl=True)
    if _can_connect(secure_client):
        return secure_client
    
    # Try insecure connection
    insecure_client = _build_client(use_ssl=False)
    if _can_connect(insecure_client):
        return insecure_client
    
    # Both failed, try to auto-start local OpenSearch
    _start_local_opensearch_container()
    return _wait_for_cluster_after_start()
