"""WebSocket server for real-time interactive UI communication."""
import asyncio
import json
import logging
import math
from typing import Any

import websockets

logger = logging.getLogger(__name__)

active_connections: set = set()
_last_search_context: dict = {}
_judgment_cache: dict[str, dict[str, int]] = {}  # query -> {doc_id: 0|1}
_aws_credentials: dict[str, str] = {}  # access_key, secret_key, region, session_token
_last_eval_summary: dict[str, Any] = {}  # stored from last auto_judge_all run


async def handle_search(data: dict[str, Any]) -> dict[str, Any]:
    from opensearch_orchestrator.opensearch_ops_tools import _search_ui_search
    index = data.get("index", "")
    query = data.get("query", "")
    size = data.get("size", 20)
    try:
        result = _search_ui_search(
            index_name=index, query_text=query, size=size, debug=False,
            search_intent=data.get("intent", ""), field_hint=data.get("field", ""),
        )
        _last_search_context.update({
            "index": index, "query": query,
            "hits": result.get("hits", []),
            "query_mode": result.get("query_mode", ""),
            "took_ms": result.get("took_ms", 0),
        })
        resp: dict[str, Any] = {"type": "search_results", "data": result}
        # Attach cached judgments if we've judged this query before
        cached = _judgment_cache.get(query)
        if cached:
            resp["cached_judgments"] = cached
        return resp
    except Exception as e:
        return {"type": "error", "message": str(e)}


def _compute_ndcg(judgments: dict[str, int], hits: list[dict]) -> float:
    """Compute NDCG@k from binary judgments (1=relevant, 0=irrelevant)."""
    dcg = 0.0
    idcg = 0.0
    n_relevant = sum(1 for v in judgments.values() if v == 1)
    for i, hit in enumerate(hits):
        doc_id = str(hit.get("_id") or hit.get("id") or "")
        rel = judgments.get(doc_id, 0)
        dcg += rel / math.log2(i + 2)
    for i in range(min(n_relevant, len(hits))):
        idcg += 1.0 / math.log2(i + 2)
    return round(dcg / idcg, 4) if idcg > 0 else 0.0


async def handle_evaluate(data: dict[str, Any]) -> dict[str, Any]:
    """Data-driven evaluation from user judgments (query-docId relevance pairs)."""
    judgments: dict[str, int] = data.get("judgments", {})
    hits = data.get("hits") or _last_search_context.get("hits", [])
    query = data.get("query") or _last_search_context.get("query", "")
    query_mode = _last_search_context.get("query_mode", "")

    if not hits:
        return {"type": "eval_result", "error": "No search results to evaluate."}
    if not judgments:
        return {"type": "eval_result", "error": "No judgments provided."}

    # Cache judgments for this query
    if query:
        _judgment_cache.setdefault(query, {}).update(judgments)

    n_judged = len(judgments)
    n_relevant = sum(1 for v in judgments.values() if v == 1)
    precision = round(n_relevant / n_judged, 3) if n_judged else 0.0
    ndcg = _compute_ndcg(judgments, hits)

    top_hit_id = str(hits[0].get("_id") or hits[0].get("id") or "") if hits else ""
    top_is_relevant = judgments.get(top_hit_id) == 1 if top_hit_id else None

    relevance_score = min(5, max(1, round(1 + precision * 4)))
    ndcg_score = min(5, max(1, round(1 + ndcg * 4)))
    top_rank_score = 5 if top_is_relevant else (3 if top_is_relevant is None else 2)

    issues = []
    suggested_preferences: dict[str, str] = {}

    if precision < 0.3:
        issues.append(f"Low precision ({precision:.0%}) — most judged results are irrelevant.")
        suggested_preferences["query_pattern"] = "mostly-semantic"
        suggested_preferences["performance"] = "accuracy-first"
    elif precision < 0.6:
        issues.append(f"Moderate precision ({precision:.0%}) — some irrelevant results surfacing.")
        if query_mode not in ("hybrid", "hybrid_structured"):
            suggested_preferences["query_pattern"] = "balanced"

    if ndcg < 0.4:
        issues.append(f"Low NDCG ({ndcg:.3f}) — relevant results are not ranking near the top.")
    elif ndcg < 0.7:
        issues.append(f"Moderate NDCG ({ndcg:.3f}) — ranking is acceptable but could improve.")

    if top_is_relevant is False:
        issues.append("Top result was marked irrelevant.")

    if not issues:
        issues.append("Results look good based on your judgments.")

    summary_lines = [
        f"Relevance: [{relevance_score}/5] — Precision {precision:.0%} ({n_relevant}/{n_judged} relevant).",
        f"Ranking Quality: [{ndcg_score}/5] — NDCG@{len(hits)} = {ndcg:.3f}.",
        f"Top Result: [{top_rank_score}/5] — {'Relevant' if top_is_relevant else ('Not judged' if top_is_relevant is None else 'Irrelevant')}.",
    ]

    try:
        from opensearch_orchestrator.mcp_server import _engine
        _engine.set_evaluation(
            search_quality_summary="\n".join(summary_lines),
            issues="\n".join(f"- {i}" for i in issues),
            suggested_preferences=suggested_preferences,
        )
    except Exception:
        pass

    return {
        "type": "eval_result",
        "metrics": {"precision": precision, "ndcg": ndcg, "n_judged": n_judged, "n_relevant": n_relevant, "top_is_relevant": top_is_relevant},
        "scores": {"relevance": relevance_score, "ranking_quality": ndcg_score, "top_result": top_rank_score},
        "issues": issues,
        "suggested_preferences": suggested_preferences,
        "summary": summary_lines,
        "judgments": judgments,
    }


