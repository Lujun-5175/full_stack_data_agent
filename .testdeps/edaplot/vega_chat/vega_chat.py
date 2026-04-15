import copy
import dataclasses
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

import pandas as pd
from langchain_core.messages import BaseMessage, trim_messages
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.constants import END
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import Self

from edaplot.data_prompts import DEFAULT_DATA_STRATEGY, DataDescriptionStrategy
from edaplot.data_utils import df_preprocess
from edaplot.llms import LLMConfig, chat, chat_sync, get_chat_model
from edaplot.spec_utils import SpecType, spec_is_empty
from edaplot.vega import (
    MessageType,
    SpecInfo,
    VegaMessage,
    append_reply,
    logger,
    make_text_spec,
    process_extracted_specs,
    validate_and_fix_spec,
)
from edaplot.vega_chat.prompts import (
    ModelResponse,
    PromptVersion,
    extract_model_response,
    get_error_correction_prompt,
    get_select_spec_info_prompt,
    get_spec_fixed_user_prompt,
    get_system_prompt,
    get_user_prompt,
)


@dataclass(kw_only=True)
class MessageInfo:
    # (Legacy) VegaChat output container
    # TODO replace with VegaMessage
    # defaults for an invalid response
    # Store the chart validity because it's a slow operation to recompute each time.
    message: BaseMessage
    message_type: MessageType
    spec: SpecType | None = None
    is_spec_fixed: bool = False
    is_empty_chart: bool = True
    is_valid_schema: bool = False
    is_drawable: bool = False
    model_response: ModelResponse | None = None

    def get_spec_info(self) -> SpecInfo | None:
        if self.spec is None:
            return None
        return SpecInfo(
            spec=self.spec,
            is_spec_fixed=self.is_spec_fixed,
            is_empty_chart=self.is_empty_chart,
            is_valid_schema=self.is_valid_schema,
            is_drawable=self.is_drawable,
        )

    def to_vega_message(self) -> VegaMessage:
        spec_info = self.get_spec_info()
        return VegaMessage(
            message=self.message,
            message_type=self.message_type,
            spec_infos=[] if spec_info is None else [spec_info],
            explanation=self.model_response.explanation if self.model_response is not None else None,
        )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        d["message_type"] = MessageType(d["message_type"])
        if "model_response" in d:
            d["model_response"] = ModelResponse(**d["model_response"]) if d["model_response"] is not None else None
        else:
            d["model_response"] = ModelResponse(specs=[], explanation=d.pop("explanation"))
        return cls(**d)

    @classmethod
    def new(cls, content: str, message_type: MessageType) -> Self:
        message = MessageType.create_message(content, message_type)
        return cls(message=message, message_type=message_type)


@dataclass(kw_only=True)
class VegaChatConfig:
    llm_config: LLMConfig = field(default_factory=lambda: LLMConfig(name="gpt-4.1-mini-2025-04-14"))

    language: str | None = "English"
    n_ec_retries: int = 5
    description_strategy: DataDescriptionStrategy = DEFAULT_DATA_STRATEGY
    message_trimmer_max_tokens: int = 8192
    retry_on_empty_plot: bool = True
    prompt_version: PromptVersion = "vega_chat_v1"
    retry_on_irrelevant_request: bool = False

    data_normalize_column_names: bool = True
    data_parse_dates: bool = True

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        fields = {f.name for f in dataclasses.fields(cls)}
        if "llm_config" in d:
            d["llm_config"] = LLMConfig(**d.pop("llm_config"))
        if "model_name" in d:  # backwards compatibility
            d["llm_config"] = LLMConfig(name=d.pop("model_name"), temperature=d.pop("temperature", 0.0))
        extra_fields = set()
        for k in d:
            if k not in fields:
                logger.warning(f"Skipping unknown config field: {k}")
                extra_fields.add(k)
        return cls(**{k: v for k, v in d.items() if k not in extra_fields})


