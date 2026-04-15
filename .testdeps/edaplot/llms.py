import os
from collections.abc import Sequence
from typing import Any, Literal

from langchain.chat_models import init_chat_model
from langchain.tools import BaseTool
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field

_OPENAI_PREFIXES = ["gpt", "o1", "o3"]
_ANTHROPIC_PREFIXES = ["claude", "anthropic"]
_OPENAI_REASONING_INFIXES = ["o1", "o3", "gpt-5", "openai/gpt-oss"]


class LLMConfig(BaseModel):
    name: str
    """The model name can be of the form 'provider:name' or 'name'."""

    temperature: float = 0.0
    max_tokens: int = 8192
    reasoning_effort: str = "medium"
    """Reasoning effort is used for OpenAI reasoning models only. 
    Warning: reasoning can use a lot of tokens! OpenAI recommends at least 25000 tokens"""
    cache_system_prompt: bool = True
    """Cache system prompt with prompt caching. Only used for Anthropic models."""
    # TODO multi-turn prompt caching
    timeout: int | None | Literal["auto"] = "auto"
    """Timeout in seconds for LLM calls. If None, use the LLM provider's defaults. 
    If 'auto', use a reasonable timeout that increases for reasoning models."""

    api_base_url: str | None = None
    """Base URL for an OpenAI-compatible API like 'http://localhost:8080/v1'. Mostly used for running local models."""
    use_responses_api: bool = True
    """Use the [responses API](https://platform.openai.com/docs/guides/migrate-to-responses) for OpenAI models. 
    If False, use the old Chat Completions API (useful for local models that don't support the new responses API)."""

    ollama_pull_model: bool = True
    """Pull the model from Ollama if it's not already downloaded. Only applicable for the 'ollama' model provider."""

    model_kwargs: dict[str, Any] = Field(default_factory=dict)
    """Additional kwargs for the model constructor."""

    model_config = ConfigDict(extra="allow")  # To deserialize old configs

    def resolve_timeout(self) -> float | None:
        if self.timeout == "auto":
            return 360 if is_reasoning_model(self.name) else 60
        else:
            return self.timeout


def is_reasoning_model(model_name: str) -> bool:
    """Check if a model is a reasoning model based on its name."""
    return any(prefix in model_name for prefix in _OPENAI_REASONING_INFIXES)


def is_openai_model(model_name: str) -> bool:
    """Check if a model is an OpenAI model based on its name."""
    return any(model_name.startswith(prefix) for prefix in _OPENAI_PREFIXES)


def is_anthropic_model(model_name: str) -> bool:
    """Check if a model is an Anthropic model based on its name."""
    return any(model_name.startswith(prefix) for prefix in _ANTHROPIC_PREFIXES)


def parse_model_provider(model: str) -> tuple[str, str]:
    """Parse the provider and model name from a string of the form 'provider:name' or 'name'."""
    provider, sep, name = model.partition(":")
    if len(sep) == 0 and len(name) == 0:
        if is_openai_model(model):
            return "openai", model
        elif is_anthropic_model(model):
            return "anthropic", model
        else:
            return "", model
    return provider, name


def get_chat_model(config: LLMConfig) -> BaseChatModel:
    provider, name = parse_model_provider(config.name)
    if provider == "openai" or config.api_base_url is not None:
        from langchain_openai import ChatOpenAI

        # Use the verbatim name if using an OAI server
        model_name = config.name if config.api_base_url is not None else name

        is_reasoning = is_reasoning_model(model_name)
        extra_kwargs: dict[str, Any] = {}
        if config.use_responses_api:
            extra_kwargs.update(
                # Without "summary", no reasoning traces will be returned by the API
                reasoning={"effort": config.reasoning_effort, "summary": "auto"} if is_reasoning else None,
                temperature=config.temperature,
                # TODO output_version="responses/v1"
            )
        else:
            extra_kwargs.update(
                reasoning_effort=config.reasoning_effort if is_reasoning else None,
                # The old API errors out if you provide a temperature for reasoning models
                temperature=config.temperature if not is_reasoning else None,
            )

        # Set a default API key for local models if the user didn't provide one
        if (
            config.api_base_url is not None
            and "api_key" not in config.model_kwargs
            and "OPENAI_API_KEY" not in os.environ
        ):
            extra_kwargs["api_key"] = "local-api-key"

        return ChatOpenAI(
            model=model_name,
            timeout=config.resolve_timeout(),
            max_tokens=config.max_tokens,
            base_url=config.api_base_url,
            use_responses_api=config.use_responses_api,
            **extra_kwargs,
            **config.model_kwargs,
        )
    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model_name=name,
            timeout=config.resolve_timeout(),
            temperature=config.temperature,
            max_tokens_to_sample=config.max_tokens,
            **config.model_kwargs,
        )

    if provider == "ollama" and config.ollama_pull_model:
        import ollama

        # Download with ollama. If the model already exists it will not be re-downloaded.
        ollama.pull(name)

    return init_chat_model(
        config.name,
        configurable_fields=None,  # Ensures we match the BaseChatModel overload
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        timeout=config.resolve_timeout(),
        **config.model_kwargs,
    )


