"""Transport-agnostic orchestration engine shared by CLI and MCP adapters."""

from __future__ import annotations

import json
from typing import Any

from opensearch_orchestrator.shared import Phase


class OrchestratorEngine:
    """Stateful workflow engine for sample -> preferences -> planning -> execution."""

    def __init__(
        self,
        *,
        state: Any,
        clear_sample_state: Any,
        reset_state: Any,
        capture_sample_from_result: Any,
        infer_semantic_text_fields: Any,
        is_semantic_dominant_query_pattern: Any,
        build_context_notes: Any,
        build_planning_context: Any,
        run_worker_with_state: Any,
        get_last_worker_run_state: Any,
        planning_session_factory: Any,
        load_builtin_sample: Any,
        load_local_file_sample: Any,
        load_url_sample: Any,
        load_localhost_index_sample: Any,
        load_pasted_sample: Any,
        budget_option_flexible: str,
        budget_option_cost_sensitive: str,
        performance_option_speed: str,
        performance_option_balanced: str,
        performance_option_accuracy: str,
        query_pattern_option_mostly_exact: str,
        query_pattern_option_balanced: str,
        query_pattern_option_mostly_semantic: str,
        model_deployment_option_opensearch_node: str,
        model_deployment_option_sagemaker_endpoint: str,
        model_deployment_option_external_embedding_api: str,
        hybrid_weight_option_semantic: str,
        hybrid_weight_option_balanced: str,
        hybrid_weight_option_lexical: str,
        resume_marker: str,
    ) -> None:
        self.state = state
        self.phase = Phase.COLLECT_SAMPLE
        self.planning = None
        self.plan_result: dict | None = None
        self.evaluation_result: dict | None = None

        self._clear_sample_state = clear_sample_state
        self._reset_state = reset_state
        self._capture_sample_from_result = capture_sample_from_result
        self._infer_semantic_text_fields = infer_semantic_text_fields
        self._is_semantic_dominant_query_pattern = is_semantic_dominant_query_pattern
        self._build_context_notes = build_context_notes
        self._build_planning_context = build_planning_context
        self._run_worker_with_state = run_worker_with_state
        self._get_last_worker_run_state = get_last_worker_run_state
        self._planning_session_factory = planning_session_factory

        self._load_builtin_sample = load_builtin_sample
        self._load_local_file_sample = load_local_file_sample
        self._load_url_sample = load_url_sample
        self._load_localhost_index_sample = load_localhost_index_sample
        self._load_pasted_sample = load_pasted_sample

        self._valid_source_types = {
            "builtin_imdb",
            "local_file",
            "url",
            "localhost_index",
            "paste",
        }
        self._valid_budget = {
            budget_option_flexible,
            budget_option_cost_sensitive,
        }
        self._valid_performance = {
            performance_option_speed,
            performance_option_balanced,
            performance_option_accuracy,
        }
        self._valid_query_pattern = {
            query_pattern_option_mostly_exact,
            query_pattern_option_balanced,
            query_pattern_option_mostly_semantic,
        }
        self._deployment_required_query_patterns = {
            query_pattern_option_balanced,
            query_pattern_option_mostly_semantic,
        }
        self._valid_deployment = {
            model_deployment_option_opensearch_node,
            model_deployment_option_sagemaker_endpoint,
            model_deployment_option_external_embedding_api,
        }
        self._query_pattern_to_hybrid_weight = {
            query_pattern_option_mostly_exact: hybrid_weight_option_lexical,
            query_pattern_option_balanced: hybrid_weight_option_balanced,
            query_pattern_option_mostly_semantic: hybrid_weight_option_semantic,
        }
        self._default_budget = budget_option_flexible
        self._default_performance = performance_option_balanced
        self._default_query_pattern = query_pattern_option_balanced
        self._default_deployment = model_deployment_option_opensearch_node
        self._resume_marker = resume_marker

    def _build_worker_context(self, additional_context: str = "") -> dict[str, object]:
        if self.plan_result is None:
            return {
                "error": "No finalized plan available. Complete the planning phase first."
            }

        plan = self.plan_result
        solution = plan.get("solution", "")
        capabilities = plan.get("search_capabilities", "")
        keynote = plan.get("keynote", "")

        worker_context = (
            f"Solution:\n{solution}\n\n"
            f"Search Capabilities:\n{capabilities}\n\n"
            f"Keynote:\n{keynote}"
        )
        if additional_context:
            worker_context += f"\n\n{additional_context}"

        return {
            "execution_context": worker_context,
            "plan": {
                "solution": str(solution or ""),
                "search_capabilities": str(capabilities or ""),
                "keynote": str(keynote or ""),
            },
        }

    def reset(self) -> None:
        self._reset_state(self.state)
        self.phase = Phase.COLLECT_SAMPLE
        self.planning = None
        self.plan_result = None
        self.evaluation_result = None

    def load_sample(
        self,
        source_type: str,
        source_value: str = "",
        localhost_auth_mode: str = "default",
        localhost_auth_username: str = "",
        localhost_auth_password: str = "",
    ) -> dict:
        if source_type not in self._valid_source_types:
            return {
                "error": (
                    f"Invalid source_type '{source_type}'. Must be one of: "
                    f"{sorted(self._valid_source_types)}"
                )
            }

        state = self.state
        self._clear_sample_state(state)

        if source_type == "builtin_imdb":
            result = self._load_builtin_sample()
        elif source_type == "local_file":
            if not source_value:
                return {
                    "error": (
                        "source_value is required for local_file source_type "
                        "(provide a file path)."
                    )
                }
            result = self._load_local_file_sample(source_value)
        elif source_type == "url":
            if not source_value:
                return {
                    "error": (
                        "source_value is required for url source_type "
                        "(provide a URL)."
                    )
                }
            result = self._load_url_sample(source_value)
        elif source_type == "localhost_index":
            normalized_mode = str(localhost_auth_mode or "").strip().lower()
            if normalized_mode not in {"default", "none", "custom"}:
                return {
                    "error": (
                        "Invalid localhost_auth_mode. Must be one of: "
                        "default, none, custom."
                    )
                }
            user = str(localhost_auth_username or "").strip()
            password = str(localhost_auth_password or "").strip()
            if normalized_mode == "custom" and (not user or not password):
                return {
                    "error": (
                        "localhost_auth_mode='custom' requires both "
                        "localhost_auth_username and localhost_auth_password."
                    )
                }

            state.localhost_auth_mode = normalized_mode
            if normalized_mode == "custom":
                state.localhost_auth_username = user
                state.localhost_auth_password = password
            else:
                state.localhost_auth_username = None
                state.localhost_auth_password = None

            result = self._load_localhost_index_sample(
                source_value,
                normalized_mode,
                user,
                password,
            )
        else:
            if not source_value:
                return {
                    "error": (
                        "source_value is required for paste source_type "
                        "(provide JSON content)."
                    )
                }
            result = self._load_pasted_sample(source_value)

        loaded = self._capture_sample_from_result(state, result)
        if not loaded:
            if isinstance(result, str) and result.startswith("Error:"):
                return {"error": result}
            return {"error": f"Failed to load sample document. Raw result: {result}"}

        parsed_result = json.loads(result)
        sample_payload = parsed_result["sample_doc"]
        state.inferred_semantic_text_fields = self._infer_semantic_text_fields(sample_payload)
        state.inferred_text_search_required = bool(state.inferred_semantic_text_fields)

        source_is_localhost = bool(parsed_result.get("source_localhost_index"))
        if source_is_localhost:
            state.source_index_name = str(parsed_result.get("source_index_name", "")).strip() or None
            raw_doc_count = parsed_result.get("source_index_doc_count")
            if isinstance(raw_doc_count, int) and not isinstance(raw_doc_count, bool):
                state.source_index_doc_count = max(0, raw_doc_count)

        self.phase = Phase.GATHER_INFO
        return {
            "status": parsed_result.get("status", "Sample loaded."),
            "sample_doc": sample_payload,
            "inferred_text_fields": state.inferred_semantic_text_fields,
            "text_search_required": state.inferred_text_search_required,
            "source_index_name": state.source_index_name,
            "source_index_doc_count": state.source_index_doc_count,
        }

    def set_preferences(
        self,
        *,
        budget: str = "flexible",
        performance: str = "balanced",
        query_pattern: str = "balanced",
        deployment_preference: str = "",
    ) -> dict:
        state = self.state
        if state.sample_doc_json is None:
            return {"error": "No sample document loaded. Call load_sample first."}

        budget_val = budget if budget in self._valid_budget else self._default_budget
        perf_val = performance if performance in self._valid_performance else self._default_performance
        qp_val = (
            query_pattern
            if query_pattern in self._valid_query_pattern
            else self._default_query_pattern
        )
        hw_val = self._query_pattern_to_hybrid_weight.get(qp_val, self._query_pattern_to_hybrid_weight[self._default_query_pattern])

        state.budget_preference = budget_val
        state.performance_priority = perf_val
        text_search_disabled = state.inferred_text_search_required is False

        if text_search_disabled:
            # Non-text datasets should not carry semantic-only preference state.
            state.hybrid_weight_profile = None
            state.model_deployment_preference = None
        else:
            state.hybrid_weight_profile = hw_val

            if state.inferred_text_search_required and state.prefix_wildcard_enabled is None:
                state.prefix_wildcard_enabled = False

            if (
                qp_val in self._deployment_required_query_patterns
                and state.inferred_text_search_required
            ):
                dep_val = (
                    deployment_preference
                    if deployment_preference in self._valid_deployment
                    else self._default_deployment
                )
                state.model_deployment_preference = dep_val
            else:
                state.model_deployment_preference = None

        return {
            "budget": state.budget_preference,
            "performance": state.performance_priority,
            "query_pattern": qp_val,
            "hybrid_weight_profile": state.hybrid_weight_profile,
            "deployment_preference": state.model_deployment_preference,
            "context_notes": self._build_context_notes(state),
        }

    async def start_planning(
        self,
        *,
        additional_context: str = "",
        planning_agent: Any = None,
    ) -> dict:
        state = self.state
        if state.sample_doc_json is None:
            return {"error": "No sample loaded. Call load_sample first."}

        context = self._build_planning_context(state, additional_context)
        self.plan_result = None
        if planning_agent is None:
            self.planning = self._planning_session_factory()
        else:
            self.planning = self._planning_session_factory(agent=planning_agent)

        if hasattr(self.planning, "astart"):
            result = await self.planning.astart(context)
        else:
            result = self.planning.start(context)

        if result.get("is_complete") and result.get("result"):
            self.plan_result = result["result"]
        return result

    async def refine_plan(self, user_feedback: str) -> dict:
        if self.planning is None:
            return {"error": "No planning session active. Call start_planning first."}

        if hasattr(self.planning, "asend"):
            result = await self.planning.asend(user_feedback)
        else:
            result = self.planning.send(user_feedback)

        if result.get("is_complete") and result.get("result"):
            self.plan_result = result["result"]
        return result

    async def finalize_plan(self) -> dict:
        if self.planning is None:
            return {"error": "No planning session active. Call start_planning first."}

        if hasattr(self.planning, "afinalize"):
            result = await self.planning.afinalize()
        else:
            result = self.planning.finalize()

        if result.get("is_complete") and result.get("result"):
            self.plan_result = result["result"]
        return result

    async def execute_plan(
        self,
        *,
        additional_context: str = "",
        worker_executor: Any = None,
        worker_executor_async: Any = None,
    ) -> dict:
        context_payload = self._build_worker_context(additional_context)
        if "error" in context_payload:
            return {"error": str(context_payload["error"])}
        worker_context = str(context_payload.get("execution_context", "")).strip()
        if not worker_context:
            return {"error": "Failed to build execution context from finalized plan."}

        if worker_executor_async is not None:
            worker_result = await worker_executor_async(worker_context)
        elif worker_executor is not None:
            worker_result = worker_executor(self.state, worker_context)
        else:
            worker_result = self._run_worker_with_state(self.state, worker_context)

        self.phase = Phase.DONE
        return {"execution_report": worker_result}

    def build_execution_context(
        self,
        *,
        additional_context: str = "",
    ) -> dict[str, object]:
        """Build execution context without running the worker."""
        return self._build_worker_context(additional_context)

    def set_plan(
        self,
        *,
        solution: str,
        search_capabilities: str = "",
        keynote: str = "",
    ) -> dict:
        """Store a client-authored finalized plan for later execution.

        This is used by MCP clients that do not support server->client sampling.
        The client LLM can author the plan and commit it through an explicit tool call.
        """
        if self.state.sample_doc_json is None:
            return {"error": "No sample loaded. Call load_sample first."}

        clean_solution = str(solution or "").strip()
        if not clean_solution:
            return {"error": "solution is required and cannot be empty."}

        self.plan_result = {
            "solution": clean_solution,
            "search_capabilities": str(search_capabilities or "").strip(),
            "keynote": str(keynote or "").strip(),
        }
        return {
            "status": "Plan stored.",
            "result": self.plan_result,
        }

    def set_evaluation(
        self,
        *,
        search_quality_summary: str,
        issues: str = "",
        suggested_preferences: dict | None = None,
    ) -> dict:
        """Store a finalized evaluation result.

        Args:
            search_quality_summary: Human-readable summary of search quality findings.
            issues: Identified issues or gaps in the current setup.
            suggested_preferences: Optional dict of recommended set_preferences args
                (budget, performance, query_pattern, deployment_preference) for a restart.

        Returns:
            dict with status and stored evaluation result.
        """
        if self.plan_result is None:
            return {"error": "No finalized plan available. Complete the planning and execution phases first."}

        clean_summary = str(search_quality_summary or "").strip()
        if not clean_summary:
            return {"error": "search_quality_summary is required and cannot be empty."}

        self.evaluation_result = {
            "search_quality_summary": clean_summary,
            "issues": str(issues or "").strip(),
            "suggested_preferences": dict(suggested_preferences) if isinstance(suggested_preferences, dict) else {},
        }
        return {
            "status": "Evaluation stored.",
            "result": self.evaluation_result,
        }

    async def retry_execution(        self,
        *,
        worker_executor: Any = None,
        worker_executor_async: Any = None,
    ) -> dict:
        context_payload = self.build_retry_execution_context()
        if "error" in context_payload:
            return {"error": str(context_payload["error"])}

        resume_context = str(context_payload.get("execution_context", "")).strip()
        if not resume_context:
            return {"error": "No checkpoint context available. Run execute_plan first."}

        if worker_executor_async is not None:
            worker_result = await worker_executor_async(resume_context)
        elif worker_executor is not None:
            worker_result = worker_executor(self.state, resume_context)
        else:
            worker_result = self._run_worker_with_state(self.state, resume_context)

        latest_state = self._get_last_worker_run_state()
        latest_status = str(latest_state.get("status", "")).lower()
        self.phase = Phase.DONE if latest_status == "success" else Phase.EXEC_FAILED
        return {"execution_report": worker_result}

    def prepare_aws_deployment(self) -> dict[str, object]:
        """Build structured context for AWS deployment (Phase 5).

        Returns deployment target, search strategy, local config extracted
        from the finalized plan and session state.
        """
        if self.phase != Phase.DONE:
            return {
                "error": (
                    "AWS deployment requires successful local execution (Phase 4). "
                    "Current phase: " + str(getattr(self.phase, "name", self.phase))
                )
            }
        if self.plan_result is None:
            return {"error": "No finalized plan available."}

        solution = str(self.plan_result.get("solution", "")).lower()
        capabilities = str(self.plan_result.get("search_capabilities", ""))
        keynote = str(self.plan_result.get("keynote", ""))

        # Detect primary search strategy from the solution text.
        strategy = "bm25"
        if "agentic" in solution:
            strategy = "agentic"
        elif "hybrid" in solution:
            strategy = "hybrid"
        elif "neural sparse" in solution or "sparse_encoding" in solution:
            strategy = "neural_sparse"
        elif (
            "dense vector" in solution
            or "knn" in solution
            or "hnsw" in solution
            or "text_embedding" in solution
        ):
            strategy = "dense_vector"

        # Deployment target: agentic requires domain, everything else uses serverless.
        deployment_target = "domain" if strategy == "agentic" else "serverless"

        state = self.state
        sample_doc = None
        if state.sample_doc_json:
            try:
                sample_doc = json.loads(state.sample_doc_json)
            except (json.JSONDecodeError, TypeError):
                sample_doc = None

        text_fields = list(state.inferred_semantic_text_fields or [])

        return {
            "deployment_target": deployment_target,
            "search_strategy": strategy,
            "steering_files": (
                [
                    "steering/aws/domain-01-provision.md",
                    "steering/aws/domain-02-deploy-search.md",
                    "steering/aws/domain-03-agentic-setup.md",
                ]
                if deployment_target == "domain"
                else [
                    "steering/aws/serverless-01-provision.md",
                    "steering/aws/serverless-02-deploy-search.md",
                ]
            ),
            "local_config": {
                "text_fields": text_fields,
                "sample_doc": sample_doc,
                "budget": state.budget_preference,
                "performance": state.performance_priority,
                "deployment_preference": state.model_deployment_preference,
                "hybrid_weight_profile": state.hybrid_weight_profile,
            },
            "plan_summary": {
                "solution": str(self.plan_result.get("solution", "")),
                "search_capabilities": capabilities,
                "keynote": keynote,
            },
            "required_mcp_servers": [
                "awslabs.aws-api-mcp-server",
                "opensearch-mcp-server",
                "aws-docs",
            ],
            "state_file_template": {
                "deployment_target": deployment_target,
                "search_strategy": strategy,
                "step_completed": None,
                "aws_account_id": None,
                "aws_region": None,
                "principal_arn": None,
                "resource_name": None,
                "resource_endpoint": None,
                "iam_role_arn": None,
                "connector_id": None,
                "model_id": None,
                "model_group_id": None,
                "agent_id": None,
                "index_name": None,
                "ingest_pipeline_name": None,
                "search_pipeline_name": None,
            },
        }

    def build_retry_execution_context(self) -> dict[str, object]:
        """Build retry execution context without running the worker."""
        worker_state = self._get_last_worker_run_state()
        recovery_context = (
            str(worker_state.get("context", "")).strip()
            if isinstance(worker_state, dict)
            else ""
        )
        if not recovery_context:
            return {
                "error": "No checkpoint context available. Run execute_plan first."
            }

        resume_context = f"{self._resume_marker}\n{recovery_context}"
        return {
            "execution_context": resume_context,
            "failed_step": (
                str(worker_state.get("failed_step", "")).strip()
                if isinstance(worker_state, dict)
                else ""
            ),
            "previous_steps": (
                dict(worker_state.get("steps", {}))
                if isinstance(worker_state, dict)
                and isinstance(worker_state.get("steps", {}), dict)
                else {}
            ),
        }
