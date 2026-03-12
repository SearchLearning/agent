#!/usr/bin/env python3
"""Standalone WebSocket server — spawned as a detached subprocess by launch_interactive_ui."""
import asyncio
import logging

logging.basicConfig(level=logging.INFO)


def main() -> None:
    from opensearch_orchestrator.websocket_server import start_websocket_server
    asyncio.run(start_websocket_server())


main()