async def handle_get_status(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "status", "phase": "ready", "ready": True,
        "last_query": _last_search_context.get("query", ""),
        "last_index": _last_search_context.get("index", ""),
    }


async def handle_get_search_context(data: dict[str, Any]) -> dict[str, Any]:
    """Return the last search context for MCP tools (cross-process)."""
    return {"type": "search_context", **_last_search_context}


async def handle_push_judgments(data: dict[str, Any], sender: Any) -> dict[str, Any]:
    """Receive judgments from MCP tool and broadcast to all UI clients."""
    judgments = data.get("judgments", {})
    if not judgments:
        return {"type": "error", "message": "No judgments provided."}
    # Cache judgments keyed by current query
    query = _last_search_context.get("query", "")
    if query:
        _judgment_cache.setdefault(query, {}).update(judgments)
    msg = json.dumps({"type": "judgments_update", "judgments": judgments})
    sent = 0
    for ws in list(active_connections):
        if ws is sender:
            continue
        try:
            await ws.send(msg)
            sent += 1
        except Exception:
            pass
    return {"type": "judgments_pushed", "sent_to": sent, "count": len(judgments)}


async def broadcast_judgments(judgments: dict[str, int]) -> None:
    """Push judgments to all connected UI clients."""
    msg = json.dumps({"type": "judgments_update", "judgments": judgments})
    for ws in list(active_connections):
        try:
            await ws.send(msg)
        except Exception:
            pass


async def handle_set_credentials(data: dict[str, Any]) -> dict[str, Any]:
    """Store AWS credentials for Bedrock LLM calls."""
    ak = data.get("access_key", "").strip()
    sk = data.get("secret_key", "").strip()
    region = data.get("region", "us-west-2").strip()
    st = data.get("session_token", "").strip()
    if not ak or not sk:
        return {"type": "error", "message": "access_key and secret_key are required."}
    _aws_credentials.update({"access_key": ak, "secret_key": sk, "region": region, "session_token": st})
    return {"type": "credentials_saved", "region": region}


def _build_judgment_prompt(query: str, query_mode: str, hits: list[dict]) -> str:
    """Build a judgment prompt from search context (vectors stripped)."""
    docs = []
    for hit in hits:
        doc_id = str(hit.get("_id") or hit.get("id") or "")
        src = hit.get("_source") or hit.get("source") or {}
        fields = {}
        for k, v in src.items():
            if v in (None, "", "\\N"):
                continue
            if isinstance(v, list) and v and isinstance(v[0], (int, float)):
                continue
            if isinstance(v, dict) and v and all(isinstance(val, (int, float)) for val in v.values()):
                continue
            fields[k] = v
        docs.append({"id": doc_id, "fields": fields})

    doc_lines = [f'  id={d["id"]} | {json.dumps(d["fields"], ensure_ascii=False)[:400]}' for d in docs]
    return (
        f'Query: "{query}"\n\n'
        f'Documents:\n' + "\n".join(doc_lines) + "\n\n"
        "For each document, decide if it is relevant (1) or not relevant (0) to the query.\n"
        "Judge purely by whether the document is a useful result for someone searching this query.\n\n"
        "Return ONLY a JSON object mapping doc_id to 1 or 0. No explanation.\n"
        'Example: {"tt0000001": 1, "tt0000002": 0}\n'
    )


