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

import pandas as pd
from langchain_core.messages import AIMessage, HumanMessage

from full_stack_data_agent.app.dependencies import check_runtime_dependencies
from full_stack_data_agent.app.normalization import normalize_dataframe
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
from full_stack_data_agent.utils.text_classification import CHART_INTENT_MARKERS

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
        self._description_registry: dict[str, set[str]] = {}
        self._fallback_domain_warning: str | None = None
        self._provider_health_cache: dict[str, tuple[float, ProviderHealth]] = {}

    @staticmethod
    def _looks_like_chinese(text: str) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", text))

    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

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

        parsed = self._extract_first_json_object(raw_text)
        if parsed is None:
            raise ValueError("LLM response did not contain valid JSON")
        return parsed

    @staticmethod
    def _extract_first_json_object(text: str) -> dict[str, Any] | None:
        start = text.find("{")
        while start != -1:
            depth = 0
            in_string = False
            escape = False
            for index in range(start, len(text)):
                char = text[index]
                if in_string:
                    if escape:
                        escape = False
                    elif char == "\\":
                        escape = True
                    elif char == '"':
                        in_string = False
                    continue
                if char == '"':
                    in_string = True
                    continue
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            parsed = json.loads(text[start : index + 1])
                        except json.JSONDecodeError:
                            break
                        if isinstance(parsed, dict):
                            return parsed
                        break
            start = text.find("{", start + 1)
        return None

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
        debug = {"source": "legacy_fallback", "language": None}
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
        debug = {"source": "lexical_fallback", "reason": None}
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
            return f"请只基于同一个 dataframe 补充未回答的部分：{bullets}。不要重述整张表。"
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
            result = self._run_turn(session, query, stream_writer=stream_writer)
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

    def _ensure_session(
        self,
        conversation_id: str,
        *,
        uploaded_contexts: list[UploadedFileContext],
        prior_turns: list[ConversationTurn] | None = None,
        provider_name: str | None = None,
    ) -> _DatabaoSession:
        signature = self._signature(uploaded_contexts)
        existing = self._sessions.get(conversation_id)
        target_provider = normalize_provider_name(provider_name or self._settings.llm_provider)
        if existing and existing.uploaded_signature == signature and existing.provider_name == target_provider:
            existing.context_replayed = False
            existing.datasource_changed = False
            existing.thread_reset_reason = None
            return existing

        replay_turns = prior_turns or []
        should_replay = (
            existing is not None
            and existing.provider_name == target_provider
            and existing.uploaded_signature != signature
            and bool(replay_turns)
        )
        session = self._build_session(
            conversation_id,
            uploaded_contexts=uploaded_contexts,
            datasource_changed=existing is not None,
            context_replayed=should_replay,
            thread_reset_reason=(
                "datasource_rebuilt_with_history_replay"
                if existing is not None and existing.provider_name == target_provider and existing.uploaded_signature != signature
                else ("provider_changed" if existing is not None else None)
            ),
            provider_name=provider_name,
        )
        if should_replay:
            self._replay_prior_turns(session, replay_turns)
        if provider_name is None:
            self._sessions[conversation_id] = session
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

    def _run_turn(self, session: _DatabaoSession, query: str, *, stream_writer: Any | None = None) -> DatabaoTurnResult:
        chart_requested = self._has_explicit_chart_intent(query)
        chart_intent = self._extract_chart_intent(query)
        with self._streaming_thread_writer(session, stream_writer):
            thread = session.thread.ask(query)
            dataframe = thread.df(rows_limit=200)
            plot_result, plot_error = self._maybe_collect_plot(thread, query)
            if plot_result is None and plot_error is None:
                auto_plot_result = self._auto_visualization_result(thread)
                if auto_plot_result is not None:
                    plot_result = auto_plot_result
                    chart_requested = True
            thread_meta = thread.meta()
            text, completion_validation = self._complete_response(
                thread,
                query,
                thread.text(),
                dataframe,
                llm_config=session.agent.llm_config,
            )

        preview = dataframe.head(10).to_dict(orient="records") if dataframe is not None else None
        columns = [str(column) for column in dataframe.columns] if dataframe is not None else None
        row_count = int(len(dataframe)) if dataframe is not None else None

        plot_spec = getattr(plot_result, "spec", None) if plot_result is not None else None
        plot_data_frame = getattr(plot_result, "spec_df", None) if plot_result is not None else None
        plot_data = plot_data_frame.to_dict(orient="records") if plot_data_frame is not None else None
        plot_meta = getattr(plot_result, "meta", None) if plot_result is not None else None
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
                or (plot_spec is not None and plot_data_frame is not None)
                or plot_image_base64 is not None
            )
        )
        planner_status = visualizer_chart_debug.get("planner_status")
        upstream_chart_failed = planner_status in {"planning_failed", "schema_parse_failed", "validation_failed", "render_failed"}
        chart_generated = plot_result is not None and effective_plot_error is None and not upstream_chart_failed

        session_provider = getattr(session, "provider_name", None) or self._settings.llm_provider
        resolved = self._resolve_provider_config(session_provider)
        chart_renderer = plot_backend
        chart_type = plot_kind
        if plot_result is not None:
            plot_object = getattr(plot_result, "plot", None)
            if plot_object is not None:
                chart_renderer = type(plot_object).__name__
            elif plot_spec is not None:
                chart_renderer = chart_renderer or "vega_lite_spec"
                chart_type = chart_type or str((plot_spec or {}).get("mark") or "vega-lite")

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
            "chart_generation_called": chart_requested,
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
        }
        if visualizer_chart_debug:
            chart_debug.update(visualizer_chart_debug)
            chart_debug["chart_generated"] = chart_generated
            chart_debug["chart_failure_stage"] = chart_failure_stage
            chart_debug["chart_failure_reason"] = chart_failure_reason
            chart_debug["plot_error"] = effective_plot_error

        thread_meta = {
            **thread_meta,
            "provider_used": resolved.provider,
            "model_used": resolved.model,
            "chart_debug": chart_debug,
        }
        result = DatabaoTurnResult(
            text=text,
            dataframe=dataframe,
            dataframe_preview=preview,
            columns=columns,
            row_count=row_count,
            provider_used=resolved.provider,
            model_used=resolved.model,
            plot_code=getattr(plot_result, "code", None) if plot_result is not None else thread_meta.get("plot_code"),
            plot_object=plot_result,
            plot_spec=plot_spec,
            plot_data=plot_data,
            plot_meta=plot_meta,
            plot_backend=plot_backend,
            plot_kind=plot_kind,
            plot_image_base64=plot_image_base64,
            plot_image_mime_type=plot_image_mime_type,
            plot_error=effective_plot_error,
            chart_debug=chart_debug,
            completion_validation=completion_validation,
            thread_meta=thread_meta,
            used_databao=True,
        )
        return result

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
            result = self._run_turn(fallback_session, query, stream_writer=stream_writer)
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
            "deepseek",
            "ollama",
        )
        return any(marker in message for marker in fallback_markers)

    @staticmethod
    def _provider_error_reason(exc: Exception) -> str:
        return f"{exc.__class__.__name__}: {exc}"

    @staticmethod
    def _explicit_chart_request_marker(query: str) -> str | None:
        lowered = query.lower()
        for marker in CHART_INTENT_MARKERS:
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
            return "vega"
        if hasattr(plot_result, "png_bytes") or hasattr(plot_result, "png_base64") or hasattr(plot_result, "image"):
            return "seaborn"
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

    def _complete_response(
        self,
        thread: Any,
        query: str,
        text: str,
        dataframe: pd.DataFrame | None,
        *,
        llm_config: "LLMConfig" | None = None,
    ) -> tuple[str, dict[str, Any]]:
        clauses, extraction_debug = self._extract_explicit_deliverables(query, llm_config=llm_config)
        validation: dict[str, Any] = {
            "explicit_deliverables": clauses,
            "missing_deliverables": [],
            "supplement_added": False,
            "deliverable_extraction_source": extraction_debug.get("source"),
            "coverage_judgement_source": None,
            "follow_up_source": None,
        }
        if extraction_debug.get("error"):
            validation["deliverable_extraction_error"] = extraction_debug["error"]

        if not clauses and dataframe is not None and not dataframe.empty:
            clauses, fallback_debug = self._extract_explicit_deliverables(query)
            validation["explicit_deliverables"] = clauses
            validation["deliverable_extraction_source"] = fallback_debug.get("source", "legacy_fallback")

        if not clauses or dataframe is None or dataframe.empty:
            return text, validation

        missing: list[str] = []
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
        validation["missing_deliverables"] = missing
        if coverage_reasons:
            validation["coverage_judgement_reasons"] = coverage_reasons
        if not missing:
            return text, validation

        follow_up = self._build_follow_up(query, missing, llm_config=llm_config)
        if follow_up is None:
            return text, validation

        try:
            follow_up_thread = thread.ask(follow_up)
            follow_up_text = follow_up_thread.text().strip()
        except Exception as exc:
            validation["follow_up_error"] = str(exc)
            return text, validation

        if not follow_up_text:
            validation["follow_up_error"] = "Follow-up response was empty."
            return text, validation

        combined = text.rstrip()
        if combined:
            combined += "\n\n"
        combined += follow_up_text
        validation["supplement_added"] = True
        validation["follow_up_query"] = follow_up
        validation["follow_up_source"] = "llm" if llm_config is not None else "template"
        return combined, validation

    def _extract_explicit_deliverables(
        self,
        query: str,
        *,
        llm_config: "LLMConfig" | None = None,
    ) -> tuple[list[str], dict[str, Any]]:
        debug: dict[str, Any] = {
            "source": "legacy_fallback",
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
                    "source": "lexical_fallback",
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
                "source": "lexical_fallback",
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




