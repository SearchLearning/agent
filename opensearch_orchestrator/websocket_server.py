"""WebSocket server for real-time interactive UI communication."""
import asyncio
import json
import logging
from typing import Any

import websockets

logger = logging.getLogger(__name__)

active_connections: set = set()

# Last search context shared across connections for chat evaluation
_last_search_context: dict = {}


async def handle_search(data: dict[str, Any]) -> dict[str, Any]:
    from opensearch_orchestrator.opensearch_ops_tools import _search_ui_search
    index = data.get("index", "")
    query = data.get("query", "")
    size = data.get("size", 20)
    try:
        result = _search_ui_search(
            index_name=index,
            query_text=query,
            size=size,
            debug=False,
            search_intent=data.get("intent", ""),
            field_hint=data.get("field", ""),
        )
        # Store for chat context
        _last_search_context.update({
            "index": index,
            "query": query,
            "hits": result.get("hits", []),
            "query_mode": result.get("query_mode", ""),
            "took_ms": result.get("took_ms", 0),
        })
        return {"type": "search_results", "data": result}
    except Exception as e:
        return {"type": "error", "message": str(e)}


async def handle_chat(data: dict[str, Any]) -> dict[str, Any]:
    message = data.get("message", "").strip()
    if not message:
        return {"type": "chat_response", "message": ""}

    # Build context from last search
    ctx_parts = []
    if _last_search_context.get("query"):
        ctx_parts.append(f"Last query: \"{_last_search_context['query']}\"")
        ctx_parts.append(f"Index: {_last_search_context.get('index', '')}")
        ctx_parts.append(f"Query mode: {_last_search_context.get('query_mode', '')}")
        ctx_parts.append(f"Took: {_last_search_context.get('took_ms', 0)}ms")
        hits = _last_search_context.get("hits", [])
        ctx_parts.append(f"Results ({len(hits)} hits):")
        for i, hit in enumerate(hits[:5]):
            src = hit.get("_source", {})
            score = hit.get("_score", 0)
            ctx_parts.append(f"  [{i+1}] score={score:.3f} {json.dumps(src)[:200]}")
    else:
        ctx_parts.append("No search has been run yet.")

    search_context = "\n".join(ctx_parts)

    try:
        from strands import Agent
        agent = Agent(
            system_prompt=(
                "You are a search quality assistant for OpenSearch. "
                "You help users evaluate search results, suggest better queries, "
                "explain why results are ranked the way they are, and recommend "
                "search strategy improvements. Be concise and actionable."
            ),
        )
        prompt = f"Search context:\n{search_context}\n\nUser question: {message}"
        response = agent(prompt)
        reply = str(response)
    except Exception as e:
        # Fallback: simple rule-based responses
        reply = _simple_chat_fallback(message, search_context)

    return {"type": "chat_response", "message": reply}


def _simple_chat_fallback(message: str, context: str) -> str:
    msg = message.lower()
    if any(k in msg for k in ["evaluat", "quality", "good", "bad", "relevant"]):
        hits = _last_search_context.get("hits", [])
        if not hits:
            return "No results to evaluate yet. Run a search first."
        scores = [h.get("_score", 0) for h in hits if h.get("_score")]
        if scores:
            avg = sum(scores) / len(scores)
            top = scores[0]
            return (
                f"Found {len(hits)} results. Top score: {top:.3f}, avg: {avg:.3f}.\n"
                f"{'Scores look reasonable.' if top > 1.0 else 'Low scores — consider a broader query or check your index has data.'}"
            )
    if any(k in msg for k in ["suggest", "improve", "better query", "try"]):
        q = _last_search_context.get("query", "")
        return f"Try variations of \"{q}\" — use fewer words for broader results, or add field filters like year ranges."
    if any(k in msg for k in ["why", "explain", "how"]):
        mode = _last_search_context.get("query_mode", "unknown")
        return f"Results are ranked using {mode} scoring. BM25 uses term frequency and inverse document frequency."
    return f"Context:\n{context}\n\nI can help evaluate results, suggest query improvements, or explain ranking. What would you like to know?"


async def handle_get_status(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "status",
        "phase": "ready",
        "ready": True,
        "last_query": _last_search_context.get("query", ""),
        "last_index": _last_search_context.get("index", ""),
    }


async def handle_message(websocket: Any, message: str) -> None:
    try:
        data = json.loads(message)
        action = data.get("action", "")
        if action == "search":
            response = await handle_search(data)
        elif action == "chat":
            response = await handle_chat(data)
        elif action == "get_status":
            response = await handle_get_status(data)
        else:
            response = {"type": "error", "message": f"Unknown action: {action}"}
        await websocket.send(json.dumps(response))
    except Exception as e:
        logger.exception("Error handling message")
        try:
            await websocket.send(json.dumps({"type": "error", "message": str(e)}))
        except Exception:
            pass


async def websocket_handler(websocket: Any) -> None:
    active_connections.add(websocket)
    try:
        await websocket.send(json.dumps({
            "type": "connected",
            "message": "Connected to OpenSearch Launchpad. Run a search, then ask me to evaluate the results."
        }))
        async for message in websocket:
            await handle_message(websocket, message)
    except Exception:
        pass
    finally:
        active_connections.discard(websocket)


async def start_websocket_server(host: str = "127.0.0.1", port: int = 8766) -> None:
    logger.info(f"Starting WebSocket server on ws://{host}:{port}")
    async with websockets.serve(websocket_handler, host, port):
        await asyncio.Future()