def _llm_judge_sync(prompt: str) -> dict[str, int]:
    """Call strands.Agent with Bedrock using AWS credentials. Returns judgments dict.

    Credentials are resolved in order:
    1. Explicit credentials pushed via set_credentials websocket action
    2. boto3 default credential chain (env vars, ~/.aws/credentials, IAM role)
    """
    import boto3
    from strands import Agent
    from strands.models.bedrock import BedrockModel

    creds = _aws_credentials
    if creds.get("access_key") and creds.get("secret_key"):
        session = boto3.Session(
            aws_access_key_id=creds["access_key"],
            aws_secret_access_key=creds["secret_key"],
            aws_session_token=creds.get("session_token") or None,
            region_name=creds.get("region", "us-west-2"),
        )
    else:
        # Fall back to default boto3 credential chain
        session = boto3.Session()
        if session.get_credentials() is None:
            raise ValueError(
                "No AWS credentials available. Configure credentials via "
                "environment variables (AWS_ACCESS_KEY_ID), ~/.aws/credentials, "
                "or push them through the MCP workflow."
            )

    bedrock_model = BedrockModel(
        model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
        boto_session=session,
    )
    agent = Agent(model=bedrock_model)
    response = str(agent(prompt))

    # Extract JSON from response
    import re
    match = re.search(r'\{[^{}]+\}', response)
    if not match:
        raise ValueError(f"LLM did not return valid JSON: {response[:200]}")
    raw = json.loads(match.group())
    return {str(k): int(v) for k, v in raw.items() if int(v) in (0, 1)}


async def handle_auto_judge(data: dict[str, Any]) -> dict[str, Any]:
    """Run LLM auto-judge on current search results using strands.Agent + Bedrock."""
    query = _last_search_context.get("query", "")
    query_mode = _last_search_context.get("query_mode", "")
    hits = _last_search_context.get("hits", [])

    if not hits or not query:
        return {"type": "auto_judge_result", "error": "No search results. Run a search first."}

    prompt = _build_judgment_prompt(query, query_mode, hits)

    try:
        loop = asyncio.get_event_loop()
        judgments = await loop.run_in_executor(None, _llm_judge_sync, prompt)
    except Exception as e:
        logger.exception("[auto_judge] LLM call failed")
        return {"type": "auto_judge_result", "error": f"LLM call failed: {e}"}

    if not judgments:
        return {"type": "auto_judge_result", "error": "LLM returned no judgments."}

    # Cache and broadcast (only fill in doc_ids not already cached)
    if query:
        existing = _judgment_cache.setdefault(query, {})
        for doc_id, score in judgments.items():
            if doc_id not in existing:
                existing[doc_id] = score
    await broadcast_judgments(judgments)

    n_relevant = sum(1 for v in judgments.values() if v == 1)
    return {
        "type": "auto_judge_result",
        "count": len(judgments),
        "relevant": n_relevant,
        "irrelevant": len(judgments) - n_relevant,
    }


async def handle_message(websocket: Any, message: str) -> None:
    try:
        data = json.loads(message)
        action = data.get("action", "")
        if action == "search":
            response = await handle_search(data)
        elif action == "evaluate":
            response = await handle_evaluate(data)
        elif action == "get_status":
            response = await handle_get_status(data)
        elif action == "get_search_context":
            response = await handle_get_search_context(data)
        elif action == "push_judgments":
            response = await handle_push_judgments(data, sender=websocket)
        elif action == "set_credentials":
            response = await handle_set_credentials(data)
        elif action == "auto_judge":
            response = await handle_auto_judge(data)
        elif action == "auto_judge_all":
            response = await handle_auto_judge_all(data, websocket)
        elif action == "remove_judgment":
            doc_id = data.get("doc_id", "")
            query = _last_search_context.get("query", "")
            if query and doc_id and query in _judgment_cache:
                _judgment_cache[query].pop(doc_id, None)
            response = {"type": "judgment_removed", "doc_id": doc_id}
        elif action == "clear_judgments":
            _judgment_cache.clear()
            response = {"type": "judgments_cleared"}
        elif action == "get_eval_summary":
            if _last_eval_summary:
                response = {"type": "eval_summary", **_last_eval_summary}
            else:
                response = {"type": "error", "message": "No evaluation summary available. Run 'Evaluate All' first."}
        elif action == "commit_evaluation":
            if _last_eval_summary:
                response = {"type": "evaluation_committed", "status": "ok", "message": "Evaluation results stored. Return to Kiro chat to continue."}
            else:
                response = {"type": "error", "message": "No evaluation summary available. Run 'Evaluate All' first."}
        else:
            response = {"type": "error", "message": f"Unknown action: {action}"}
        await websocket.send(json.dumps(response))
    except Exception as e:
        logger.exception("Error handling message")
        try:
            await websocket.send(json.dumps({"type": "error", "message": str(e)}))
        except Exception:
            pass