class VegaChatState(TypedDict, total=False):
    messages: list[MessageInfo]
    spec_history: dict[int, SpecInfo]
    should_retry: bool
    attempt: int
    max_attempts: int


class VegaChatGraph:
    def __init__(
        self,
        config: VegaChatConfig,
        df: pd.DataFrame,
        metadata: str = "",
    ):
        self._config = config
        self._df = df_preprocess(
            df, normalize_column_names=config.data_normalize_column_names, parse_dates=config.data_parse_dates
        )
        self._llm = get_chat_model(config.llm_config)
        self._max_error_length = int(config.message_trimmer_max_tokens * 0.33)
        self._message_trimmer = trim_messages(
            max_tokens=config.message_trimmer_max_tokens,
            strategy="last",
            token_counter=count_tokens_approximately,
            include_system=True,
            allow_partial=False,
            start_on="human",
        )
        self._system_prompt = get_system_prompt(
            config.prompt_version,
            df=self._df,
            data_description_strategy=config.description_strategy,
            extra_metadata=metadata,
            language=config.language,
        )

    @property
    def dataframe(self) -> pd.DataFrame:
        return self._df

    def _get_user_prompt(self, content: str, *, last_message: MessageInfo | None = None) -> str:
        # For multi-turn prompts, tell the LLM about the possibly fixed spec so it doesn't use
        # the incorrect generated one
        if last_message is not None and last_message.is_spec_fixed:
            assert last_message.spec is not None
            return get_spec_fixed_user_prompt(last_message.spec, content)
        return get_user_prompt(content)

    def _process_response(
        self, response: MessageInfo, spec_history: dict[int, SpecInfo]
    ) -> tuple[MessageInfo, str | None]:
        response = copy.copy(response)  # Avoid modifying the original
        try:
            extracted_response = extract_model_response(response.message.text)
            extracted_spec = process_extracted_specs(extracted_response.specs)
        except ValueError as e:
            reply = get_error_correction_prompt(str(e), max_length=self._max_error_length)
            response.message_type = MessageType.AI_RESPONSE_ERROR
            return response, reply

        # If the LLM doesn't generate a spec because the request is irrelevant,
        # return a dummy spec instead to avoid going into an error retrying loop.
        if (
            not self._config.retry_on_irrelevant_request
            and not extracted_response.relevant_request
            and not extracted_response.data_exists
            and spec_is_empty(extracted_spec)
        ):
            text_content = append_reply(
                "Invalid.",
                append_reply(extracted_response.relevant_request_rationale, extracted_response.data_exists_rationale),
            )
            assert text_content is not None
            response.spec = make_text_spec(text_content)
            response.model_response = extracted_response
            response.is_spec_fixed = False
            response.is_valid_schema = True
            response.is_empty_chart = True
            response.is_drawable = True
            return response, None
        else:
            spec_history_values = [v for (k, v) in sorted(spec_history.items())]
            spec_fix = validate_and_fix_spec(
                extracted_spec,
                self._df,
                retry_on_empty_plot=self._config.retry_on_empty_plot,
                max_reply_length=self._max_error_length,
                spec_history=spec_history_values,
            )
            assert spec_fix.spec_validity is not None

            response.spec = spec_fix.spec
            response.model_response = extracted_response
            response.is_spec_fixed = extracted_spec != spec_fix.spec
            response.is_valid_schema = spec_fix.spec_validity.is_valid_schema
            response.is_empty_chart = spec_fix.spec_validity.is_empty_scenegraph
            response.is_drawable = spec_fix.spec_validity.is_valid_scenegraph

            if not response.is_drawable or (self._config.retry_on_empty_plot and response.is_empty_chart):
                assert spec_fix.reply is not None
                response.message_type = MessageType.AI_RESPONSE_ERROR

            if spec_fix.reply is not None:
                # Not necessarily undrawable, but it causes a retry
                response.message_type = MessageType.AI_RESPONSE_ERROR
            return response, spec_fix.reply

    def get_start_messages(self) -> list[MessageInfo]:
        return [MessageInfo.new(self._system_prompt, MessageType.SYSTEM)]

    def get_start_state(
        self,
        q: str,
        force_q: bool = False,
        message_type: MessageType = MessageType.USER,
        messages: list[MessageInfo] | None = None,
        max_attempts: int | None = None,
    ) -> VegaChatState:
        content = (
            q if force_q else self._get_user_prompt(q, last_message=messages[-1] if messages is not None else None)
        )
        new_message = MessageInfo.new(content, message_type)
        old_messages = messages or self.get_start_messages()
        new_messages = [*old_messages, new_message]

        spec_history: dict[int, SpecInfo] = {}  # Map from message index to spec
        if messages is not None:
            for idx, message in enumerate(messages):
                if (spec_info := message.get_spec_info()) is not None:
                    spec_history[idx] = spec_info

        return {
            "messages": new_messages,
            "spec_history": spec_history,
            "attempt": 0,
            "max_attempts": max_attempts if max_attempts is not None else self._config.n_ec_retries,
        }

    def _prepare_messages_for_llm(self, messages: list[MessageInfo]) -> list[BaseMessage]:
        llm_messages = [m.message for m in messages]
        llm_messages = self._message_trimmer.invoke(llm_messages)
        return llm_messages  # type: ignore[no-any-return]

    def _process_llm_responses(self, messages: list[MessageInfo], llm_response: BaseMessage) -> VegaChatState:
        response_message = MessageInfo(message=llm_response, message_type=MessageType.AI_RESPONSE_VALID)
        new_messages = [*messages, response_message]
        return {"messages": new_messages}

    def _node_llm_sync(self, state: VegaChatState) -> VegaChatState:
        messages = state["messages"]
        llm_messages = self._prepare_messages_for_llm(messages)
        response_llm_messages = chat_sync(llm_messages, self._config.llm_config, model=self._llm)
        return self._process_llm_responses(messages, response_llm_messages[-1])

    async def _node_llm(self, state: VegaChatState) -> VegaChatState:
        messages = state["messages"]
        llm_messages = self._prepare_messages_for_llm(messages)
        response_llm_messages = await chat(llm_messages, self._config.llm_config, model=self._llm)
        return self._process_llm_responses(messages, response_llm_messages[-1])

    def _node_validate(self, state: VegaChatState) -> VegaChatState:
        spec_history = state["spec_history"]
        new_spec_history = spec_history.copy()
        messages = state["messages"]

        response, reply = self._process_response(messages[-1], spec_history)
        if (spec_info := response.get_spec_info()) is not None:
            new_spec_history[len(messages) - 1] = spec_info

        new_messages = [*messages[:-1], response]
        attempt = state["attempt"] + 1

        # Prepare for the next attempt if necessary
        should_retry = attempt <= self._config.n_ec_retries and reply is not None
        if should_retry:
            assert reply is not None
            reply_message = MessageInfo.new(reply, MessageType.USER_ERROR_CORRECTION)
            new_messages.append(reply_message)

        return VegaChatState(
            messages=new_messages,
            spec_history=new_spec_history,
            attempt=attempt,
            should_retry=should_retry,
        )

    def parse_final_state(self, state: VegaChatState) -> MessageInfo:
        messages = state["messages"]
        return messages[-1]

    def compile_graph(self, *, is_async: bool) -> CompiledStateGraph[Any]:
        def node_should_retry(state: VegaChatState) -> Literal["llm", "__end__"]:
            return "llm" if state["should_retry"] else END  # type: ignore[return-value]

        graph = StateGraph(VegaChatState)

        graph.add_node("llm", self._node_llm if is_async else self._node_llm_sync)
        graph.add_node("validate", self._node_validate)

        graph.set_entry_point("llm")
        graph.add_edge("llm", "validate")
        graph.add_conditional_edges("validate", node_should_retry)

        return graph.compile()


