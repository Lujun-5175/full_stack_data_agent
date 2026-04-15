from __future__ import annotations

"""Legacy prompt-stuffing context path kept for fallback/reference.

The default analysis path is now Databao domain + DatabaoRuntime.
"""

from pathlib import Path

from databao_context_engine import init_or_get_dce_domain
from databao_context_engine.project.layout import get_source_dir
from databao_context_engine.search_context.search_service import ContextSearchMode

from full_stack_data_agent.config.provider_resolution import provider_resolution_summary
from full_stack_data_agent.config.settings import Settings
from full_stack_data_agent.context.conversation_engine import ConversationEngine
from full_stack_data_agent.context.models import (
    ContextDebugInfo,
    ContextPacket,
    ConversationState,
    RetrievedContextItem,
    UploadedFileContext,
)


class ContextPacketBuilder:
    def __init__(self, settings: Settings, conversation_engine: ConversationEngine):
        self._settings = settings
        self._conversation_engine = conversation_engine
        self._domain_manager = self._ensure_domain()
        self._engine = self._domain_manager.get_engine_for_domain()

    @property
    def domain_dir(self) -> Path:
        return self._settings.domain_dir

    def build_packet(self, state: ConversationState, latest_user_message: str) -> ContextPacket:
        retrieved_contexts, retrieval_mode, retrieval_error = self._retrieve_context(latest_user_message)
        uploaded_contexts = self._load_uploaded_contexts(state)
        recent_messages = self._conversation_engine.recent_messages(state)
        recent_user_message = next((message.content for message in reversed(recent_messages) if message.role == "user"), None)
        recent_assistant_message = next(
            (message.content for message in reversed(recent_messages) if message.role == "assistant"),
            None,
        )
        return ContextPacket(
            conversation_id=state.conversation_id,
            turn_count=state.turn_count,
            recent_messages=recent_messages,
            context_summary=self._build_context_summary(state, uploaded_contexts),
            retrieved_contexts=retrieved_contexts,
            debug=ContextDebugInfo(
                recent_user_message=recent_user_message,
                recent_assistant_message=recent_assistant_message,
                retrieval_mode=retrieval_mode,
                retrieval_error=retrieval_error,
            ),
            uploaded_contexts=uploaded_contexts,
            artifacts={"domain_dir": str(self._settings.domain_dir)},
        )

    def _ensure_domain(self):
        self._settings.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._settings.domain_dir.mkdir(parents=True, exist_ok=True)
        domain_manager = init_or_get_dce_domain(self._settings.domain_dir)
        files_dir = get_source_dir(self._settings.domain_dir) / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        provider_summary = provider_resolution_summary(self._settings)

        overview_path = files_dir / "full_stack_data_agent_overview.md"
        if not overview_path.exists():
            overview_path.write_text(
                "\n".join(
                    [
                        "# Full Stack Data Agent",
                        "",
                        "This project combines Databao Agent and Databao Context Engine in one local app.",
                        f"It uses provider {provider_summary['provider']} and model {provider_summary['model']}.",
                        "When answering, prefer conversation memory first, then project domain context.",
                        "The UI should surface provider health, context packet details, and agent debug traces.",
                    ]
                ),
                encoding="utf-8",
            )

        notes_path = files_dir / "workspace_notes.md"
        if not notes_path.exists():
            notes_path.write_text(
                "\n".join(
                    [
                        "# Workspace Notes",
                        "",
                        f"Active provider base URL defaults to {provider_summary['base_url']}.",
                        f"Active model defaults to {provider_summary['model']}.",
                        "The system should support multi-turn memory such as remembering a provided user name.",
                    ]
                ),
                encoding="utf-8",
            )

        domain_manager.build_context(should_index=False, should_enrich_context=False)
        return domain_manager

    def _load_uploaded_contexts(self, state: ConversationState) -> list[UploadedFileContext]:
        raw_contexts = state.debug_state.get("uploaded_contexts", [])
        loaded: list[UploadedFileContext] = []
        for item in raw_contexts:
            if isinstance(item, UploadedFileContext):
                loaded.append(item)
            elif isinstance(item, dict):
                try:
                    loaded.append(UploadedFileContext(**item))
                except TypeError:
                    continue
        return loaded

    def _build_context_summary(self, state: ConversationState, uploaded_contexts: list[UploadedFileContext]) -> str:
        base_summary = self._conversation_engine.summarize(state)
        if not uploaded_contexts:
            return base_summary
        upload_lines = [
            f"- {item.file_name}: {item.summary[:300]}"
            for item in uploaded_contexts
        ]
        return "\n".join([base_summary, "", "Uploaded Context:", *upload_lines])

    def _retrieve_context(self, query: str) -> tuple[list[RetrievedContextItem], str, str | None]:
        try:
            results = self._engine.search_context(
                query,
                limit=self._settings.context_result_limit,
                context_search_mode=ContextSearchMode.KEYWORD_SEARCH,
            )
            items = [
                RetrievedContextItem(
                    title=str(result.datasource_id),
                    content=result.context_result,
                    score=result.score,
                    source="dce_keyword_search",
                )
                for result in results
            ]
            if items:
                return items, "dce_keyword_search", None
        except Exception as exc:
            return self._fallback_scan(query), "fallback_scan", str(exc)
        return self._fallback_scan(query), "fallback_scan", None

    def _fallback_scan(self, query: str) -> list[RetrievedContextItem]:
        lowered_terms = {term for term in query.lower().split() if term}
        matches: list[RetrievedContextItem] = []
        for context in self._engine.get_all_contexts():
            haystack = context.context.lower()
            score = float(sum(1 for term in lowered_terms if term in haystack))
            if score <= 0:
                continue
            matches.append(
                RetrievedContextItem(
                    title=str(context.datasource_id),
                    content=context.context[:1200],
                    score=score,
                    source="context_fallback_scan",
                )
            )
        matches.sort(key=lambda item: item.score or 0.0, reverse=True)
        return matches[: self._settings.context_result_limit]