def _compute_eval_metrics(judgments: dict[str, int], hits: list[dict]) -> dict[str, Any]:
    """Compute evaluation metrics for a single query's judgments."""
    n_judged = len(judgments)
    n_relevant = sum(1 for v in judgments.values() if v == 1)
    precision = round(n_relevant / n_judged, 3) if n_judged else 0.0
    ndcg = _compute_ndcg(judgments, hits)
    top_hit_id = str(hits[0].get("_id") or hits[0].get("id") or "") if hits else ""
    top_is_relevant = judgments.get(top_hit_id) == 1 if top_hit_id else None
    return {
        "precision": precision, "ndcg": ndcg,
        "n_judged": n_judged, "n_relevant": n_relevant,
        "top_is_relevant": top_is_relevant,
    }


async def handle_auto_judge_all(data: dict[str, Any], websocket: Any) -> dict[str, Any]:
    """Run auto-judge across all suggested queries, streaming per-query results."""
    index = data.get("index", "") or _last_search_context.get("index", "")
    suggestions = data.get("suggestions", [])
    if not suggestions:
        return {"type": "auto_judge_all_result", "error": "No suggested queries provided."}
    if not index:
        return {"type": "auto_judge_all_result", "error": "No index specified."}

    from opensearch_orchestrator.opensearch_ops_tools import _search_ui_search

    query_results: list[dict[str, Any]] = []
    loop = asyncio.get_event_loop()

    for i, entry in enumerate(suggestions):
        q_text = entry.get("text", "")
        q_capability = entry.get("capability", "")
        q_mode = entry.get("query_mode", "")
        q_field = entry.get("field", "")
        if not q_text:
            continue

        # Send progress
        try:
            await websocket.send(json.dumps({
                "type": "auto_judge_all_progress",
                "current": i + 1, "total": len(suggestions),
                "query": q_text, "capability": q_capability,
            }))
        except Exception:
            pass

        # Run search
        try:
            result = _search_ui_search(
                index_name=index, query_text=q_text, size=20, debug=False,
                search_intent="", field_hint=q_field,
            )
            hits = result.get("hits", [])
            actual_mode = result.get("query_mode", q_mode)
        except Exception as e:
            query_results.append({
                "query": q_text, "capability": q_capability, "query_mode": q_mode,
                "error": str(e), "metrics": None, "judgments": {},
            })
            continue

        if not hits:
            query_results.append({
                "query": q_text, "capability": q_capability, "query_mode": actual_mode,
                "hit_count": 0, "metrics": None, "judgments": {},
            })
            continue

        # Build prompt and judge
        prompt = _build_judgment_prompt(q_text, actual_mode, hits)
        try:
            judgments = await loop.run_in_executor(None, _llm_judge_sync, prompt)
        except Exception as e:
            query_results.append({
                "query": q_text, "capability": q_capability, "query_mode": actual_mode,
                "hit_count": len(hits), "error": f"LLM: {e}", "metrics": None, "judgments": {},
            })
            continue

        # Cache (only fill in doc_ids not already cached)
        existing = _judgment_cache.setdefault(q_text, {})
        for doc_id, score in judgments.items():
            if doc_id not in existing:
                existing[doc_id] = score

        metrics = _compute_eval_metrics(judgments, hits)
        query_results.append({
            "query": q_text, "capability": q_capability, "query_mode": actual_mode,
            "hit_count": len(hits), "metrics": metrics, "judgments": judgments,
        })

    # Build overall summary
    summary = _build_overall_summary(query_results)

    # Store for MCP retrieval
    _last_eval_summary.clear()
    _last_eval_summary.update({"summary": summary, "query_results": query_results})

    return {
        "type": "auto_judge_all_result",
        "query_results": query_results,
        "summary": summary,
    }


