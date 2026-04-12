from __future__ import annotations

import re
from dataclasses import dataclass, field
from io import StringIO
from typing import TYPE_CHECKING, Any

import pandas as pd
import requests

from full_stack_data_agent.app.runtime_models import DatabaoSessionSnapshot, DatabaoTurnResult, RegisteredTable
from full_stack_data_agent.config.settings import Settings
from full_stack_data_agent.context.models import UploadedFileContext
from full_stack_data_agent.llm.models import ProviderHealth

if TYPE_CHECKING:
    from databao.agent.configs.llm import LLMConfig
    from databao.agent.core.visualizer import VisualisationResult


PLOT_KEYWORDS = ("plot", "chart", "graph", "distribution", "histogram", "scatter", "bar", "line", "visual")


@dataclass
class _DatabaoSession:
    conversation_id: str
    uploaded_signature: tuple[tuple[str, int, str | None], ...]
    domain: Any
    agent: Any
    thread: Any
    registered_tables: list[RegisteredTable] = field(default_factory=list)
    context_build_error: str | None = None


class DatabaoRuntime:
    def __init__(self, settings: Settings, *, executor_type: str = "lighthouse"):
        self._settings = settings
        self._executor_type = executor_type
        self._sessions: dict[str, _DatabaoSession] = {}

    def provider_status(self) -> ProviderHealth:
        try:
            response = requests.get(self._settings.ollama_tags_url, timeout=min(self._settings.ollama_timeout, 10))
            response.raise_for_status()
            models = response.json().get("models", [])
            available_models = [str(item.get("name")) for item in models if item.get("name")]
            connected = self._settings.ollama_model in available_models
            error = None if connected else f"Model {self._settings.ollama_model} is not available in local Ollama."
            return ProviderHealth(
                provider=self._settings.provider_name,
                base_url=self._settings.ollama_base_url,
                model=self._settings.ollama_model,
                connected=connected,
                available_models=available_models,
                error=error,
            )
        except requests.RequestException as exc:
            return ProviderHealth(
                provider=self._settings.provider_name,
                base_url=self._settings.ollama_base_url,
                model=self._settings.ollama_model,
                connected=False,
                available_models=[],
                error=str(exc),
            )

    def ask(
        self,
        conversation_id: str,
        query: str,
        *,
        uploaded_contexts: list[UploadedFileContext] | None = None,
        history_queries: list[str] | None = None,
    ) -> tuple[DatabaoTurnResult, DatabaoSessionSnapshot]:
        session = self._ensure_session(
            conversation_id,
            uploaded_contexts=uploaded_contexts or [],
            history_queries=history_queries or [],
        )
        thread = session.thread.ask(query)
        dataframe = thread.df(rows_limit=200)
        plot_result = self._maybe_collect_plot(thread, query)
        thread_meta = thread.meta()

        preview = dataframe.head(10).to_dict(orient="records") if dataframe is not None else None
        columns = [str(column) for column in dataframe.columns] if dataframe is not None else None
        row_count = int(len(dataframe)) if dataframe is not None else None
        result = DatabaoTurnResult(
            text=thread.text(),
            dataframe=dataframe,
            dataframe_preview=preview,
            columns=columns,
            row_count=row_count,
            plot_code=plot_result.code if plot_result is not None else thread_meta.get("plot_code"),
            plot_meta=plot_result.meta if plot_result is not None else None,
            thread_meta=thread_meta,
            used_databao=True,
        )
        return result, self._snapshot(session)

    def _ensure_session(
        self,
        conversation_id: str,
        *,
        uploaded_contexts: list[UploadedFileContext],
        history_queries: list[str],
    ) -> _DatabaoSession:
        signature = self._signature(uploaded_contexts)
        existing = self._sessions.get(conversation_id)
        if existing and existing.uploaded_signature == signature:
            return existing

        session = self._build_session(conversation_id, uploaded_contexts=uploaded_contexts, history_queries=history_queries)
        self._sessions[conversation_id] = session
        return session

    def _build_session(
        self,
        conversation_id: str,
        *,
        uploaded_contexts: list[UploadedFileContext],
        history_queries: list[str],
    ) -> _DatabaoSession:
        from databao.agent import api as bao_api

        domain = bao_api.domain(self._settings.domain_dir)
        llm_config = self._build_llm_config()
        registered_tables = self._register_uploaded_sources(domain, uploaded_contexts)
        self._add_text_descriptions(domain, uploaded_contexts)

        context_build_error: str | None = None
        if domain.supports_context and not domain.is_context_built():
            try:
                domain.build_context()
            except Exception as exc:
                context_build_error = str(exc)

        agent = bao_api.agent(
            domain,
            name="fsda",
            llm_config=llm_config,
            executor_type=self._executor_type,
            stream_ask=False,
            stream_plot=False,
            auto_output_modality=True,
        )
        thread = agent.thread(cache_scope=f"fsda/{conversation_id}")
        for previous_query in history_queries:
            if previous_query.strip():
                thread.ask(previous_query.strip())

        return _DatabaoSession(
            conversation_id=conversation_id,
            uploaded_signature=self._signature(uploaded_contexts),
            domain=domain,
            agent=agent,
            thread=thread,
            registered_tables=registered_tables,
            context_build_error=context_build_error,
        )

    def _build_llm_config(self) -> "LLMConfig":
        from databao.agent.configs.llm import LLMConfig

        use_openai_compatible = bool(self._settings.ollama_base_url)
        model_name = self._settings.ollama_model
        if not use_openai_compatible and not model_name.startswith(("ollama:", "openai:", "anthropic:")):
            model_name = f"ollama:{model_name}"
        return LLMConfig(
            name=model_name,
            temperature=self._settings.ollama_temperature,
            timeout=int(self._settings.ollama_timeout),
            api_base_url=self._settings.ollama_base_url if use_openai_compatible else None,
            use_responses_api=False,
            model_kwargs={
                "num_ctx": self._settings.ollama_num_ctx,
                "validate_model_on_init": True,
            },
        )

    def _register_uploaded_sources(self, domain: Any, uploaded_contexts: list[UploadedFileContext]) -> list[RegisteredTable]:
        registered: list[RegisteredTable] = []
        for index, item in enumerate(uploaded_contexts, start=1):
            if not item.is_tabular:
                continue
            dataframe = self._parse_dataframe(item)
            table_name = self._safe_table_name(item.table_name or item.file_name, index)
            description = f"Uploaded file {item.file_name} registered for this session."
            domain.add_df(dataframe, name=table_name, description=description)
            registered.append(
                RegisteredTable(
                    name=table_name,
                    source_file=item.file_name,
                    row_count=int(len(dataframe)),
                    columns=[str(column) for column in dataframe.columns],
                    description=description,
                )
            )
        return registered

    def _add_text_descriptions(self, domain: Any, uploaded_contexts: list[UploadedFileContext]) -> None:
        domain.add_description(
            "Full Stack Data Agent local-first workspace. Use uploaded datasets and existing domain context to answer analysis questions."
        )
        text_contexts = [item for item in uploaded_contexts if not item.is_tabular]
        if text_contexts:
            description = "\n\n".join(
                f"Uploaded context file: {item.file_name}\nSummary: {item.summary}\nSnippets:\n" + "\n".join(item.snippets[:3])
                for item in text_contexts
            )
            domain.add_description(description)

    def _parse_dataframe(self, item: UploadedFileContext) -> pd.DataFrame:
        if not item.extracted_text.strip():
            raise ValueError(f"Uploaded file {item.file_name} does not contain readable CSV content.")
        return pd.read_csv(StringIO(item.extracted_text))

    def _maybe_collect_plot(self, thread: Any, query: str) -> "VisualisationResult | None":
        lowered = query.lower()
        should_plot = any(keyword in lowered for keyword in PLOT_KEYWORDS)
        if not should_plot:
            return None
        try:
            return thread.plot(query)
        except Exception:
            return None

    def _snapshot(self, session: _DatabaoSession) -> DatabaoSessionSnapshot:
        return DatabaoSessionSnapshot(
            conversation_id=session.conversation_id,
            llm_name=session.agent.llm_config.name,
            executor_type=self._executor_type,
            registered_tables=session.registered_tables,
            context_build_error=session.context_build_error,
        )

    @staticmethod
    def _signature(uploaded_contexts: list[UploadedFileContext]) -> tuple[tuple[str, int, str | None], ...]:
        return tuple((item.file_name, item.size_bytes, item.mime_type) for item in uploaded_contexts)

    @staticmethod
    def _safe_table_name(seed: str, index: int) -> str:
        stem = re.sub(r"[^A-Za-z0-9_]+", "_", seed.split(".")[0]).strip("_").lower() or f"uploaded_data_{index}"
        if not stem[0].isalpha():
            stem = f"uploaded_{stem}"
        return stem