class VegaChat:
    # (Legacy) Stateful implementation tied to the workings of the UI

    def __init__(
        self,
        config: VegaChatConfig,
        graph: VegaChatGraph,
    ) -> None:
        # Stateful settings for the UI
        self._graph = graph
        self._n_retries = config.n_ec_retries
        self._is_running = False  # To help the UI
        self._spec_history: dict[int, SpecInfo] = {}  # Map from message index to spec

        self._messages: list[MessageInfo] = self._graph.get_start_messages()

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def dataframe(self) -> pd.DataFrame:
        return self._graph.dataframe

    @property
    def messages(self) -> list[MessageInfo]:
        return self._messages

    @property
    def last_message(self) -> MessageInfo:
        # There is always at least the system message
        return self._messages[-1]

    def set_num_error_retries(self, new_value: int) -> None:
        self._n_retries = new_value

    def add_user_message(self, content: str, message_type: MessageType = MessageType.USER) -> MessageInfo:
        message = MessageInfo.new(content, message_type)
        self._messages.append(message)
        return message

    def select_chart(self, spec_info: SpecInfo) -> None:
        spec_info_prompt = get_select_spec_info_prompt(
            spec_info.spec, is_drawable=spec_info.is_drawable, is_empty_chart=spec_info.is_empty_chart
        )
        self.add_user_message(spec_info_prompt, MessageType.USER_ERROR_CORRECTION)
        self._spec_history[len(self._messages) - 1] = spec_info

    def get_last_user_message_index(self) -> int | None:
        for i, m in enumerate(reversed(self._messages)):
            if m.message_type == MessageType.USER:
                return len(self._messages) - i - 1
        return None

    def clear_messages_from_index(self, message_index: int) -> None:
        """Revert the chat state to just before the given message index."""
        if message_index <= 0:
            raise ValueError("message_index must be > 0")
        self._messages = self._messages[:message_index]
        spec_history_indices = list(self._spec_history.keys())
        for i in spec_history_indices:
            if i >= message_index:
                del self._spec_history[i]

    def start_query(
        self, q: str, *, force_q: bool = False, message_type: MessageType = MessageType.USER, is_async: bool = True
    ) -> tuple[VegaChatState, CompiledStateGraph[VegaChatState, Any, VegaChatState, VegaChatState]]:
        self._is_running = True
        start_state = self._graph.get_start_state(
            q, force_q, message_type, self._messages, max_attempts=self._n_retries
        )
        compiled_graph = self._graph.compile_graph(is_async=is_async)
        return start_state, compiled_graph

    def submit_query(self, final_state: VegaChatState) -> MessageInfo:
        self._messages = final_state["messages"]
        self._is_running = False
        return self._graph.parse_final_state(final_state)

    async def query(self, q: str, force_q: bool = False, message_type: MessageType = MessageType.USER) -> MessageInfo:
        start_state, compiled_graph = self.start_query(q, force_q=force_q, message_type=message_type, is_async=True)
        final_state: VegaChatState = await compiled_graph.ainvoke(start_state)  # type: ignore[assignment]
        return self.submit_query(final_state)

    def query_sync(self, q: str, force_q: bool = False, message_type: MessageType = MessageType.USER) -> MessageInfo:
        start_state, compiled_graph = self.start_query(q, force_q=force_q, message_type=message_type, is_async=True)
        final_state: VegaChatState = compiled_graph.invoke(start_state)  # type: ignore[assignment]
        return self.submit_query(final_state)

    @classmethod
    def from_config(cls, config: VegaChatConfig, df: pd.DataFrame, metadata: str = "") -> Self:
        graph = VegaChatGraph(config, df, metadata=metadata)
        return cls(config, graph)