def model_bind_tools(
    model: BaseChatModel, tools: Sequence[BaseTool], **kwargs: Any
) -> Runnable[LanguageModelInput, BaseMessage]:
    if isinstance(model, ChatOpenAI):
        return model.bind_tools(tools, strict=True, **kwargs)
    else:
        return model.bind_tools(tools, **kwargs)


def set_anthropic_cache_breakpoint(content: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(content, str):
        return {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
    elif isinstance(content, dict):
        d = content.copy()
        d["cache_control"] = {"type": "ephemeral"}
        return d
    else:
        raise ValueError(f"Unknown content type: {type(content)}")


def set_message_cache_breakpoint(config: LLMConfig, message: BaseMessage) -> BaseMessage:
    """Enable prompt caching for this message (for Anthropic models).

    If you have a list of messages, set a breakpoint only on the last message to automatically cache all previous messages.

    See https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
    > Prompt caching references the entire prompt - tools, system, and messages (in that order) up to and including the block designated with cache_control.
    """
    if not is_anthropic_model(config.name):
        return message
    new_content: list[dict[str, Any] | str]
    match message.content:
        case str() | dict():
            new_content = [set_anthropic_cache_breakpoint(message.content)]
        case list():
            # Set checkpoint only for the last message
            new_content = message.content.copy()
            new_content[-1] = set_anthropic_cache_breakpoint(new_content[-1])
    return message.model_copy(update={"content": new_content})


def apply_system_prompt_caching(config: LLMConfig, messages: list[BaseMessage]) -> list[BaseMessage]:
    """Apply system prompt caching for Anthropic models."""
    if not (config.cache_system_prompt and is_anthropic_model(config.name)):
        return messages
    # Assume only the first message can be a system prompt.
    assert all(m.type != "system" for m in messages[1:])
    if messages[0].type == "system":
        messages = [set_message_cache_breakpoint(config, messages[0]), *messages[1:]]
    return messages


async def _call_model(model: Runnable[Any, Any], messages: list[BaseMessage]) -> Any:
    return await model.with_retry(wait_exponential_jitter=True, stop_after_attempt=3).ainvoke(messages)


def _call_model_sync(model: Runnable[Any, Any], messages: list[BaseMessage]) -> Any:
    return model.with_retry(wait_exponential_jitter=True, stop_after_attempt=3).invoke(messages)


async def chat(
    messages: list[BaseMessage],
    config: LLMConfig,
    model: Runnable[Any, Any] | None = None,
) -> list[BaseMessage]:
    if model is None:
        model = get_chat_model(config)
    messages = apply_system_prompt_caching(config, messages)
    response: AIMessage = await _call_model(model, messages)
    return [*messages, response]


def chat_sync(
    messages: list[BaseMessage],
    config: LLMConfig,
    model: Runnable[Any, Any] | None = None,
) -> list[BaseMessage]:
    if model is None:
        model = get_chat_model(config)
    messages = apply_system_prompt_caching(config, messages)
    response: AIMessage = _call_model_sync(model, messages)
    return [*messages, response]


async def chat_structured_output(
    messages: list[BaseMessage],
    config: LLMConfig,
    schema: type[BaseModel],
    model: BaseChatModel | None = None,
) -> tuple[list[BaseMessage], BaseModel]:
    if model is None:
        model = get_chat_model(config)
    structured_model = model.with_structured_output(schema, include_raw=True)

    messages = apply_system_prompt_caching(config, messages)
    response = await _call_model(structured_model, messages)

    raw_message: AIMessage = response["raw"]
    parsed_output: BaseModel = response["parsed"]

    if not isinstance(parsed_output, schema):
        raise ValueError(f"The object returned by the llm is not a valid {schema.__name__}: {parsed_output}")

    # OpenAI returns "structured outputs" (json), while Claude returns a tool call and expects the next message
    # to be the tool result so always convert to json.
    # Copy response metadata (usage details) to new message
    resp_message = raw_message.model_copy(
        update={"content": parsed_output.model_dump_json(), "tool_calls": [], "invalid_tool_calls": []}
    )
    return [*messages, resp_message], parsed_output