def _build_overall_summary(query_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a 4-dimension evaluation summary from per-query results + execution plan."""
    scored = [r for r in query_results if r.get("metrics")]
    if not scored:
        return {"verdict": "No queries could be evaluated.", "overall_score": 0,
                "dimensions": {}, "capability_breakdown": {}, "recommendations": [],
                "current_config": {}}

    avg_precision = sum(r["metrics"]["precision"] for r in scored) / len(scored)
    avg_ndcg = sum(r["metrics"]["ndcg"] for r in scored) / len(scored)
    total_relevant = sum(r["metrics"]["n_relevant"] for r in scored)
    total_judged = sum(r["metrics"]["n_judged"] for r in scored)
    top_failures = [r for r in scored if r["metrics"]["top_is_relevant"] is False]
    error_queries = [r for r in query_results if r.get("error")]
    zero_hit_queries = [r for r in query_results if r.get("hit_count") == 0 and not r.get("error")]

    # Per-capability breakdown
    cap_stats: dict[str, list[dict]] = {}
    for r in scored:
        cap = r.get("capability", "unknown")
        cap_stats.setdefault(cap, []).append(r["metrics"])

    cap_breakdown = {}
    for cap, metrics_list in cap_stats.items():
        cap_avg_p = sum(m["precision"] for m in metrics_list) / len(metrics_list)
        cap_avg_n = sum(m["ndcg"] for m in metrics_list) / len(metrics_list)
        cap_breakdown[cap] = {"precision": round(cap_avg_p, 3), "ndcg": round(cap_avg_n, 3), "count": len(metrics_list)}

    # ── Detect actual search configuration from OpenSearch index ──
    solution = ""
    query_pattern = ""
    performance = ""
    budget = ""
    deployment_pref = ""
    has_semantic = False
    has_sparse = False
    has_hybrid = False
    has_bm25 = False
    has_agentic = False
    has_structured = False

    # Read preferences from engine state (these are always reliable)
    try:
        from opensearch_orchestrator.mcp_server import _engine
        prefs = getattr(_engine, "state", None)
        if prefs:
            query_pattern = str(getattr(prefs, "query_pattern", ""))
            performance = str(getattr(prefs, "performance", ""))
            budget = str(getattr(prefs, "budget", ""))
            deployment_pref = str(getattr(prefs, "deployment_preference", ""))
    except Exception:
        pass

    # Introspect the actual OpenSearch index for ground-truth configuration
    eval_index = _last_search_context.get("index", "")
    if eval_index:
        try:
            from opensearch_orchestrator.opensearch_ops_tools import (
                _create_client,
                _extract_index_field_specs,
                _normalize_text,
            )
            client = _create_client()
            field_specs = _extract_index_field_specs(client, eval_index)

            # Detect field types from actual mapping
            for field_name, spec in field_specs.items():
                ftype = spec.get("type", "")
                if ftype == "knn_vector":
                    has_semantic = True
                elif ftype == "sparse_vector" or ftype == "rank_features":
                    has_sparse = True
                elif ftype == "text":
                    has_bm25 = True
                elif ftype in ("keyword", "integer", "long", "float", "double",
                               "date", "boolean", "short", "byte"):
                    has_structured = True

            # Detect pipelines from index settings
            settings_resp = client.indices.get_settings(index=eval_index)
            idx_settings = next(iter(settings_resp.values()), {})
            idx_cfg = idx_settings.get("settings", {}).get("index", {})
            ingest_pipeline = _normalize_text(idx_cfg.get("default_pipeline", ""))
            search_pipeline = _normalize_text(
                idx_cfg.get("search", {}).get("default_pipeline", "")
            )

            # Check search pipeline for hybrid normalization or agentic processor
            if search_pipeline:
                try:
                    pipe_resp = client.transport.perform_request(
                        "GET", f"/_search/pipeline/{search_pipeline}"
                    )
                    pipe_def = pipe_resp.get(search_pipeline, {})
                    # Phase processors contain normalization + combination for hybrid
                    phase_procs = pipe_def.get("phase_results_processors", [])
                    for proc in phase_procs:
                        if isinstance(proc, dict) and "normalization-processor" in proc:
                            has_hybrid = True
                            break
                    # Request processors may contain agentic translator
                    req_procs = pipe_def.get("request_processors", [])
                    for proc in req_procs:
                        if isinstance(proc, dict) and "agentic_query_translator" in proc:
                            has_agentic = True
                            break
                except Exception:
                    pass

            # Check ingest pipeline for embedding model info
            embedding_model = ""
            if ingest_pipeline:
                try:
                    pipe_resp = client.ingest.get_pipeline(id=ingest_pipeline)
                    pipe_def = pipe_resp.get(ingest_pipeline, {})
                    for proc in pipe_def.get("processors", []):
                        if isinstance(proc, dict):
                            emb = proc.get("text_embedding")
                            if isinstance(emb, dict) and emb.get("model_id"):
                                embedding_model = str(emb["model_id"])
                                break
                            sparse_emb = proc.get("sparse_encoding")
                            if isinstance(sparse_emb, dict) and sparse_emb.get("model_id"):
                                embedding_model = str(sparse_emb["model_id"])
                                break
                except Exception:
                    pass

            # Build a descriptive solution string from actual config
            parts = []
            if has_hybrid:
                parts.append("Hybrid")
                if has_bm25:
                    parts.append("BM25")
                if has_semantic:
                    parts.append("Dense Vector")
                if has_sparse:
                    parts.append("Sparse Vector")
            elif has_agentic:
                parts.append("Agentic Search")
            elif has_semantic:
                parts.append("Dense Vector")
            elif has_sparse:
                parts.append("Sparse Vector")
            elif has_bm25:
                parts.append("BM25")
            if embedding_model:
                parts.append(f"model={embedding_model}")
            solution = " + ".join(parts) if parts else ""

        except Exception:
            pass

    # Derive declared capabilities from actual index configuration
    declared_caps: set[str] = set()
    if has_bm25:
        declared_caps.add("exact")
    if has_semantic or has_sparse:
        declared_caps.add("semantic")
    if has_structured:
        declared_caps.add("structured")
    if (has_bm25 or has_semantic or has_sparse) and has_structured:
        declared_caps.add("combined")

    tested_caps = set(cap_breakdown.keys()) - {"unknown"}

    # ── Dimension 1: Relevance ──
    relevance_score = min(5, max(1, round(1 + avg_precision * 4)))
    relevance_detail = f"Avg precision {avg_precision:.0%} ({total_relevant}/{total_judged} relevant across {len(scored)} queries)."
    if avg_precision >= 0.8:
        relevance_detail += " Top results consistently match user intent."
    elif avg_precision >= 0.5:
        relevance_detail += " Some irrelevant results surfacing in the top positions."
    else:
        relevance_detail += " Most results are not matching user intent."

    # ── Dimension 2: Query Coverage ──
    missing_caps = declared_caps - tested_caps
    coverage_ratio = len(tested_caps) / max(len(declared_caps), 1) if declared_caps else (1.0 if tested_caps else 0.0)
    failed_caps = {cap for cap, stats in cap_breakdown.items() if stats["precision"] < 0.3}
    effective_coverage = (len(tested_caps) - len(failed_caps)) / max(len(declared_caps), len(tested_caps), 1)
    coverage_score = min(5, max(1, round(1 + effective_coverage * 4)))
    coverage_detail = f"{len(tested_caps)} of {len(declared_caps) or len(tested_caps)} declared capabilities tested."
    if missing_caps:
        coverage_detail += f" Not tested: {', '.join(sorted(missing_caps))}."
    if failed_caps:
        coverage_detail += f" Failing (<30% precision): {', '.join(sorted(failed_caps))}."
    if zero_hit_queries:
        coverage_detail += f" {len(zero_hit_queries)} queries returned 0 hits."

    # ── Dimension 3: Ranking Quality ──
    ranking_score = min(5, max(1, round(1 + avg_ndcg * 4)))
    top_fail_ratio = len(top_failures) / len(scored) if scored else 0
    if top_fail_ratio > 0.3:
        ranking_score = min(ranking_score, 3)
    ranking_detail = f"Avg NDCG {avg_ndcg:.3f}."
    if top_failures:
        ranking_detail += f" {len(top_failures)}/{len(scored)} queries had irrelevant top result."
    if avg_ndcg >= 0.8:
        ranking_detail += " Relevant documents are ranking near the top."
    elif avg_ndcg >= 0.5:
        ranking_detail += " Ranking is acceptable but some relevant docs are buried."
    else:
        ranking_detail += " Relevant documents are not surfacing at the top."

    # ── Dimension 4: Capability Gaps ──
    gaps: list[str] = []
    gap_recommendations: list[dict] = []

    # Check if semantic is needed but missing
    sem_stats = cap_breakdown.get("semantic", {})
    if sem_stats and sem_stats.get("precision", 1) < 0.5 and not has_semantic and not has_hybrid:
        gaps.append("No semantic/vector search — meaning-based queries fail with BM25 only")
        gap_recommendations.append({
            "area": "Semantic search gap",
            "issue": f"Semantic queries have {sem_stats['precision']:.0%} precision. Current plan uses BM25 only with no embedding model.",
            "action": "Switch to hybrid search (BM25 + dense vector) to handle meaning-based queries. This requires adding an embedding model and neural search pipeline.",
            "preference_change": {"query_pattern": "balanced"},
        })
    elif sem_stats and sem_stats.get("precision", 1) < 0.5 and has_hybrid:
        gaps.append("Hybrid search active but semantic precision is low — embedding model or weights may need tuning")
        model_hint = "sparse" if has_sparse else ("dense" if has_semantic else "unknown")
        gap_recommendations.append({
            "area": "Semantic search quality",
            "issue": f"Semantic queries have {sem_stats['precision']:.0%} precision even with hybrid search ({model_hint} model).",
            "action": f"Try {'switching from sparse to dense vector model' if has_sparse else 'adjusting hybrid weights toward semantic-heavy (0.2/0.8)'} or upgrading the embedding model for better semantic understanding.",
            "preference_change": {"query_pattern": "mostly-semantic", "performance": "accuracy-first"},
        })

    # Check exact search
    ex_stats = cap_breakdown.get("exact", {})
    if ex_stats and ex_stats.get("precision", 1) < 0.5:
        gaps.append("Exact/term queries returning irrelevant results — keyword field mapping issue")
        gap_recommendations.append({
            "area": "Exact search",
            "issue": f"Exact queries have {ex_stats['precision']:.0%} precision.",
            "action": "Verify keyword fields are mapped with .keyword subfield and term queries target them. Consider switching to mostly-exact query pattern to prioritize lexical matching.",
            "preference_change": {"query_pattern": "mostly-exact"},
        })

    # Check structured filters
    st_stats = cap_breakdown.get("structured", {})
    if st_stats and st_stats.get("precision", 1) < 0.5:
        gaps.append("Structured filter queries not filtering correctly")
        gap_recommendations.append({
            "area": "Structured filters",
            "issue": f"Structured filter queries have {st_stats['precision']:.0%} precision.",
            "action": "Ensure filter fields use keyword type and structured queries use bool.filter (not bool.must) to avoid score pollution.",
            "preference_change": {},
        })

    # Check combined
    comb_stats = cap_breakdown.get("combined", {})
    if comb_stats and comb_stats.get("precision", 1) < 0.5:
        gaps.append("Combined (text + filter) queries not working well")
        gap_recommendations.append({
            "area": "Combined search",
            "issue": f"Combined queries have {comb_stats['precision']:.0%} precision.",
            "action": "Check that hybrid_structured mode properly separates text matching (bool.must) from filters (bool.filter). Filters should not affect relevance scores.",
            "preference_change": {},
        })

    # Missing capabilities not tested
    for mc in sorted(missing_caps):
        gaps.append(f"{mc} capability declared but not tested")

    # Ranking issues
    if avg_ndcg < 0.5:
        gap_recommendations.append({
            "area": "Ranking quality",
            "issue": f"Average NDCG is {avg_ndcg:.3f}. Relevant results are not ranking near the top.",
            "action": "Consider adding field boosts (e.g. boost title field in multi_match) or adjusting hybrid weights to improve ranking.",
            "preference_change": {"performance": "accuracy-first"},
        })

    if len(top_failures) > len(scored) / 2:
        gap_recommendations.append({
            "area": "Top result quality",
            "issue": f"{len(top_failures)}/{len(scored)} queries had an irrelevant top result.",
            "action": "The ranking function needs tuning. Consider boosting exact-match fields or adjusting BM25 parameters (k1, b).",
            "preference_change": {"performance": "accuracy-first"},
        })

    # ── Overall precision threshold check (< 70% triggers actionable suggestions) ──
    if avg_precision < 0.7 and not gap_recommendations:
        # No single capability is catastrophically bad, but overall quality is below threshold.
        # Generate meaningful suggestions based on the search strategy and per-capability analysis.

        # Identify weakest capability
        weakest_cap = ""
        weakest_precision = 1.0
        for cap, stats in cap_breakdown.items():
            if stats["precision"] < weakest_precision:
                weakest_precision = stats["precision"]
                weakest_cap = cap

        if has_hybrid and has_sparse:
            # Sparse hybrid — suggest switching to dense or adjusting weights toward semantic
            gaps.append(f"Overall precision {avg_precision:.0%} is below 70% threshold")
            gap_recommendations.append({
                "area": "Hybrid weight tuning",
                "issue": f"Overall precision is {avg_precision:.0%}. Sparse vector hybrid may not capture semantic nuance well enough for this dataset.",
                "action": "Try switching to mostly-semantic query pattern to increase semantic weight (0.2 lexical / 0.8 semantic), or switch from sparse to dense vector model for richer embeddings.",
                "preference_change": {"query_pattern": "mostly-semantic"},
            })
        elif has_hybrid and has_semantic:
            # Dense hybrid — suggest weight adjustment
            gaps.append(f"Overall precision {avg_precision:.0%} is below 70% threshold")
            gap_recommendations.append({
                "area": "Hybrid weight tuning",
                "issue": f"Overall precision is {avg_precision:.0%}. Current balanced hybrid weights may not be optimal for this dataset.",
                "action": "Try mostly-semantic query pattern to shift hybrid weights toward semantic (0.2/0.8), or try accuracy-first performance to use a higher-quality embedding model.",
                "preference_change": {"query_pattern": "mostly-semantic", "performance": "accuracy-first"},
            })
        elif has_bm25 and not has_hybrid:
            # BM25 only — suggest adding semantic
            gaps.append(f"Overall precision {avg_precision:.0%} is below 70% threshold with BM25 only")
            gap_recommendations.append({
                "area": "Add semantic search",
                "issue": f"Overall precision is {avg_precision:.0%} with BM25 only. Keyword matching alone may miss relevant results for natural language queries.",
                "action": "Switch to balanced or mostly-semantic query pattern to enable hybrid search with an embedding model.",
                "preference_change": {"query_pattern": "balanced"},
            })
        else:
            # Generic fallback
            gaps.append(f"Overall precision {avg_precision:.0%} is below 70% threshold")
            gap_recommendations.append({
                "area": "Overall precision",
                "issue": f"Overall precision is {avg_precision:.0%}. Weakest capability: {weakest_cap} at {weakest_precision:.0%}.",
                "action": "Try accuracy-first performance priority to use higher-quality models and stricter relevance tuning.",
                "preference_change": {"performance": "accuracy-first"},
            })

        # Suggest indexing more text fields if only one field is being embedded
        try:
            from opensearch_orchestrator.mcp_server import _engine
            inferred_fields = getattr(_engine.state, "inferred_text_fields", []) or []
            if len(inferred_fields) > 1:
                gap_recommendations.append({
                    "area": "Text field coverage",
                    "issue": f"Multiple text fields available ({', '.join(inferred_fields)}) but the ingest pipeline may only embed one. Broader field coverage could improve recall.",
                    "action": "During re-planning, ensure the ingest pipeline embeds a concatenation of key text fields (e.g. primaryTitle + genres) rather than a single field.",
                    "preference_change": {},
                })
        except Exception:
            pass

    gap_score = 5 if not gaps else max(1, 5 - len(gaps))
    gap_detail = "No significant gaps." if not gaps else " ".join(gaps[:3]) + ("." if not gaps[0].endswith(".") else "")

    # Overall score = weighted average of 4 dimensions, capped by precision threshold
    raw_overall = round((relevance_score + coverage_score + ranking_score + gap_score) / 4)
    # If avg precision is below 70%, cap overall at 3 regardless of other dimensions
    if avg_precision < 0.7:
        overall_score = min(3, raw_overall)
    else:
        overall_score = min(5, max(1, raw_overall))

    # Merge all recommendations
    recommendations = list(gap_recommendations)
    if not recommendations:
        if avg_precision >= 0.7:
            recommendations.append({
                "area": "Overall",
                "issue": f"Search quality is solid at {avg_precision:.0%} precision across all tested query types.",
                "action": "No changes needed. Consider deploying to AWS.",
                "preference_change": {},
            })
        else:
            # Safety fallback — should not normally reach here
            recommendations.append({
                "area": "Overall",
                "issue": f"Overall precision is {avg_precision:.0%}, below the 70% quality threshold.",
                "action": "Consider restarting with accuracy-first performance priority.",
                "preference_change": {"performance": "accuracy-first"},
            })

    has_actionable = any(r.get("preference_change") for r in recommendations)
    if avg_precision >= 0.7 and overall_score >= 4 and not has_actionable:
        verdict = "Ready for deployment"
    elif overall_score >= 3 and avg_precision >= 0.5:
        verdict = "Acceptable — improvements recommended"
    else:
        verdict = "Improvements needed before deployment"

    dimensions = {
        "relevance": {"score": relevance_score, "detail": relevance_detail},
        "query_coverage": {"score": coverage_score, "detail": coverage_detail},
        "ranking_quality": {"score": ranking_score, "detail": ranking_detail},
        "capability_gaps": {"score": gap_score, "detail": gap_detail},
    }

    return {
        "overall_score": overall_score,
        "verdict": verdict,
        "dimensions": dimensions,
        "avg_precision": round(avg_precision, 3),
        "avg_ndcg": round(avg_ndcg, 3),
        "total_relevant": total_relevant,
        "total_judged": total_judged,
        "queries_evaluated": len(scored),
        "queries_failed": len(error_queries),
        "queries_zero_hits": len(zero_hit_queries),
        "top_failures": len(top_failures),
        "capability_breakdown": cap_breakdown,
        "recommendations": recommendations,
        "current_config": {
            "solution": solution,
            "query_pattern": query_pattern,
            "performance": performance,
            "budget": budget,
            "deployment_preference": deployment_pref,
            "has_semantic": has_semantic,
            "has_sparse": has_sparse,
            "has_hybrid": has_hybrid,
            "has_bm25": has_bm25,
            "has_agentic": has_agentic,
            "has_structured": has_structured,
            "declared_capabilities": sorted(declared_caps),
        },
    }




async def websocket_handler(websocket: Any) -> None:
    active_connections.add(websocket)
    try:
        await websocket.send(json.dumps({
            "type": "connected",
            "message": "Connected to OpenSearch Search Builder."
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