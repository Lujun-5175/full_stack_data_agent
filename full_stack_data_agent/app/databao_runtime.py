from __future__ import annotations

import base64
import hashlib
import io
import json
import queue
import re
import time
import threading
from dataclasses import dataclass, field
from io import StringIO
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pandas as pd
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from full_stack_data_agent.app.chart_contracts import (
    build_chart_request_contract,
    build_controlled_repair_prompt,
    validate_chart_contract,
)
from full_stack_data_agent.app.binding_engine import resolve_binding_bundle
from full_stack_data_agent.app.binding_intent import parse_binding_intent
from full_stack_data_agent.app.llm_binding_intent import enrich_binding_intent_with_llm
from full_stack_data_agent.app.dependencies import check_runtime_dependencies
from full_stack_data_agent.app.normalization import normalize_dataframe
from full_stack_data_agent.app.response_grounding import GroundedResponse
from full_stack_data_agent.app.result_artifacts import ResultArtifact
from full_stack_data_agent.app.result_workspace import ResultWorkspace
from full_stack_data_agent.app.runtime_models import DatabaoSessionSnapshot, DatabaoTurnResult, RegisteredTable
from full_stack_data_agent.config.provider_resolution import (
    ResolvedProviderConfig,
    normalize_provider_name,
    probe_provider_health,
    resolve_provider_config,
)
from full_stack_data_agent.config.settings import Settings
from full_stack_data_agent.context.models import ConversationTurn, UploadedFileContext
from full_stack_data_agent.context.semantic_profile import build_semantic_profile
from full_stack_data_agent.llm.models import (
    ProviderAuthError,
    ProviderError,
    ProviderHealth,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from full_stack_data_agent.utils.json_extract import extract_first_json_object
from full_stack_data_agent.utils.pandas_types import is_categorical_dtype
from full_stack_data_agent.utils.text_classification import EXPLICIT_CHART_REQUEST_MARKERS

if TYPE_CHECKING:
    from databao.agent.configs.llm import LLMConfig
    from databao.agent.core.visualizer import VisualisationResult


_DELIVERABLE_MARKERS = (
    "deliverable",
    "deliverables",
    "requirement",
    "requirements",
    "task",
    "tasks",
    "query",
    "question",
    "query:",
    "question:",
    "要求",
    "任务",
    "查询",
    "请回答",
    "请分析",
    "请解释",
    "请说明",
    "回答以下",
    "you must answer",
    "please answer the following",
    "please answer the questions",
    "please answer",
    "answer the following",
    "final answer must include",
)

_DELIVERABLE_TRIGGER_MARKERS = (
    "query:",
    "question:",
    "要求:",
    "任务:",
    "查询:",
    "解释:",
    "画图:",
    "分析:",
    "请回答:",
    "please answer",
    "answer the following",
    "follow the following",
    "1.",
    "1)",
    "a.",
    "- ",
    "* ",
)

_QUESTION_STOPWORDS = {
    "and",
    "are",
    "based",
    "be",
    "data",
    "final",
    "for",
    "from",
    "give",
    "how",
    "in",
    "is",
    "least",
    "must",
    "of",
    "or",
    "please",
    "question",
    "questions",
    "result",
    "results",
    "show",
    "tell",
    "the",
    "to",
    "using",
    "what",
    "when",
    "whether",
    "which",
    "with",
    "you",
    "your",
    "回答",
    "请问",
    "请回答",
    "请说明",
    "请分析",
    "根据",
    "基于",
    "整体",
    "大致",
    "最终",
}

_DESCRIPTION_CATEGORICAL_VALUE_LIMIT = 10
_DESCRIPTION_MAX_LENGTH = 2000
_PROVIDER_HEALTH_CACHE_TTL_SECONDS = 15.0
_GUARDRAIL_BLOCK_MARKERS = (
    "sql guardrail blocked query generation",
    "sql_guardrail_error",
    "guardrail blocked",
)
_DIAGNOSTIC_TEXT_MARKERS = (
    "no data",
    "missing data",
    "database has no",
    "blocked",
    "error",
    "失败",
    "错误",
    "没有",
    "无法",
    "需要",
)
_PSEUDO_SUCCESS_MARKERS = (
    "analysis complete",
    "analysis completed",
    "successfully",
    "已完成",
    "分析完成",
    "已经完成",
)


@dataclass
class _DatabaoSession:
    conversation_id: str
    provider_name: str
    uploaded_signature: tuple[tuple[str, int, str | None], ...]
    domain: Any
    agent: Any
    thread: Any
    registered_tables: list[RegisteredTable] = field(default_factory=list)
    context_build_error: str | None = None
    context_replayed: bool = False
    datasource_changed: bool = False
    thread_reset_reason: str | None = None


class _StreamingTextWriter:
    def __init__(self, on_text_chunk: Any):
        self._on_text_chunk = on_text_chunk

    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        return None

    def on_text_chunk(self, text: str) -> None:
        if not text:
            return
        if callable(self._on_text_chunk):
            self._on_text_chunk(text)


class DatabaoRuntime:
    def __init__(self, settings: Settings, *, executor_type: str = "lighthouse"):
        self._settings = settings
        self._executor_type = executor_type
        self._sessions: dict[str, _DatabaoSession] = {}
        self._session_lock = threading.RLock()
        self._description_registry: dict[str, set[str]] = {}
        self._fallback_domain_warning: str | None = None
        self._provider_health_cache: dict[str, tuple[float, ProviderHealth]] = {}

    @staticmethod
    def _looks_like_chinese(text: str) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", text))

    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _is_diagnostic_dataframe(dataframe: pd.DataFrame | None) -> bool:
        if dataframe is None or dataframe.empty:
            return False
        if len(dataframe) > 5:
            return False
        normalized_columns = {_normalize for _normalize in (str(column).strip().lower() for column in dataframe.columns)}
        if {"category", "value"} <= normalized_columns:
            return True
        if {"category", "item", "count"} <= normalized_columns:
            return True
        text_like_columns = [
            column
            for column in dataframe.columns
            if pd.api.types.is_string_dtype(dataframe[column])
            or pd.api.types.is_object_dtype(dataframe[column])
            or is_categorical_dtype(dataframe[column])
        ]
        if not text_like_columns:
            return False
        flattened = " ".join(str(value).lower() for value in dataframe[text_like_columns].fillna("").astype("string").stack())
        if not flattened.strip():
            return False
        return any(marker in flattened for marker in _DIAGNOSTIC_TEXT_MARKERS)

    @staticmethod
    def _failure_response_text(query: str, failure_reason: str | None) -> str:
        if DatabaoRuntime._looks_like_chinese(query):
            reason = failure_reason or "本次查询未能通过 SQL 守护与结果校验。"
            return (
                f"本次请求未能完成：{reason}。"
                "系统已严格中止后续业务总结、图表和解释，以避免输出误导性结果。"
                "请调整查询或检查数据后重试。"
            )
        reason = failure_reason or "the query did not pass SQL guardrail and result validation."
        return (
            f"I couldn't complete this request: {reason} "
            "I stopped business summary, chart generation, and result explanation to avoid misleading output. "
            "Please adjust the query or dataset and retry."
        )

    @staticmethod
    def _enforce_failed_completion_validation(
        validation: dict[str, Any] | None,
        *,
        failure_reason: str | None,
        chart_requested: bool,
    ) -> dict[str, Any]:
        payload = dict(validation or {})
        payload["status"] = "failed"
        payload["supplement_added"] = False
        if failure_reason:
            payload["failure_reason"] = failure_reason
        missing = list(payload.get("missing_deliverables") or [])
        for deliverable in ("table", "chart", "explanation"):
            if deliverable == "chart" and not chart_requested:
                continue
            if deliverable not in missing:
                missing.append(deliverable)
        payload["missing_deliverables"] = missing
        return payload

    @staticmethod
    def _detect_turn_failure_state(
        *,
        text: str,
        dataframe: pd.DataFrame | None,
    ) -> tuple[str | None, str | None]:
        lowered = str(text or "").lower()
        if any(marker in lowered for marker in _GUARDRAIL_BLOCK_MARKERS):
            return "sql_guardrail_blocked", "SQL guardrail blocked query generation."
        if DatabaoRuntime._is_diagnostic_dataframe(dataframe):
            return "diagnostic_only_result", "Runtime returned diagnostic-only dataframe instead of business result."
        return None, None

    @staticmethod
    def _extract_sql_guardrail_trace(thread_meta: dict[str, Any]) -> dict[str, Any]:
        trace: dict[str, Any] = {
            "query_obligations": thread_meta.get("query_obligations"),
            "sql_retry_history": list(thread_meta.get("sql_retry_history") or []),
            "sql_guardrail_reports": [],
            "final_failure_reason": thread_meta.get("guardrail_stop_reason"),
            "parsed_sql_summary": thread_meta.get("parsed_sql_summary"),
            "execution_validation_report": thread_meta.get("execution_validation_report"),
            "final_guardrail_status": thread_meta.get("final_guardrail_status"),
            "repair_attempts": list(thread_meta.get("repair_attempts") or []),
        }
        messages = thread_meta.get("messages") or []
        for message in messages:
            if not isinstance(message, ToolMessage):
                continue
            artifact = getattr(message, "artifact", None)
            if not isinstance(artifact, dict):
                continue
            if artifact.get("error_type") != "sql_guardrail":
                continue
            payload = artifact.get("guardrail")
            if not isinstance(payload, dict):
                continue
            report = payload.get("report")
            if isinstance(report, dict):
                trace["sql_guardrail_reports"].append(report)
                trace["parsed_sql_summary"] = report.get("parsed_sql_summary") or trace.get("parsed_sql_summary")
                trace["execution_validation_report"] = (
                    report.get("execution_validation_report") or trace.get("execution_validation_report")
                )
                trace["final_guardrail_status"] = report.get("final_guardrail_status") or trace.get("final_guardrail_status")
                if not trace["repair_attempts"]:
                    trace["repair_attempts"] = list(report.get("repair_attempts") or [])
                if trace.get("query_obligations") is None:
                    trace["query_obligations"] = report.get("obligations")
                trace["final_failure_reason"] = report.get("guardrail_report", {}).get("final_reason") or trace.get(
                    "final_failure_reason"
                )
        if trace["sql_guardrail_reports"] and not trace["sql_retry_history"]:
            trace["sql_retry_history"] = [
                {"attempt": index + 1, "report": report}
                for index, report in enumerate(trace["sql_guardrail_reports"])
            ]
        return trace

    def _trigger_explicit_deliverable_extraction(self, query: str) -> bool:
        lowered = query.lower()
        list_pattern = bool(re.search(r"(?:^|\n)\s*(?:\d+[.)]|[-*•])\s+\S", query))
        section_pattern = any(
            marker in query
            for marker in ("查询:", "问题:", "要求:", "任务:", "任务清单:", "tasks:", "requirements:")
        )
        marker_hits = sum(1 for marker in _DELIVERABLE_TRIGGER_MARKERS if marker in lowered or marker in query)
        return list_pattern or section_pattern or marker_hits >= 2

    def _llm_json(self, llm_config: "LLMConfig" | None, messages: list[Any]) -> dict[str, Any]:
        if llm_config is None:
            raise ValueError("LLM config is required for structured planning")

        from databao.agent.executors.llm import call_model_with_retry

        model = llm_config.new_chat_model()
        response = call_model_with_retry(model, messages)
        raw_text = getattr(response, "content", response)
        if isinstance(raw_text, list):
            raw_text = "\n".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in raw_text)
        if not isinstance(raw_text, str):
            raw_text = str(raw_text)

        parsed = extract_first_json_object(raw_text)
        if parsed is None:
            raise ValueError("LLM response did not contain valid JSON")
        return parsed

    def _build_deliverable_extraction_messages(self, query: str) -> list[Any]:
        prompt = (
            "Extract the user's explicit deliverables as concise grounded items.\n"
            "Return only JSON with the shape:\n"
            '{"language":"zh","deliverables":["..."]}\n'
            "Rules:\n"
            "- Keep deliverables at the user's intended granularity.\n"
            "- Do not return the full original text.\n"
            "- Do not invent new requirements.\n"
            "- Preserve the main language of the query.\n"
            f"Query:\n{query}\n"
        )
        return [
            HumanMessage(content="You extract structured deliverables from user questions."),
            HumanMessage(content=prompt),
        ]

    def _extract_explicit_deliverables_with_llm(
        self,
        query: str,
        *,
        llm_config: "LLMConfig" | None = None,
    ) -> tuple[list[str], dict[str, Any]]:
        debug = {"source": "heuristic_local", "language": None}
        if llm_config is None or not self._trigger_explicit_deliverable_extraction(query):
            return [], debug

        try:
            payload = self._llm_json(llm_config, self._build_deliverable_extraction_messages(query))
            deliverables = payload.get("deliverables", [])
            if not isinstance(deliverables, list):
                raise ValueError("deliverables must be a list")
            items = [self._normalize_whitespace(str(item)) for item in deliverables if self._normalize_whitespace(str(item))]
            debug["source"] = "llm"
            debug["language"] = payload.get("language")
            return items, debug
        except Exception as exc:
            debug["error"] = str(exc)
            return [], debug

    def _judge_clause_coverage_with_llm(
        self,
        query: str,
        clause: str,
        assistant_text: str,
        *,
        llm_config: "LLMConfig" | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        debug = {"source": "lexical_heuristic", "reason": None}
        if llm_config is None:
            return False, debug
        prompt = (
            "Judge whether the assistant answer semantically covers the user clause.\n"
            "Return only JSON in this format:\n"
            '{"covered": true, "reason": "..." }\n'
            "Semantically equivalent wording counts as covered.\n"
            "Do not require exact wording.\n"
            f"User query:\n{query}\n\nClause:\n{clause}\n\nAssistant answer:\n{assistant_text}\n"
        )
        try:
            payload = self._llm_json(llm_config, [HumanMessage(content=prompt)])
            covered = bool(payload.get("covered", False))
            debug["source"] = "llm"
            debug["reason"] = payload.get("reason")
            return covered, debug
        except Exception as exc:
            debug["error"] = str(exc)
            return False, debug

    def _build_follow_up_messages(self, query: str, missing_clauses: list[str]) -> list[Any]:
        language = "zh" if self._looks_like_chinese(query + " " + " ".join(missing_clauses)) else "en"
        prompt = (
            "Write a short follow-up that answers only the missing clauses using the same dataframe.\n"
            "Return only JSON in the form:\n"
            '{"follow_up":"..."}\n'
            "Rules:\n"
            "- Stay in the same language as the query.\n"
            "- Keep it short and grounded.\n"
            "- Do not restate the entire table.\n"
            "- Do not invent data.\n"
            f"Language: {language}\n"
            f"Original query:\n{query}\n\nMissing clauses:\n- " + "\n- ".join(missing_clauses)
        )
        return [HumanMessage(content=prompt)]

    def _template_follow_up(self, query: str, missing_clauses: list[str]) -> str:
        if self._looks_like_chinese(query + " " + " ".join(missing_clauses)):
            bullets = "； ".join(missing_clauses)
            return (
                f"请只基于同一个 dataframe 补充未回答的部分：{bullets}。"
                "不要重述整张表。Do not restate the full table."
            )
        bullets = "; ".join(missing_clauses)
        return f"Please answer the missing points from the same dataframe only: {bullets}. Do not restate the full table."

    def provider_status(self) -> ProviderHealth:
        dependency_status = check_runtime_dependencies()
        if not dependency_status.is_ok:
            resolved = resolve_provider_config(self._settings)
            return ProviderHealth(
                provider=resolved.provider,
                base_url=resolved.base_url,
                model=resolved.model,
                connected=False,
                available_models=[],
                api_key_present=resolved.api_key_present,
                fallback_provider=resolved.fallback_provider,
                fallback_connected=False,
                fallback_error=dependency_status.error,
                error=dependency_status.error,
            )

        active = self._cached_provider_health(self._settings.llm_provider)
        fallback_name = active.fallback_provider or self._settings.llm_fallback_provider
        fallback = active if fallback_name == active.provider else self._cached_provider_health(fallback_name)

        return ProviderHealth(
            provider=active.provider,
            base_url=active.base_url,
            model=active.model,
            connected=active.connected,
            available_models=active.available_models,
            api_key_present=active.api_key_present,
            fallback_provider=fallback.provider,
            fallback_connected=fallback.connected,
            fallback_error=fallback.error,
            latency_ms=active.latency_ms,
            error=active.error,
        )

    def drop_session(self, conversation_id: str) -> None:
        with self._session_lock:
            session = self._sessions.pop(conversation_id, None)
        if session is None:
            return

        close_candidates = [
            getattr(session.agent, "close", None),
            getattr(getattr(session.agent, "executor", None), "close", None),
            getattr(getattr(session.agent, "visualizer", None), "close", None),
            getattr(session.domain, "close", None),
        ]
        for close in close_candidates:
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def ask(
        self,
        conversation_id: str,
        query: str,
        *,
        uploaded_contexts: list[UploadedFileContext] | None = None,
        prior_turns: list[ConversationTurn] | None = None,
        stream_writer: Any | None = None,
    ) -> tuple[DatabaoTurnResult, DatabaoSessionSnapshot]:
        self._ensure_runtime_ready()
        uploaded = uploaded_contexts or []
        history_turns = prior_turns or []
        session = self._ensure_session(
            conversation_id,
            uploaded_contexts=uploaded,
            prior_turns=history_turns,
        )
        try:
            result = self._run_turn(session, query, prior_turns=history_turns, stream_writer=stream_writer)
            return result, self._snapshot(session)
        except Exception as exc:
            fallback_result = self._maybe_retry_with_fallback(
                conversation_id,
                query,
                uploaded,
                history_turns,
                session,
                exc,
                stream_writer=stream_writer,
            )
            if fallback_result is not None:
                return fallback_result
            raise

    def ask_stream(
        self,
        conversation_id: str,
        query: str,
        *,
        uploaded_contexts: list[UploadedFileContext] | None = None,
        prior_turns: list[ConversationTurn] | None = None,
    ):
        event_queue: queue.Queue[dict[str, Any]] = queue.Queue()

        def _emit_chunk(text: str) -> None:
            if text:
                event_queue.put({"type": "chunk", "text": text})

        stream_writer = _StreamingTextWriter(_emit_chunk)

        def _worker() -> None:
            try:
                result, snapshot = self.ask(
                    conversation_id,
                    query,
                    uploaded_contexts=uploaded_contexts,
                    prior_turns=prior_turns,
                    stream_writer=stream_writer,
                )
                event_queue.put({"type": "final", "result": result, "snapshot": snapshot})
            except Exception as exc:
                event_queue.put({"type": "error", "error": str(exc)})

        threading.Thread(target=_worker, daemon=True).start()

        while True:
            event = event_queue.get()
            yield event
            if event.get("type") in {"final", "error"}:
                break

    def _get_session(self, conversation_id: str) -> _DatabaoSession | None:
        with self._session_lock:
            return self._sessions.get(conversation_id)

    def _publish_session(
        self,
        conversation_id: str,
        session: _DatabaoSession,
        *,
        expected_existing: _DatabaoSession | None,
    ) -> _DatabaoSession:
        with self._session_lock:
            current = self._sessions.get(conversation_id)
            if current is expected_existing:
                self._sessions[conversation_id] = session
                return session
            return current if current is not None else session

    def _ensure_session(
        self,
        conversation_id: str,
        *,
        uploaded_contexts: list[UploadedFileContext],
        prior_turns: list[ConversationTurn] | None = None,
        provider_name: str | None = None,
    ) -> _DatabaoSession:
        signature = self._signature(uploaded_contexts)
        existing = self._get_session(conversation_id)
        target_provider = normalize_provider_name(provider_name or self._settings.llm_provider)
        if existing and existing.uploaded_signature == signature and existing.provider_name == target_provider:
            existing.context_replayed = False
            existing.datasource_changed = False
            existing.thread_reset_reason = None
            return existing

        replay_turns = prior_turns or []
        datasource_rebuilt = (
            existing is not None
            and existing.provider_name == target_provider
            and existing.uploaded_signature != signature
        )
        should_replay = datasource_rebuilt and bool(replay_turns)
        session = self._build_session(
            conversation_id,
            uploaded_contexts=uploaded_contexts,
            datasource_changed=existing is not None,
            context_replayed=should_replay,
            thread_reset_reason=(
                "datasource_rebuilt_with_history_replay"
                if should_replay
                else (
                    "datasource_rebuilt"
                    if datasource_rebuilt
                    else ("provider_changed" if existing is not None else None)
                )
            ),
            provider_name=provider_name,
        )
        if should_replay:
            self._replay_prior_turns(session, replay_turns)
        if provider_name is None:
            session = self._publish_session(conversation_id, session, expected_existing=existing)
        return session

    def _build_session(
        self,
        conversation_id: str,
        *,
        uploaded_contexts: list[UploadedFileContext],
        datasource_changed: bool,
        context_replayed: bool = False,
        thread_reset_reason: str | None = None,
        provider_name: str | None = None,
    ) -> _DatabaoSession:
        domain = self._create_domain()
        context_build_error: str | None = self._fallback_domain_warning
        self._fallback_domain_warning = None
        llm_config = self._build_llm_config(provider_name=provider_name)
        registered_tables = self._register_uploaded_sources(domain, uploaded_contexts)
        self._add_text_descriptions(domain, uploaded_contexts)

        if domain.supports_context and not domain.is_context_built():
            try:
                domain.build_context()
            except Exception as exc:
                context_build_error = str(exc)

        agent = self._create_agent(domain, llm_config)
        thread = agent.thread(cache_scope=f"fsda/{conversation_id}")

        return _DatabaoSession(
            conversation_id=conversation_id,
            provider_name=self._resolve_provider_config(provider_name).provider,
            uploaded_signature=self._signature(uploaded_contexts),
            domain=domain,
            agent=agent,
            thread=thread,
            registered_tables=registered_tables,
            context_build_error=context_build_error,
            context_replayed=context_replayed,
            datasource_changed=datasource_changed,
            thread_reset_reason=thread_reset_reason or ("datasource_changed" if datasource_changed else None),
        )

    def _build_llm_config(self, provider_name: str | None = None) -> "LLMConfig":
        from databao.agent.configs.llm import LLMConfig

        resolved = self._resolve_provider_config(provider_name)
        if resolved.provider == "deepseek":
            model_kwargs: dict[str, Any] = {}
            if resolved.api_key:
                model_kwargs["api_key"] = resolved.api_key
            return LLMConfig(
                name=resolved.model,
                temperature=resolved.temperature,
                timeout=int(resolved.timeout),
                api_base_url=resolved.base_url,
                use_responses_api=False,
                ollama_pull_model=False,
                model_kwargs=model_kwargs,
            )

        model_name = resolved.model
        if not model_name.startswith(("ollama:", "openai:", "anthropic:", "gemini:")):
            model_name = f"ollama:{model_name}"
        return LLMConfig(
            name=model_name,
            temperature=resolved.temperature,
            timeout=int(resolved.timeout),
            api_base_url=None,
            use_responses_api=False,
            ollama_pull_model=False,
            model_kwargs={
                "num_ctx": resolved.num_ctx,
                "validate_model_on_init": True,
            },
        )

    def _create_domain(self) -> Any:
        from databao.agent import api as bao_api

        try:
            return bao_api.domain(self._settings.domain_dir)
        except ValueError as exc:
            # DCE project domains can contain sources that are not configurable enough
            # to be restored by the Databao agent domain adapter. Fall back to an
            # in-memory domain so CSV uploads and the default lighthouse path stay usable.
            self._fallback_domain_warning = str(exc)
            return bao_api.domain(None)

    def _create_agent(self, domain: Any, llm_config: "LLMConfig") -> Any:
        from databao.agent import api as bao_api
        from databao.agent.visualizers.seaborn_chat import SeabornChatVisualizer

        return bao_api.agent(
            domain,
            name="fsda",
            llm_config=llm_config,
            executor_type=self._executor_type,
            stream_ask=False,
            stream_plot=False,
            auto_output_modality=True,
            visualizer=SeabornChatVisualizer(llm_config),
        )

    def _resolve_provider_config(self, provider_name: str | None = None) -> ResolvedProviderConfig:
        return resolve_provider_config(self._settings, provider_name=provider_name)

    def _cached_provider_health(self, provider_name: str | None = None) -> ProviderHealth:
        resolved_name = normalize_provider_name(provider_name or self._settings.llm_provider)
        cached = self._provider_health_cache.get(resolved_name)
        now = time.time()
        if cached is not None:
            timestamp, health = cached
            if now - timestamp <= _PROVIDER_HEALTH_CACHE_TTL_SECONDS:
                return health

        health = probe_provider_health(self._settings, provider_name=resolved_name)
        self._provider_health_cache[resolved_name] = (now, health)
        return health

    @contextmanager
    def _streaming_thread_writer(self, session: _DatabaoSession, stream_writer: Any | None):
        thread = session.thread
        previous_writer = getattr(thread, "_writer", None)
        if stream_writer is not None:
            thread._writer = stream_writer
        try:
            yield
        finally:
            if stream_writer is not None:
                thread._writer = previous_writer

    def _run_turn(
        self,
        session: _DatabaoSession,
        query: str,
        *,
        prior_turns: list[ConversationTurn] | None = None,
        stream_writer: Any | None = None,
    ) -> DatabaoTurnResult:
        chart_requested = self._has_explicit_chart_intent(query)
        chart_intent = self._extract_chart_intent(query)
        turn_failure_state: str | None = None
        failure_reason: str | None = None
        contract_debug: dict[str, Any] = {}
        with self._streaming_thread_writer(session, stream_writer):
            thread = session.thread.ask(query)
            dataframe = thread.df(rows_limit=200)
            thread_meta = thread.meta()
            sql_guardrail_trace = self._extract_sql_guardrail_trace(thread_meta)
            raw_text = thread.text()
            turn_failure_state, failure_reason = self._detect_turn_failure_state(
                text=raw_text,
                dataframe=dataframe,
            )
            if sql_guardrail_trace.get("final_failure_reason"):
                turn_failure_state = "sql_guardrail_blocked"
                failure_reason = str(sql_guardrail_trace.get("final_failure_reason"))
            chart_generation_allowed = turn_failure_state is None
            if chart_generation_allowed:
                plot_result, plot_error = self._maybe_collect_plot(thread, query)
                if plot_result is None and plot_error is None:
                    auto_plot_result = self._auto_visualization_result(thread)
                    if auto_plot_result is not None:
                        plot_result = auto_plot_result
                        chart_requested = True
            else:
                plot_result, plot_error = None, None
            text, completion_validation = self._complete_response(
                thread,
                query,
                raw_text,
                dataframe,
                llm_config=session.agent.llm_config,
                allow_follow_up=chart_generation_allowed,
                failure_reason=failure_reason,
            )
            plot_result, plot_error, contract_debug = self._enforce_chart_contract(
                thread=thread,
                query=query,
                chart_requested=chart_requested and chart_generation_allowed,
                dataframe=dataframe,
                plot_result=plot_result,
                plot_error=plot_error,
                turn_failure_state=turn_failure_state,
            )

        preview = dataframe.head(10).to_dict(orient="records") if dataframe is not None else None
        columns = [str(column) for column in dataframe.columns] if dataframe is not None else None
        row_count = int(len(dataframe)) if dataframe is not None else None

        plot_plan = getattr(plot_result, "chart_plan", None) if plot_result is not None else None
        plot_spec = getattr(plot_result, "spec", None) if plot_result is not None else None
        plot_data_frame = getattr(plot_result, "spec_df", None) if plot_result is not None else None
        plot_data = plot_data_frame.to_dict(orient="records") if plot_data_frame is not None else None
        plot_meta = getattr(plot_result, "meta", None) if plot_result is not None else None
        if plot_plan is None and isinstance(plot_meta, dict):
            plot_plan = plot_meta.get("chart_plan") or plot_meta.get("plot_config")
        if plot_plan is None and plot_result is not None:
            plot_plan = getattr(plot_result, "plot_config", None)
        if plot_spec is None and isinstance(plot_plan, dict):
            plot_spec = dict(plot_plan)
        visualizer_chart_debug = dict(plot_meta.get("chart_debug") or {}) if isinstance(plot_meta, dict) else {}
        visualizer_plot_error = str(plot_meta.get("plot_error")) if isinstance(plot_meta, dict) and plot_meta.get("plot_error") else None
        effective_plot_error = visualizer_plot_error or plot_error
        plot_backend = self._plot_backend(plot_result)
        plot_kind = self._plot_kind(plot_result, plot_spec, plot_meta)
        plot_image_base64, plot_image_mime_type = self._plot_image_artifact(plot_result)
        plot_data_rows = len(plot_data) if plot_data is not None else 0
        chart_renderable = bool(
            plot_result is not None
            and (
                getattr(plot_result, "plot", None) is not None
                or plot_plan is not None
                or (plot_spec is not None and plot_data_frame is not None)
                or plot_image_base64 is not None
            )
        )
        planner_status = visualizer_chart_debug.get("planner_status")
        upstream_chart_failed = planner_status in {"planning_failed", "schema_parse_failed", "validation_failed", "render_failed"}
        if chart_requested and upstream_chart_failed and turn_failure_state is None:
            turn_failure_state = "chart_planner_failed"
            failure_reason = str(visualizer_chart_debug.get("render_error") or visualizer_chart_debug.get("planner_error") or "Chart planner failed.")
        chart_generated = plot_result is not None and effective_plot_error is None and not upstream_chart_failed
        if chart_requested and not chart_generated and turn_failure_state is None and effective_plot_error:
            turn_failure_state = "chart_generation_failed"
            failure_reason = str(effective_plot_error)

        session_provider = getattr(session, "provider_name", None) or self._settings.llm_provider
        resolved = self._resolve_provider_config(session_provider)
        chart_renderer = plot_backend
        chart_type = plot_kind
        if plot_result is not None:
            plot_object = getattr(plot_result, "plot", None)
            if plot_object is not None:
                chart_renderer = type(plot_object).__name__
            elif plot_plan is not None:
                chart_renderer = chart_renderer or "seaborn_chart_plan"
                chart_type = chart_type or str((plot_plan or {}).get("kind") or "chart_plan")
            elif plot_spec is not None:
                chart_renderer = chart_renderer or "serialized_chart_spec"
                chart_type = chart_type or str((plot_spec or {}).get("mark") or "chart_spec")

        chart_artifact_id = self._build_chart_artifact_id(
            query=query,
            provider=resolved.provider,
            model=resolved.model,
            plot_code=getattr(plot_result, "code", None) if plot_result is not None else None,
            plot_spec=plot_spec,
            plot_data=plot_data,
            plot_backend=plot_backend,
            plot_kind=plot_kind,
            plot_image_base64=plot_image_base64,
            plot_error=effective_plot_error,
        )
        chart_failure_stage = None
        chart_failure_reason = None
        if chart_requested:
            if upstream_chart_failed:
                chart_failure_stage = str(planner_status)
                validation_errors = visualizer_chart_debug.get("validation_errors") or []
                render_error = visualizer_chart_debug.get("render_error")
                planner_error = visualizer_chart_debug.get("planner_error")
                if render_error:
                    chart_failure_reason = str(render_error)
                elif validation_errors:
                    chart_failure_reason = "; ".join(str(error) for error in validation_errors)
                elif planner_error:
                    chart_failure_reason = str(planner_error)
                else:
                    chart_failure_reason = "chart generation failed upstream"
            elif turn_failure_state is not None:
                chart_failure_stage = "blocked_by_turn_failure"
                chart_failure_reason = failure_reason or "Chart generation blocked because turn failed."
            elif plot_result is None and effective_plot_error is None:
                chart_failure_stage = "generation_failed"
                chart_failure_reason = "chart request was detected but no chart artifact was produced"
            elif effective_plot_error:
                chart_failure_stage = "generation_failed"
                chart_failure_reason = effective_plot_error
            elif not chart_renderable:
                chart_failure_stage = "generation_failed"
                chart_failure_reason = "chart artifact is present but not renderable"

        chart_debug = {
            "chart_requested": chart_requested,
            "chart_intent": chart_intent,
            "chart_generation_called": chart_requested and turn_failure_state is None,
            "chart_generated": chart_generated,
            "chart_renderable": chart_renderable,
            "chart_renderer": chart_renderer,
            "chart_type": chart_type,
            "plot_backend": plot_backend,
            "plot_kind": plot_kind,
            "plot_image_present": plot_image_base64 is not None,
            "chart_artifact_id": chart_artifact_id,
            "plot_spec_present": plot_spec is not None,
            "plot_data_rows": plot_data_rows,
            "chart_saved_to_history": True,
            "chart_render_called": False,
            "chart_container_width": "container",
            "chart_container_height": 360,
            "chart_failure_stage": chart_failure_stage,
            "chart_failure_reason": chart_failure_reason,
            "plot_error": effective_plot_error,
            "planner": visualizer_chart_debug.get("planner"),
            "planner_status": planner_status,
            "parsed_plan": visualizer_chart_debug.get("parsed_plan"),
            "final_plan": visualizer_chart_debug.get("final_plan"),
            "validation_errors": visualizer_chart_debug.get("validation_errors"),
            "render_error": visualizer_chart_debug.get("render_error"),
            "repair_used": visualizer_chart_debug.get("repair_used"),
            "raw_planner_response": visualizer_chart_debug.get("raw_planner_response"),
            "raw_repair_response": visualizer_chart_debug.get("raw_repair_response"),
            "fallback_blocked": visualizer_chart_debug.get("fallback_blocked"),
            "fallback_reason": visualizer_chart_debug.get("fallback_reason"),
            "turn_failure_state": turn_failure_state,
            "failure_reason": failure_reason,
        }
        if visualizer_chart_debug:
            chart_debug.update(visualizer_chart_debug)
            chart_debug["chart_generated"] = chart_generated
            chart_debug["chart_failure_stage"] = chart_failure_stage
            chart_debug["chart_failure_reason"] = chart_failure_reason
            chart_debug["plot_error"] = effective_plot_error
        if contract_debug:
            chart_debug.update(contract_debug)

        if turn_failure_state is not None:
            completion_validation = self._enforce_failed_completion_validation(
                completion_validation,
                failure_reason=failure_reason,
                chart_requested=chart_requested,
            )
            if any(marker in str(text or "").lower() for marker in _PSEUDO_SUCCESS_MARKERS):
                text = self._failure_response_text(query, failure_reason)

        thread_meta = {
            **thread_meta,
            "provider_used": resolved.provider,
            "model_used": resolved.model,
            "chart_debug": chart_debug,
            "turn_failure_state": turn_failure_state,
            "failure_reason": failure_reason,
            "business_result_present": turn_failure_state is None and dataframe is not None and not dataframe.empty,
            "query_obligations": sql_guardrail_trace.get("query_obligations"),
            "parsed_sql_summary": sql_guardrail_trace.get("parsed_sql_summary"),
            "sql_guardrail_report": (sql_guardrail_trace.get("sql_guardrail_reports") or [None])[-1],
            "execution_validation_report": sql_guardrail_trace.get("execution_validation_report"),
            "sql_retry_history": sql_guardrail_trace.get("sql_retry_history"),
            "final_guardrail_status": sql_guardrail_trace.get("final_guardrail_status"),
            "repair_attempts": sql_guardrail_trace.get("repair_attempts"),
            "final_failure_reason": sql_guardrail_trace.get("final_failure_reason"),
        }
        turn_id = str(thread_meta.get("turn_id") or uuid4())
        workspace = self._build_result_workspace(
            conversation_id=session.conversation_id,
            turn_id=turn_id,
            dataframe=dataframe,
            preview=preview,
            columns=columns,
            row_count=row_count,
            text=text,
            plot_result=plot_result,
            plot_plan=plot_plan,
            plot_spec=plot_spec,
            plot_data=plot_data,
            plot_meta=plot_meta,
            plot_backend=plot_backend,
            plot_kind=plot_kind,
            plot_image_base64=plot_image_base64,
            plot_error=effective_plot_error,
            chart_artifact_id=chart_artifact_id,
            thread_meta=thread_meta,
            turn_failure_state=turn_failure_state,
            failure_reason=failure_reason,
        )
        recent_turn_metadatas = self._recent_turn_metadata_snapshots(prior_turns)
        grounded_response = self._build_grounded_response(
            query=query,
            workspace=workspace,
            recent_turn_metadatas=recent_turn_metadatas,
            use_llm=True,
            turn_failure_state=turn_failure_state,
        )
        workspace_table_artifact = workspace.resolve_artifact(grounded_response.primary_table_artifact_id)
        workspace_chart_artifact = workspace.resolve_artifact(grounded_response.primary_chart_artifact_id)

        projected_preview = (
            workspace_table_artifact.dataframe_preview
            if workspace_table_artifact is not None
            else preview
        )
        projected_columns = (
            list((workspace_table_artifact.metadata or {}).get("columns") or [])
            if workspace_table_artifact is not None
            else (columns or [])
        )
        projected_row_count = (
            (workspace_table_artifact.metadata or {}).get("row_count")
            if workspace_table_artifact is not None
            else row_count
        )
        projected_plot_plan = workspace_chart_artifact.chart_plan if workspace_chart_artifact is not None else plot_plan
        projected_plot_spec = workspace_chart_artifact.chart_spec if workspace_chart_artifact is not None else plot_spec
        projected_plot_data = workspace_chart_artifact.chart_data if workspace_chart_artifact is not None else plot_data
        projected_plot_meta = workspace_chart_artifact.chart_meta if workspace_chart_artifact is not None else plot_meta
        projected_plot_backend = (
            (workspace_chart_artifact.metadata or {}).get("plot_backend")
            if workspace_chart_artifact is not None
            else plot_backend
        )
        projected_plot_kind = (
            (workspace_chart_artifact.metadata or {}).get("plot_kind")
            if workspace_chart_artifact is not None
            else plot_kind
        )
        projected_plot_image_base64 = (
            (workspace_chart_artifact.metadata or {}).get("plot_image_base64")
            if workspace_chart_artifact is not None
            else plot_image_base64
        )
        thread_meta = {
            **thread_meta,
            "turn_id": turn_id,
            "result_workspace": workspace.to_dict(),
            "grounded_response": grounded_response.to_dict(),
            "primary_artifact_id": grounded_response.primary_table_artifact_id or grounded_response.primary_text_artifact_id,
            "primary_table_artifact_id": grounded_response.primary_table_artifact_id,
            "primary_chart_artifact_id": grounded_response.primary_chart_artifact_id,
            "followup_target_artifact_id": grounded_response.followup_target_artifact_id,
            "binding_intent": grounded_response.render_payload.get("binding_intent"),
            "binding_bundle": grounded_response.render_payload.get("binding_bundle"),
            "binding_decisions": grounded_response.render_payload.get("binding_decisions"),
            "decision_mode": grounded_response.render_payload.get("decision_mode"),
            "llm_used": grounded_response.render_payload.get("llm_used", False),
            "turn_failure_state": turn_failure_state,
            "failure_reason": failure_reason,
            "business_result_present": turn_failure_state is None and dataframe is not None and not dataframe.empty,
        }
        result = DatabaoTurnResult(
            text=text,
            dataframe=dataframe,
            dataframe_preview=projected_preview,
            columns=projected_columns,
            row_count=projected_row_count,
            provider_used=resolved.provider,
            model_used=resolved.model,
            plot_code=getattr(plot_result, "code", None) if plot_result is not None else thread_meta.get("plot_code"),
            plot_object=plot_result,
            plot_plan=projected_plot_plan,
            plot_spec=projected_plot_spec,
            plot_data=projected_plot_data,
            plot_meta=projected_plot_meta,
            plot_backend=projected_plot_backend,
            plot_kind=projected_plot_kind,
            plot_image_base64=projected_plot_image_base64,
            plot_image_mime_type=plot_image_mime_type,
            plot_error=effective_plot_error,
            chart_debug=chart_debug,
            completion_validation=completion_validation,
            thread_meta=thread_meta,
            result_workspace=workspace,
            grounded_response=grounded_response,
            primary_artifact_id=grounded_response.primary_table_artifact_id or grounded_response.primary_text_artifact_id,
            primary_table_artifact_id=grounded_response.primary_table_artifact_id,
            primary_chart_artifact_id=grounded_response.primary_chart_artifact_id,
            followup_target_artifact_id=grounded_response.followup_target_artifact_id,
            binding_intent=grounded_response.render_payload.get("binding_intent"),
            binding_bundle=grounded_response.render_payload.get("binding_bundle"),
            binding_decisions=grounded_response.render_payload.get("binding_decisions"),
            decision_mode=grounded_response.render_payload.get("decision_mode"),
            llm_used=bool(grounded_response.render_payload.get("llm_used", False)),
            turn_failure_state=turn_failure_state,
            failure_reason=failure_reason,
            business_result_present=turn_failure_state is None and dataframe is not None and not dataframe.empty,
            used_databao=True,
        )
        return result

    @staticmethod
    def _infer_dataframe_artifact_type(
        dataframe: pd.DataFrame | None,
        thread_meta: dict[str, Any],
        plot_meta: dict[str, Any] | None,
    ) -> str:
        if dataframe is None:
            return "filtered_df"
        lowered_keys = " ".join(str(key).lower() for key in (thread_meta or {}).keys())
        lowered_meta = json.dumps(plot_meta or {}, default=str).lower()
        aggregation_markers = ("group", "aggregate", "aggregation", "count", "mean", "sum", "avg")
        if any(marker in lowered_keys or marker in lowered_meta for marker in aggregation_markers):
            return "grouped_df"
        if isinstance(dataframe.columns, pd.Index) and any(str(column).lower().startswith(("avg_", "sum_", "count_")) for column in dataframe.columns):
            return "grouped_df"
        return "filtered_df"

    @staticmethod
    def _maybe_extract_scalar_artifact(
        dataframe: pd.DataFrame | None,
        *,
        parent_artifact_id: str | None,
    ) -> ResultArtifact | None:
        if dataframe is None or dataframe.shape != (1, 1):
            return None
        value = dataframe.iat[0, 0]
        return ResultArtifact(
            artifact_id=f"scalar:{parent_artifact_id or 'result'}",
            artifact_type="scalar",
            name="Scalar Result",
            parent_artifact_id=parent_artifact_id,
            created_by_step="turn_result",
            scalar_value=value,
            metadata={"source": "dataframe_cell"},
        )

    @staticmethod
    def _build_chart_render_payload(
        plot_result: Any | None,
        *,
        plot_plan: dict[str, Any] | None,
        plot_spec: dict[str, Any] | None,
        plot_data: list[dict[str, Any]] | None,
        plot_meta: dict[str, Any] | None,
        plot_backend: str | None,
        plot_kind: str | None,
        plot_image_base64: str | None,
        plot_error: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        runtime_handles: dict[str, Any] = {}
        render_kind = "unknown"
        heavy_runtime_object = None
        plot_attr = getattr(plot_result, "plot", None) if plot_result is not None else None
        serializable_plan = plot_plan

        if plot_image_base64 is not None:
            render_kind = "image_base64"
        elif serializable_plan is not None:
            render_kind = "chart_plan"
        elif isinstance(plot_attr, Axes):
            render_kind = "matplotlib_axes"
            heavy_runtime_object = plot_attr
        elif isinstance(plot_attr, Figure):
            render_kind = "matplotlib_figure"
            heavy_runtime_object = plot_attr
        elif plot_attr is not None:
            render_kind = "plot_attr"
            heavy_runtime_object = plot_attr
        elif isinstance(plot_result, Axes):
            render_kind = "matplotlib_axes"
            heavy_runtime_object = plot_result
        elif isinstance(plot_result, Figure):
            render_kind = "matplotlib_figure"
            heavy_runtime_object = plot_result
        elif plot_result is not None:
            render_kind = "runtime_handle"
            heavy_runtime_object = plot_result

        if plot_result is not None:
            runtime_handles["plot_result"] = plot_result
        if heavy_runtime_object is not None:
            runtime_handles["chart_runtime_object"] = heavy_runtime_object

        render_payload = {
            "render_kind": render_kind,
            "chart_plan": serializable_plan,
            "chart_spec": None,
            "chart_data": plot_data,
            "chart_meta": plot_meta,
            "plot_image_base64": plot_image_base64,
            "plot_backend": plot_backend,
            "plot_kind": plot_kind,
            "plot_error": plot_error,
            "has_heavy_runtime_object": bool(runtime_handles),
        }
        return render_payload, runtime_handles

    def _build_result_workspace(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        dataframe: pd.DataFrame | None,
        preview: list[dict[str, Any]] | None,
        columns: list[str] | None,
        row_count: int | None,
        text: str,
        plot_result: Any | None,
        plot_plan: dict[str, Any] | None,
        plot_spec: dict[str, Any] | None,
        plot_data: list[dict[str, Any]] | None,
        plot_meta: dict[str, Any] | None,
        plot_backend: str | None,
        plot_kind: str | None,
        plot_image_base64: str | None,
        plot_error: str | None,
        chart_artifact_id: str,
        thread_meta: dict[str, Any],
        turn_failure_state: str | None,
        failure_reason: str | None,
    ) -> ResultWorkspace:
        workspace = ResultWorkspace(conversation_id=conversation_id, turn_id=turn_id)
        dataframe_artifact_id: str | None = None
        dataframe_purpose = "diagnostic" if turn_failure_state is not None else "business_result"
        if dataframe is not None:
            dataframe_artifact_type = self._infer_dataframe_artifact_type(dataframe, thread_meta, plot_meta)
            dataframe_artifact = workspace.register_artifact(
                ResultArtifact(
                    artifact_id=f"{dataframe_artifact_type}:{turn_id}",
                    artifact_type=dataframe_artifact_type,
                    name="Query Result DataFrame",
                    created_by_step="turn_result",
                    dataframe=dataframe,
                    dataframe_preview=preview,
                    metadata={
                        "columns": list(columns or []),
                        "row_count": row_count,
                        "turn_failure_state": turn_failure_state,
                        "failure_reason": failure_reason,
                    },
                    artifact_purpose=dataframe_purpose,
                )
            )
            dataframe_artifact_id = dataframe_artifact.artifact_id

        text_artifact = workspace.register_artifact(
            ResultArtifact(
                artifact_id=f"text_answer:{turn_id}",
                artifact_type="text_answer",
                name="Answer Text",
                parent_artifact_id=dataframe_artifact_id,
                created_by_step="answer_completion",
                text_value=text,
                metadata={"length": len(text), "turn_failure_state": turn_failure_state, "failure_reason": failure_reason},
                artifact_purpose="diagnostic" if turn_failure_state is not None else "business_result",
            )
        )

        if plot_result is not None or plot_plan is not None or plot_spec is not None or plot_image_base64 is not None:
            render_payload, runtime_handles = self._build_chart_render_payload(
                plot_result,
                plot_plan=plot_plan,
                plot_spec=plot_spec,
                plot_data=plot_data,
                plot_meta=plot_meta,
                plot_backend=plot_backend,
                plot_kind=plot_kind,
                plot_image_base64=plot_image_base64,
                plot_error=plot_error,
            )
            workspace.register_artifact(
                ResultArtifact(
                    artifact_id=f"chart:{chart_artifact_id}",
                    artifact_type="chart",
                    name="Chart Result",
                    parent_artifact_id=dataframe_artifact_id,
                    created_by_step="chart_generation",
                    chart_plan=plot_plan,
                    chart_spec=plot_spec,
                    chart_data=plot_data,
                    chart_meta=plot_meta,
                    render_payload=render_payload,
                    runtime_handles=runtime_handles,
                    metadata={
                        "plot_backend": plot_backend,
                        "plot_kind": plot_kind,
                        "plot_image_base64": plot_image_base64,
                        "plot_error": plot_error,
                    },
                    artifact_purpose="business_result" if turn_failure_state is None else "diagnostic",
                )
            )

        scalar_artifact = self._maybe_extract_scalar_artifact(dataframe, parent_artifact_id=dataframe_artifact_id)
        if scalar_artifact is not None:
            workspace.register_artifact(scalar_artifact)

        if workspace.root_artifact_id is None:
            workspace.root_artifact_id = text_artifact.artifact_id
        return workspace

    @staticmethod
    def _recent_turn_metadata_snapshots(prior_turns: list[ConversationTurn] | None) -> list[dict[str, Any]]:
        snapshots: list[dict[str, Any]] = []
        for turn in prior_turns or []:
            metadata = dict(getattr(turn, "metadata", None) or {})
            if not metadata:
                continue
            snapshots.append(
                {
                    "result_workspace": metadata.get("result_workspace"),
                    "grounded_response": metadata.get("grounded_response"),
                    "primary_artifact_id": metadata.get("primary_artifact_id"),
                    "primary_table_artifact_id": metadata.get("primary_table_artifact_id"),
                    "primary_chart_artifact_id": metadata.get("primary_chart_artifact_id"),
                    "followup_target_artifact_id": metadata.get("followup_target_artifact_id"),
                    "binding_intent": metadata.get("binding_intent"),
                    "binding_bundle": metadata.get("binding_bundle"),
                    "binding_decisions": metadata.get("binding_decisions"),
                    "decision_mode": metadata.get("decision_mode"),
                    "llm_used": metadata.get("llm_used", False),
                }
            )
        return snapshots

    def _build_grounded_response(
        self,
        *,
        query: str,
        workspace: ResultWorkspace,
        recent_turn_metadatas: list[dict[str, Any]] | None = None,
        use_llm: bool = True,
        turn_failure_state: str | None = None,
    ) -> GroundedResponse:
        if turn_failure_state:
            text_artifact = workspace.latest("text_answer")
            primary_text = text_artifact.artifact_id if text_artifact is not None else None
            available_actions_by_artifact = {
                artifact.artifact_id: list(artifact.available_actions)
                for artifact in workspace.artifacts_by_id.values()
            }
            referenced_ids = [artifact_id for artifact_id in (primary_text,) if artifact_id is not None]
            return GroundedResponse(
                primary_text_artifact_id=primary_text,
                primary_table_artifact_id=None,
                primary_chart_artifact_id=None,
                primary_explain_artifact_id=primary_text,
                referenced_artifact_ids=referenced_ids,
                followup_target_artifact_id=primary_text,
                available_actions_by_artifact=available_actions_by_artifact,
                render_payload={
                    "turn_failure_state": turn_failure_state,
                    "decision_mode": "deterministic",
                    "llm_used": False,
                },
            )
        binding_intent = parse_binding_intent(query)
        binding_intent = enrich_binding_intent_with_llm(binding_intent, settings=self._settings, use_llm=use_llm)
        binding_bundle = resolve_binding_bundle(
            workspace,
            query=query,
            recent_turn_metadatas=recent_turn_metadatas,
            binding_intent=binding_intent,
            use_llm=use_llm,
            llm_settings=self._settings,
        )
        primary_text = binding_bundle.text_target or (workspace.latest("text_answer").artifact_id if workspace.latest("text_answer") is not None else None)
        primary_table = binding_bundle.table_target
        table_artifact = workspace.resolve_artifact(primary_table)
        if table_artifact is None or not table_artifact.is_dataframe_like():
            for artifact_type in ("grouped_df", "filtered_df", "raw_df"):
                artifact = workspace.latest(artifact_type)
                if artifact is not None:
                    primary_table = artifact.artifact_id
                    break
        primary_chart = binding_bundle.chart_target
        chart_artifact = workspace.resolve_artifact(primary_chart)
        if chart_artifact is None or not chart_artifact.is_chart_like():
            latest_chart = workspace.latest("chart")
            primary_chart = latest_chart.artifact_id if latest_chart is not None else primary_chart
        primary_explain = binding_bundle.explain_target
        followup_target = binding_bundle.followup_target
        available_actions_by_artifact = {
            artifact.artifact_id: list(artifact.available_actions)
            for artifact in workspace.artifacts_by_id.values()
        }
        referenced_ids = [
            artifact_id
            for artifact_id in (
                primary_text,
                primary_table,
                primary_chart,
                primary_explain,
                followup_target,
            )
            if artifact_id is not None
        ]
        decision_modes = {
            action: decision.decision_mode
            for action, decision in binding_bundle.decisions_by_action.items()
        }
        llm_used = any(decision.llm_used for decision in binding_bundle.decisions_by_action.values())
        decision_mode = next(
            (
                mode
                for mode in (
                    decision_modes.get("followup"),
                    decision_modes.get("explain"),
                    decision_modes.get("show_chart"),
                    decision_modes.get("show_table"),
                    decision_modes.get("answer_text"),
                )
                if mode
            ),
            "deterministic",
        )
        chart_artifact = workspace.resolve_artifact(primary_chart)
        return GroundedResponse(
            primary_text_artifact_id=primary_text,
            primary_table_artifact_id=primary_table,
            primary_chart_artifact_id=primary_chart,
            primary_explain_artifact_id=primary_explain,
            referenced_artifact_ids=referenced_ids,
            followup_target_artifact_id=followup_target,
            available_actions_by_artifact=available_actions_by_artifact,
            render_payload={
                "primary_text_artifact_id": primary_text,
                "primary_table_artifact_id": primary_table,
                "primary_chart_artifact_id": primary_chart,
                "primary_chart_render_payload": dict(chart_artifact.render_payload) if chart_artifact is not None else {},
                "binding_intent": binding_intent.to_dict(),
                "binding_bundle": binding_bundle.to_dict(),
                "binding_decisions": {
                    action: decision.to_dict()
                    for action, decision in binding_bundle.decisions_by_action.items()
                },
                "decision_mode": decision_mode,
                "llm_used": llm_used,
            },
        )

    def _maybe_retry_with_fallback(
        self,
        conversation_id: str,
        query: str,
        uploaded_contexts: list[UploadedFileContext],
        _prior_turns: list[ConversationTurn],
        session: _DatabaoSession,
        exc: Exception,
        *,
        stream_writer: Any | None = None,
    ) -> tuple[DatabaoTurnResult, DatabaoSessionSnapshot] | None:
        if not self._should_fallback(exc):
            return None

        primary = self._resolve_provider_config()
        fallback_provider = normalize_provider_name(primary.fallback_provider, default="ollama")
        if fallback_provider == primary.provider:
            return None

        fallback_session = self._build_session(
            conversation_id,
            uploaded_contexts=uploaded_contexts,
            datasource_changed=session.datasource_changed,
            context_replayed=False,
            thread_reset_reason="provider_fallback",
            provider_name=fallback_provider,
        )
        try:
            result = self._run_turn(
                fallback_session,
                query,
                prior_turns=_prior_turns,
                stream_writer=stream_writer,
            )
        except Exception:
            return None

        result.fallback_triggered = True
        result.fallback_reason = self._provider_error_reason(exc)
        result.provider_used = fallback_provider
        result.model_used = self._resolve_provider_config(fallback_provider).model
        result.thread_meta = {
            **result.thread_meta,
            "provider_used": result.provider_used,
            "model_used": result.model_used,
            "fallback_triggered": True,
            "fallback_reason": result.fallback_reason,
            "primary_provider_error": self._provider_error_reason(exc),
        }
        return result, self._snapshot(fallback_session)

    def _should_fallback(self, exc: Exception) -> bool:
        if isinstance(exc, (ProviderAuthError, ProviderRateLimitError, ProviderTimeoutError, ProviderUnavailableError)):
            return True
        if isinstance(exc, ProviderError):
            return False

        message = f"{exc.__class__.__name__}: {exc}".lower()
        fallback_markers = (
            "authentication",
            "unauthorized",
            "forbidden",
            "rate limit",
            "429",
            "timeout",
            "timed out",
            "connection error",
            "api connection",
            "temporarily unavailable",
            "503",
            "502",
            "504",
        )
        infrastructure_prefixes = ("connection", "network", "request", "transport", "timeout", "temporary", "service unavailable")
        if not any(marker in message for marker in fallback_markers):
            return False
        return any(prefix in message for prefix in infrastructure_prefixes) or any(code in message for code in ("429", "502", "503", "504"))

    @staticmethod
    def _provider_error_reason(exc: Exception) -> str:
        return f"{exc.__class__.__name__}: {exc}"

    @staticmethod
    def _explicit_chart_request_marker(query: str) -> str | None:
        lowered = query.lower()
        for marker in EXPLICIT_CHART_REQUEST_MARKERS:
            if re.search(r"[a-z]", marker):
                pattern = r"(?<![a-z])" + re.escape(marker).replace(r"\ ", r"\s+") + r"(?![a-z])"
                if re.search(pattern, lowered):
                    return marker
            elif marker in query:
                return marker
        return None

    @classmethod
    def _extract_chart_intent(cls, query: str) -> str | None:
        return cls._explicit_chart_request_marker(query)

    @staticmethod
    def _build_chart_artifact_id(
        *,
        query: str,
        provider: str,
        model: str,
        plot_code: str | None,
        plot_spec: dict[str, Any] | None,
        plot_data: list[dict[str, Any]] | None,
        plot_backend: str | None,
        plot_kind: str | None,
        plot_image_base64: str | None,
        plot_error: str | None,
    ) -> str:
        payload = {
            "query": query,
            "provider": provider,
            "model": model,
            "plot_code": plot_code,
            "plot_spec": plot_spec,
            "plot_data_rows": len(plot_data or []),
            "plot_backend": plot_backend,
            "plot_kind": plot_kind,
            "plot_image_present": plot_image_base64 is not None,
            "plot_error": plot_error,
        }
        digest = hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        return digest[:12]

    def _register_uploaded_sources(self, domain: Any, uploaded_contexts: list[UploadedFileContext]) -> list[RegisteredTable]:
        registered: list[RegisteredTable] = []
        for index, item in enumerate(uploaded_contexts, start=1):
            if not item.is_tabular:
                continue
            dataframe = self._parse_dataframe(item)
            normalization = normalize_dataframe(dataframe)
            normalized_dataframe = normalization.dataframe
            semantic_profile = build_semantic_profile(normalized_dataframe)
            table_name = self._safe_table_name(item.table_name or item.file_name, index)
            description = self._build_df_description(
                table_name,
                item.file_name,
                normalized_dataframe,
                semantic_profile,
            )
            domain.add_df(normalized_dataframe, name=table_name, description=description)
            registered.append(
                RegisteredTable(
                    name=table_name,
                    source_file=item.file_name,
                    row_count=int(len(normalized_dataframe)),
                    columns=[str(column) for column in normalized_dataframe.columns],
                    description=description,
                    normalization_report=normalization.to_dict(),
                    semantic_profile=semantic_profile,
                )
            )
        return registered

    def _build_df_description(
        self,
        table_name: str,
        source_file: str,
        dataframe: pd.DataFrame,
        profile: dict[str, Any],
    ) -> str:
        summary = profile.get("profiling_summary", {}) if isinstance(profile, dict) else {}
        column_hints = profile.get("column_type_hints", {}) if isinstance(profile, dict) else {}
        canonical_maps = profile.get("canonical_categorical_value_maps", {}) if isinstance(profile, dict) else {}

        row_count = int(summary.get("row_count", len(dataframe)))
        column_count = int(summary.get("column_count", len(dataframe.columns)))

        lines: list[str] = [
            f"Table: {table_name} (from {source_file})",
            f"Rows: {row_count} | Columns: {column_count}",
        ]

        measures = self._profile_columns_by_type(column_hints, "measure")
        if measures:
            lines.append(f"Numeric measures (suitable for aggregation): {', '.join(measures)}")

        times = self._profile_columns_by_type(column_hints, "time")
        if times:
            lines.append(f"Time columns (suitable for date grouping): {', '.join(times)}")

        keys = self._profile_columns_by_type(column_hints, "key")
        if keys:
            lines.append(f"Identifier columns (do not aggregate): {', '.join(keys)}")

        categorical_columns = self._profile_columns_by_type(column_hints, "categorical")
        boolean_columns = self._profile_columns_by_type(column_hints, "boolean")
        if categorical_columns or boolean_columns:
            lines.append("Categorical columns:")
            for column in categorical_columns + [item for item in boolean_columns if item not in categorical_columns]:
                if column not in dataframe.columns:
                    continue
                values = self._preview_unique_values(dataframe[column], limit=_DESCRIPTION_CATEGORICAL_VALUE_LIMIT)
                value_text = ", ".join(values) if values else "no non-empty values"
                line = f"  - {column}: {value_text}"
                canonical_map = canonical_maps.get(column, {})
                variant_note = self._format_variant_note(canonical_map)
                if variant_note:
                    line += f" ({variant_note})"
                lines.append(line)

        if not measures and not times and not keys and not categorical_columns and not boolean_columns:
            lines.append("No high-confidence semantic column groups were identified.")

        description = "\n".join(lines)
        return self._truncate_description(description)

    def _add_text_descriptions(self, domain: Any, uploaded_contexts: list[UploadedFileContext]) -> None:
        self._register_description_once(
            domain,
            "Full Stack Data Agent local-first workspace. Use uploaded datasets and existing domain context to answer analysis questions.",
        )
        text_contexts = [item for item in uploaded_contexts if not item.is_tabular]
        if text_contexts:
            for item in text_contexts:
                description = (
                    f"Uploaded context file: {item.file_name}\nSummary: {item.summary}\nSnippets:\n"
                    + "\n".join(item.snippets[:3])
                )
                dedupe_key = f"text_upload::{item.extracted_text.strip() or item.summary.strip()}"
                self._register_description_once(domain, description, dedupe_key=dedupe_key)

    def _parse_dataframe(self, item: UploadedFileContext) -> pd.DataFrame:
        payload = item.tabular_payload
        if payload is None or not payload.strip():
            raise ValueError(
                f"Uploaded file {item.file_name} is tabular but does not include a reconstructable payload."
            )
        return pd.read_csv(StringIO(payload))

    def _replay_prior_turns(self, session: _DatabaoSession, prior_turns: list[ConversationTurn]) -> None:
        replay_messages: list[Any] = []
        for turn in prior_turns:
            replay_messages.append(HumanMessage(content=turn.user_message.content))
            if turn.assistant_message is not None:
                replay_messages.append(AIMessage(content=turn.assistant_message.content))

        if not replay_messages:
            return

        cache = session.agent.cache.scoped(f"fsda/{session.conversation_id}")
        cache.put("state", {"messages": replay_messages})

    @staticmethod
    def _auto_visualization_result(thread: Any) -> "VisualisationResult | None":
        auto_vis = getattr(thread, "_visualization_result", None)
        return auto_vis if auto_vis is not None else None

    @staticmethod
    def _plot_backend(plot_result: Any | None) -> str | None:
        if plot_result is None:
            return None
        backend = getattr(plot_result, "backend", None) or getattr(plot_result, "plot_backend", None)
        if backend is not None:
            return str(backend)
        if hasattr(plot_result, "spec") and hasattr(plot_result, "spec_df"):
            return "chart_spec"
        if hasattr(plot_result, "png_bytes") or hasattr(plot_result, "png_base64") or hasattr(plot_result, "image"):
            return "matplotlib"
        return type(plot_result).__name__

    @staticmethod
    def _plot_kind(plot_result: Any | None, plot_spec: dict[str, Any] | None, plot_meta: dict[str, Any] | None) -> str | None:
        if plot_result is None:
            return None
        kind = getattr(plot_result, "kind", None) or getattr(plot_result, "plot_kind", None)
        if kind is not None:
            return str(kind)
        if isinstance(plot_meta, dict) and plot_meta.get("kind") is not None:
            return str(plot_meta["kind"])
        if isinstance(plot_spec, dict):
            mark = plot_spec.get("mark")
            if mark is not None:
                return str(mark)
        return None

    @staticmethod
    def _plot_image_artifact(plot_result: Any | None) -> tuple[str | None, str | None]:
        if plot_result is None:
            return None, None
        png_base64 = getattr(plot_result, "png_base64", None)
        if callable(png_base64):
            try:
                encoded = png_base64()
                if encoded:
                    return str(encoded), "image/png"
            except Exception:
                pass
        png_bytes = getattr(plot_result, "png_bytes", None)
        if callable(png_bytes):
            try:
                raw = png_bytes()
                if raw:
                    return base64.b64encode(raw).decode("utf-8"), "image/png"
            except Exception:
                pass
        image = getattr(plot_result, "image", None)
        if callable(image):
            try:
                image_obj = image()
                if image_obj is not None:
                    buffer = io.BytesIO()
                    image_obj.save(buffer, format="PNG")
                    return base64.b64encode(buffer.getvalue()).decode("utf-8"), "image/png"
            except Exception:
                pass
        return None, None

    def _maybe_collect_plot(self, thread: Any, query: str) -> tuple["VisualisationResult | None", str | None]:
        if not self._has_explicit_chart_intent(query):
            return None, None
        try:
            return thread.plot(query), None
        except Exception as exc:
            return None, str(exc)

    def _enforce_chart_contract(
        self,
        *,
        thread: Any,
        query: str,
        chart_requested: bool,
        dataframe: pd.DataFrame | None,
        plot_result: Any | None,
        plot_error: str | None,
        turn_failure_state: str | None,
    ) -> tuple[Any | None, str | None, dict[str, Any]]:
        contract_debug: dict[str, Any] = {
            "chart_contract": None,
            "chart_contract_validation": None,
            "chart_contract_repair_attempted": False,
            "chart_contract_repair_success": False,
        }
        if not chart_requested:
            return plot_result, plot_error, contract_debug
        if plot_error:
            contract_debug["chart_contract_skipped"] = "upstream_plot_error"
            return plot_result, plot_error, contract_debug
        plot_meta = getattr(plot_result, "meta", None) if plot_result is not None else None
        planner_status = None
        if isinstance(plot_meta, dict):
            planner_status = str((plot_meta.get("chart_debug") or {}).get("planner_status") or "")
            if plot_meta.get("plot_error"):
                contract_debug["chart_contract_skipped"] = "upstream_plot_meta_error"
                return plot_result, plot_error, contract_debug
        if planner_status in {"planning_failed", "schema_parse_failed", "validation_failed", "render_failed"}:
            contract_debug["chart_contract_skipped"] = "upstream_planner_failure"
            return plot_result, plot_error, contract_debug

        columns = [str(column) for column in dataframe.columns] if dataframe is not None else []
        contract = build_chart_request_contract(query, chart_requested=chart_requested)
        contract_debug["chart_contract"] = contract.to_dict()
        if not any(
            [
                contract.required_fields,
                contract.requested_kind,
                contract.requested_orientation,
                contract.requested_hue,
                contract.requested_stack_mode,
                contract.requested_normalize_mode,
            ]
        ):
            contract_debug["chart_contract_skipped"] = "no_explicit_chart_contract"
            return plot_result, plot_error, contract_debug

        plot_plan = getattr(plot_result, "chart_plan", None) if plot_result is not None else None
        plot_spec = getattr(plot_result, "spec", None) if plot_result is not None else None
        if plot_plan is None and plot_result is not None:
            plot_plan = getattr(plot_result, "plot_config", None)
        if plot_plan is None and isinstance(plot_meta, dict):
            plot_plan = plot_meta.get("chart_plan") or plot_meta.get("plot_config")
        source_purpose = "diagnostic" if (turn_failure_state is not None or self._is_diagnostic_dataframe(dataframe)) else "business_result"
        validation = validate_chart_contract(
            contract=contract,
            dataframe_columns=columns,
            chart_plan=plot_plan,
            chart_spec=plot_spec,
            artifact_purpose=source_purpose,
        )
        contract_debug["chart_contract_validation"] = validation.to_dict()
        if validation.status != "mismatch":
            return plot_result, plot_error, contract_debug

        if turn_failure_state is not None:
            reason = "; ".join(validation.mismatch_reasons) or "chart blocked by failed turn state"
            return None, f"chart_contract_mismatch: {reason}", contract_debug

        if not validation.repairable:
            reason = "; ".join(validation.mismatch_reasons) or "chart contract mismatch"
            return None, f"chart_contract_mismatch: {reason}", contract_debug

        repair_prompt = build_controlled_repair_prompt(contract, validation.resolved_fields)
        contract_debug["chart_contract_repair_attempted"] = True
        contract_debug["chart_contract_repair_prompt"] = repair_prompt
        try:
            repaired_plot = thread.plot(repair_prompt)
        except Exception as exc:
            reason = "; ".join(validation.mismatch_reasons) or "chart contract mismatch"
            return None, f"chart_contract_mismatch: {reason}; repair_error: {exc}", contract_debug

        repaired_plan = getattr(repaired_plot, "chart_plan", None) if repaired_plot is not None else None
        repaired_spec = getattr(repaired_plot, "spec", None) if repaired_plot is not None else None
        if repaired_plan is None and repaired_plot is not None:
            repaired_plan = getattr(repaired_plot, "plot_config", None)
        repaired_meta = getattr(repaired_plot, "meta", None) if repaired_plot is not None else None
        if repaired_plan is None and isinstance(repaired_meta, dict):
            repaired_plan = repaired_meta.get("chart_plan") or repaired_meta.get("plot_config")
        repaired_validation = validate_chart_contract(
            contract=contract,
            dataframe_columns=columns,
            chart_plan=repaired_plan,
            chart_spec=repaired_spec,
            artifact_purpose=source_purpose,
        )
        contract_debug["chart_contract_repair_validation"] = repaired_validation.to_dict()
        if repaired_validation.status == "matched":
            contract_debug["chart_contract_repair_success"] = True
            return repaired_plot, None, contract_debug

        reason = "; ".join(repaired_validation.mismatch_reasons) or "chart contract mismatch"
        return None, f"chart_contract_mismatch: {reason}", contract_debug

    def _complete_response(
        self,
        thread: Any,
        query: str,
        text: str,
        dataframe: pd.DataFrame | None,
        *,
        llm_config: "LLMConfig" | None = None,
        allow_follow_up: bool = True,
        failure_reason: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        clauses, extraction_debug = self._extract_explicit_deliverables(query, llm_config=llm_config)
        validation: dict[str, Any] = {
            "explicit_deliverables": clauses,
            "requested_deliverables": clauses,
            "satisfied_deliverables": [],
            "missing_deliverables": [],
            "supplement_added": False,
            "deliverable_extraction_source": extraction_debug.get("source"),
            "coverage_judgement_source": None,
            "follow_up_source": None,
            "status": "skipped",
        }
        if extraction_debug.get("error"):
            validation["deliverable_extraction_error"] = extraction_debug["error"]
        if failure_reason:
            validation["failure_reason"] = failure_reason
            validation["status"] = "failed"
            validation["supplement_added"] = False
            return self._failure_response_text(query, failure_reason), validation

        if not clauses and dataframe is not None and not dataframe.empty:
            clauses, fallback_debug = self._extract_explicit_deliverables(query)
            validation["explicit_deliverables"] = clauses
            validation["requested_deliverables"] = clauses
            validation["deliverable_extraction_source"] = fallback_debug.get("source", "heuristic_local")

        if not clauses or dataframe is None or dataframe.empty:
            if clauses and (dataframe is None or dataframe.empty):
                validation["missing_deliverables"] = list(clauses)
                validation["status"] = "failed"
            return text, validation

        missing: list[str] = []
        satisfied: list[str] = []
        coverage_reasons: list[str] = []
        for clause in clauses:
            covered, judge_debug = self._response_covers_clause(
                text,
                clause,
                query=query,
                llm_config=llm_config,
            )
            validation["coverage_judgement_source"] = judge_debug.get("source")
            if judge_debug.get("reason"):
                coverage_reasons.append(str(judge_debug["reason"]))
            if not covered:
                missing.append(clause)
            else:
                satisfied.append(clause)
        validation["missing_deliverables"] = missing
        validation["satisfied_deliverables"] = satisfied
        if coverage_reasons:
            validation["coverage_judgement_reasons"] = coverage_reasons
        if not missing:
            validation["status"] = "satisfied"
            return text, validation
        if not allow_follow_up:
            validation["status"] = "failed"
            validation["follow_up_error"] = "Follow-up supplement disabled due to turn failure state."
            return text, validation

        follow_up = self._build_follow_up(query, missing, llm_config=llm_config)
        if follow_up is None:
            validation["status"] = "failed"
            return text, validation

        try:
            follow_up_thread = thread.ask(follow_up)
            follow_up_text = follow_up_thread.text().strip()
        except Exception as exc:
            validation["follow_up_error"] = str(exc)
            validation["status"] = "failed"
            return text, validation

        if not follow_up_text:
            validation["follow_up_error"] = "Follow-up response was empty."
            validation["status"] = "failed"
            return text, validation

        combined = text.rstrip()
        if combined:
            combined += "\n\n"
        combined += follow_up_text
        validation["supplement_added"] = True
        validation["follow_up_query"] = follow_up
        validation["follow_up_source"] = "llm" if llm_config is not None else "template"
        validation["status"] = "supplemented"
        return combined, validation

    def _extract_explicit_deliverables(
        self,
        query: str,
        *,
        llm_config: "LLMConfig" | None = None,
    ) -> tuple[list[str], dict[str, Any]]:
        debug: dict[str, Any] = {
            "source": "heuristic_local",
            "language": "zh" if self._looks_like_chinese(query) else "en",
        }

        if llm_config is not None and self._trigger_explicit_deliverable_extraction(query):
            deliverables, llm_debug = self._extract_explicit_deliverables_with_llm(query, llm_config=llm_config)
            debug.update(llm_debug)
            if deliverables:
                return deliverables, debug
            if llm_debug.get("error"):
                debug["fallback_reason"] = llm_debug["error"]

        lowered = query.lower()
        marker_index: int | None = None
        marker_length = 0
        for marker in _DELIVERABLE_MARKERS:
            index = lowered.find(marker.lower())
            if index == -1:
                continue
            if marker_index is None or index < marker_index:
                marker_index = index
                marker_length = len(marker)

        if marker_index is None:
            return [], debug

        tail = query[marker_index + marker_length :]
        tail = tail.lstrip(" \t\r\n-•*")
        tail = re.sub(r"^[^:\n]{0,80}:\s*", "", tail)
        if not tail:
            return [], debug

        def _is_meaningful_deliverable(text: str) -> bool:
            candidate = self._normalize_whitespace(text)
            if not candidate:
                return False
            lowered = candidate.lower()
            instruction_only_markers = {
                "你最终至少要回答",
                "最终至少要回答",
                "请回答以下问题",
                "请回答以下",
                "answer the following",
                "answer the following explicit deliverables",
                "explicit deliverables",
            }
            if lowered in instruction_only_markers:
                return False
            if re.fullmatch(r"[\W_：:，,；;。.!！？、\-•*]+", candidate):
                return False
            if len(candidate) == 1 and not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", candidate):
                return False
            return True

        clauses: list[str] = []
        for raw_chunk in re.split(r"[\n•]+", tail):
            chunk = raw_chunk.strip(" \t\r\n-•*")
            if not chunk:
                continue
            parts = re.split(r"(?<=[?。.!！？])\s*|(?<=\d[.)])\s*|(?<=：)\s*(?=[A-Za-z0-9\u4e00-\u9fff])", chunk)
            for part in parts:
                cleaned = part.strip(" \t\r\n-•*")
                cleaned = re.sub(r"^[：:，,；;\-•*]+", "", cleaned).strip()
                cleaned = re.sub(r"[：:，,；;]+$", "", cleaned).strip()
                if _is_meaningful_deliverable(cleaned):
                    clauses.append(cleaned)

        if not clauses:
            cleaned_tail = tail.strip(" \t\r\n-•*")
            cleaned_tail = re.sub(r"^[：:，,；;\-•*]+", "", cleaned_tail).strip()
            clauses = [cleaned_tail] if _is_meaningful_deliverable(cleaned_tail) else []

        return clauses, debug

    def _response_covers_clause(
        self,
        text: str,
        clause: str,
        *,
        query: str,
        llm_config: "LLMConfig" | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        covered, judge_debug = self._judge_clause_coverage_with_llm(
            query,
            clause,
            text,
            llm_config=llm_config,
        )
        if judge_debug.get("source") == "llm":
            return covered, judge_debug

        clause_terms = self._extract_significant_terms(clause)
        if not clause_terms:
            matched = clause.strip().lower() in text.lower()
            judge_debug.update(
                {
                    "source": "lexical_heuristic",
                    "reason": "Exact substring match" if matched else "No significant terms found",
                }
            )
            return matched, judge_debug

        lower_text = text.lower()
        hits = sum(1 for term in clause_terms if term.lower() in lower_text)
        required_hits = max(1, (len(clause_terms) * 3 + 4) // 5)
        matched = hits >= required_hits
        judge_debug.update(
            {
                "source": "lexical_heuristic",
                "reason": f"Matched {hits}/{len(clause_terms)} significant terms",
            }
        )
        return matched, judge_debug

    def _extract_significant_terms(self, text: str) -> list[str]:
        normalized = re.sub(
            r"(?:是否|大致|整体|大约|请问|请回答|回答|根据|基于|你最终至少要回答)",
            " ",
            text,
        )
        normalized = re.sub(r"[，。!！?？、/\\()\[\]{}\-和与及以及]", " ", normalized)

        terms: list[str] = []
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_+-]*|[\u4e00-\u9fff]{2,}", normalized):
            normalized_token = token.strip().lower()
            if len(normalized_token) < 2:
                continue
            if normalized_token in _QUESTION_STOPWORDS:
                continue
            terms.append(token)

        deduped: list[str] = []
        seen: set[str] = set()
        for term in terms:
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(term)
        return deduped

    def _build_follow_up(
        self,
        query: str,
        missing_clauses: list[str],
        *,
        llm_config: "LLMConfig" | None = None,
    ) -> str | None:
        if not missing_clauses:
            return None
        if llm_config is not None:
            try:
                payload = self._llm_json(llm_config, self._build_follow_up_messages(query, missing_clauses))
                follow_up = payload.get("follow_up")
                if isinstance(follow_up, str) and follow_up.strip():
                    return self._normalize_whitespace(follow_up)
            except Exception:
                pass
        return self._template_follow_up(query, missing_clauses)

    def _snapshot(self, session: _DatabaoSession) -> DatabaoSessionSnapshot:
        return DatabaoSessionSnapshot(
            conversation_id=session.conversation_id,
            llm_name=session.agent.llm_config.name,
            executor_type=self._executor_type,
            registered_tables=session.registered_tables,
            normalization_reports=[table.normalization_report for table in session.registered_tables],
            context_build_error=session.context_build_error,
            context_replayed=getattr(session, "context_replayed", False),
            datasource_changed=getattr(session, "datasource_changed", False),
            thread_reset_reason=getattr(session, "thread_reset_reason", None),
        )

    def _register_description_once(self, domain: Any, description: str, *, dedupe_key: str | None = None) -> None:
        domain_key = str(self._settings.domain_dir)
        seen = self._description_registry.setdefault(domain_key, set())
        marker = dedupe_key or description
        if marker in seen:
            return
        domain.add_description(description)
        seen.add(marker)

    def _ensure_runtime_ready(self) -> None:
        dependency_status = check_runtime_dependencies()
        if dependency_status.is_ok:
            return
        raise ProviderUnavailableError(dependency_status.error or "Missing runtime dependencies.")

    def _has_explicit_chart_intent(self, query: str) -> bool:
        return self._explicit_chart_request_marker(query) is not None

    @staticmethod
    def _profile_columns_by_type(column_hints: dict[str, Any], semantic_type: str) -> list[str]:
        columns = [
            column
            for column, hint in column_hints.items()
            if isinstance(hint, dict) and hint.get("semantic_type") == semantic_type
        ]
        return [str(column) for column in columns]

    @staticmethod
    def _preview_unique_values(series: pd.Series, *, limit: int) -> list[str]:
        values: list[str] = []
        seen: set[str] = set()
        for value in series.dropna().astype("string").tolist():
            cleaned = " ".join(str(value).split())
            if not cleaned:
                continue
            key = cleaned.casefold()
            if key in seen:
                continue
            seen.add(key)
            values.append(cleaned)
            if len(values) >= limit:
                break
        return values

    @staticmethod
    def _format_variant_note(canonical_map: dict[str, str]) -> str:
        if not canonical_map:
            return ""
        sample_pairs = [f"{source} -> {target}" for source, target in list(canonical_map.items())[:3]]
        if not sample_pairs:
            return "canonicalized variants detected"
        return "canonicalized variants: " + "; ".join(sample_pairs)

    @staticmethod
    def _truncate_description(text: str) -> str:
        compact = text.strip()
        if len(compact) <= _DESCRIPTION_MAX_LENGTH:
            return compact
        return compact[: _DESCRIPTION_MAX_LENGTH - 16].rstrip() + " [truncated]"

    @staticmethod
    def _signature(uploaded_contexts: list[UploadedFileContext]) -> tuple[tuple[str, int, str | None], ...]:
        return tuple((item.file_name, item.size_bytes, item.mime_type) for item in uploaded_contexts)

    @staticmethod
    def _safe_table_name(seed: str, index: int) -> str:
        stem = re.sub(r"[^A-Za-z0-9_]+", "_", seed.split(".")[0]).strip("_").lower() or f"uploaded_data_{index}"
        if not stem[0].isalpha():
            stem = f"uploaded_{stem}"
        return stem




