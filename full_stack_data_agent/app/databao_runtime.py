from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from io import StringIO
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
    "你最终至少要回答",
    "最终至少要回答",
    "请回答以下问题",
    "请回答下列问题",
    "你需要回答",
    "最后说明",
    "最后回答",
    "you must answer",
    "please answer the following",
    "please answer the questions",
    "please answer",
    "answer the following",
    "final answer must include",
)

_CHART_INTENT_MARKERS = (
    "plot",
    "chart",
    "graph",
    "distribution",
    "histogram",
    "scatter",
    "bar chart",
    "line chart",
    "heatmap",
    "visualize",
    "visualisation",
    "visualization",
    "画图",
    "绘图",
    "图表",
    "分布图",
    "直方图",
    "散点图",
    "热力图",
    "柱状图",
    "折线图",
    "箱线图",
)

_QUESTION_STOPWORDS = {
    "and",
    "are",
    "based",
    "be",
    "does",
    "do",
    "explain",
    "following",
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
    "answer",
    "analysis",
    "based",
    "data",
    "directly",
    "final",
    "question",
    "summary",
    "the",
    "最終",
    "最终",
    "至少",
    "回答",
    "以下",
    "请",
    "请你",
    "请问",
    "需要",
    "说明",
    "分析",
    "结果",
    "结论",
    "直接",
    "基于",
    "数据",
    "告诉",
    "一下",
    "什么",
    "哪个",
    "哪一个",
    "哪种",
    "是否",
    "如何",
    "大致",
    "所有",
    "问题",
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


class DatabaoRuntime:
    def __init__(self, settings: Settings, *, executor_type: str = "lighthouse"):
        self._settings = settings
        self._executor_type = executor_type
        self._sessions: dict[str, _DatabaoSession] = {}
        self._description_registry: dict[str, set[str]] = {}
        self._fallback_domain_warning: str | None = None
        self._provider_health_cache: dict[str, tuple[float, ProviderHealth]] = {}

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
            result = self._run_turn(session, query)
            return result, self._snapshot(session)
        except Exception as exc:
            fallback_result = self._maybe_retry_with_fallback(
                conversation_id,
                query,
                uploaded,
                history_turns,
                session,
                exc,
            )
            if fallback_result is not None:
                return fallback_result
            raise

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

        return bao_api.agent(
            domain,
            name="fsda",
            llm_config=llm_config,
            executor_type=self._executor_type,
            stream_ask=False,
            stream_plot=False,
            auto_output_modality=True,
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

    def _run_turn(self, session: _DatabaoSession, query: str) -> DatabaoTurnResult:
        chart_requested = self._has_explicit_chart_intent(query)
        chart_intent = self._extract_chart_intent(query)
        thread = session.thread.ask(query)
        dataframe = thread.df(rows_limit=200)
        plot_result, plot_error = self._maybe_collect_plot(thread, query)
        if plot_result is None and plot_error is None:
            auto_plot_result = self._auto_visualization_result(thread)
            if auto_plot_result is not None:
                plot_result = auto_plot_result
                chart_requested = True
        thread_meta = thread.meta()
        text, completion_validation = self._complete_response(thread, query, thread.text(), dataframe)

        preview = dataframe.head(10).to_dict(orient="records") if dataframe is not None else None
        columns = [str(column) for column in dataframe.columns] if dataframe is not None else None
        row_count = int(len(dataframe)) if dataframe is not None else None

        plot_spec = getattr(plot_result, "spec", None) if plot_result is not None else None
        plot_data_frame = getattr(plot_result, "spec_df", None) if plot_result is not None else None
        plot_data = plot_data_frame.to_dict(orient="records") if plot_data_frame is not None else None
        plot_data_rows = len(plot_data) if plot_data is not None else 0
        chart_renderable = bool(
            plot_result is not None
            and (getattr(plot_result, "plot", None) is not None or (plot_spec is not None and plot_data_frame is not None))
        )
        chart_generated = plot_result is not None and plot_error is None

        session_provider = getattr(session, "provider_name", None) or self._settings.llm_provider
        resolved = self._resolve_provider_config(session_provider)
        chart_renderer = None
        chart_type = None
        if plot_result is not None:
            plot_object = getattr(plot_result, "plot", None)
            if plot_object is not None:
                chart_renderer = type(plot_object).__name__
                chart_type = type(plot_object).__name__
            elif plot_spec is not None:
                chart_renderer = "vega_lite_spec"
                chart_type = str((plot_spec or {}).get("mark") or "vega-lite")

        chart_artifact_id = self._build_chart_artifact_id(
            query=query,
            provider=resolved.provider,
            model=resolved.model,
            plot_code=getattr(plot_result, "code", None) if plot_result is not None else None,
            plot_spec=plot_spec,
            plot_data=plot_data,
            plot_error=plot_error,
        )
        chart_failure_stage = None
        chart_failure_reason = None
        if chart_requested:
            if plot_result is None and plot_error is None:
                chart_failure_stage = "generation_failed"
                chart_failure_reason = "chart request was detected but no chart artifact was produced"
            elif plot_error:
                chart_failure_stage = "generation_failed"
                chart_failure_reason = plot_error
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
            "chart_artifact_id": chart_artifact_id,
            "plot_spec_present": plot_spec is not None,
            "plot_data_rows": plot_data_rows,
            "chart_saved_to_history": True,
            "chart_render_called": False,
            "chart_container_width": "container",
            "chart_container_height": 360,
            "chart_failure_stage": chart_failure_stage,
            "chart_failure_reason": chart_failure_reason,
            "plot_error": plot_error,
        }

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
            plot_meta=getattr(plot_result, "meta", None) if plot_result is not None else None,
            plot_error=plot_error,
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
            result = self._run_turn(fallback_session, query)
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
    def _extract_chart_intent(query: str) -> str | None:
        lowered = query.lower()
        for marker in CHART_INTENT_MARKERS:
            if marker in lowered:
                return marker
        return None

    @staticmethod
    def _build_chart_artifact_id(
        *,
        query: str,
        provider: str,
        model: str,
        plot_code: str | None,
        plot_spec: dict[str, Any] | None,
        plot_data: list[dict[str, Any]] | None,
        plot_error: str | None,
    ) -> str:
        payload = {
            "query": query,
            "provider": provider,
            "model": model,
            "plot_code": plot_code,
            "plot_spec": plot_spec,
            "plot_data_rows": len(plot_data or []),
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
        if not item.extracted_text.strip():
            raise ValueError(f"Uploaded file {item.file_name} does not contain readable CSV content.")
        return pd.read_csv(StringIO(item.extracted_text))

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
    ) -> tuple[str, dict[str, Any]]:
        clauses = self._extract_explicit_deliverables(query)
        validation: dict[str, Any] = {
            "explicit_deliverables": clauses,
            "missing_deliverables": [],
            "supplement_added": False,
        }
        if not clauses or dataframe is None or dataframe.empty:
            return text, validation

        missing = [clause for clause in clauses if not self._response_covers_clause(text, clause)]
        validation["missing_deliverables"] = missing
        if not missing:
            return text, validation

        follow_up = self._build_follow_up(missing)
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
        return combined, validation

    def _extract_explicit_deliverables(self, query: str) -> list[str]:
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
            return []

        tail = query[marker_index + marker_length :]
        tail = tail.lstrip(" \t\r\n：:，,。.!?？-•*")
        tail = re.sub(r"^[^:\n]{0,80}:\s*", "", tail)
        if not tail:
            return []

        clauses: list[str] = []
        for raw_chunk in re.split(r"[\n；;]+", tail):
            chunk = raw_chunk.strip(" \t\r\n：:，,。.!?？-•*")
            if not chunk:
                continue
            parts = re.split(r"(?<=[?？])\s*", chunk)
            for part in parts:
                cleaned = part.strip(" \t\r\n：:，,。.!?？-•*")
                if cleaned:
                    clauses.append(cleaned)

        if clauses:
            return clauses

        cleaned_tail = tail.strip(" \t\r\n：:，,。.!?？-•*")
        return [cleaned_tail] if cleaned_tail else []

    def _response_covers_clause(self, text: str, clause: str) -> bool:
        clause_terms = self._extract_significant_terms(clause)
        if not clause_terms:
            return clause.strip().lower() in text.lower()

        lower_text = text.lower()
        hits = 0
        for term in clause_terms:
            if term.lower() in lower_text:
                hits += 1

        required_hits = max(1, (len(clause_terms) * 3 + 4) // 5)
        return hits >= required_hits

    def _extract_significant_terms(self, text: str) -> list[str]:
        normalized = re.sub(
            r"(?:\u662f\u5426|\u5927\u81f4|\u6574\u4f53|\u5927\u7ea6|\u8bf7\u95ee|\u8bf7\u56de\u7b54|\u56de\u7b54|\u6839\u636e|\u57fa\u4e8e|\u4f60\u6700\u7ec8\u81f3\u5c11\u8981\u56de\u7b54)",
            " ",
            text,
        )
        normalized = re.sub(r"[,\uFF0C\u3002!\uff01\?？:：;；、/\\\(\)\[\]\{\}\-\u548c\u4e0e\u53ca\u4ee5\u53ca]", " ", normalized)

        terms: list[str] = []
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_+-]*|[\u4e00-\u9fff]{2,}", normalized):
            normalized = token.strip().lower()
            if len(normalized) < 2:
                continue
            if normalized in _QUESTION_STOPWORDS:
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

    def _build_follow_up(self, missing_clauses: list[str]) -> str | None:
        if not missing_clauses:
            return None
        bullets = "\n".join(f"- {clause}" for clause in missing_clauses)
        return (
            "Please answer the missing sub-questions from the same dataframe only. "
            "Do not restate the full table; give direct grounded answers.\n"
            f"{bullets}"
        )

    def _snapshot(self, session: _DatabaoSession) -> DatabaoSessionSnapshot:
        return DatabaoSessionSnapshot(
            conversation_id=session.conversation_id,
            llm_name=session.agent.llm_config.name,
            executor_type=self._executor_type,
            registered_tables=session.registered_tables,
            normalization_reports=[table.normalization_report for table in session.registered_tables],
            context_build_error=session.context_build_error,
            context_replayed=session.context_replayed,
            datasource_changed=session.datasource_changed,
            thread_reset_reason=session.thread_reset_reason,
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
        lowered = query.lower()
        return any(marker in lowered for marker in CHART_INTENT_MARKERS)

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
