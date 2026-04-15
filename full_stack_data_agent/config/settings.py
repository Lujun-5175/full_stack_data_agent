from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values


def _read_env_file(env_path: Path) -> dict[str, str]:
    if not env_path.is_file():
        return {}
    values = dotenv_values(str(env_path))
    return {str(key): str(value) for key, value in values.items() if key is not None and value is not None}


def _parse_float(name: str, value: str) -> float:
    try:
        return float(value)
    except ValueError:
        raise ValueError(f"Environment variable {name}={value!r} is not a valid float") from None


def _parse_int(name: str, value: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"Environment variable {name}={value!r} is not a valid integer") from None


def _parse_bool(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Environment variable {name}={value!r} is not a valid boolean")


@dataclass(frozen=True)
class Settings:
    app_root: Path
    runtime_dir: Path
    domain_dir: Path
    llm_provider: str
    llm_fallback_provider: str
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    ollama_base_url: str
    ollama_model: str
    llm_timeout: float
    llm_temperature: float
    llm_num_ctx: int
    binding_llm_enabled: bool
    context_turn_window: int
    context_result_limit: int

    @property
    def provider_name(self) -> str:
        return self.llm_provider

    @property
    def fallback_provider_name(self) -> str:
        return self.llm_fallback_provider

    @property
    def active_model(self) -> str:
        return self.deepseek_model if self.llm_provider == "deepseek" else self.ollama_model

    @property
    def active_base_url(self) -> str:
        return self.deepseek_base_url if self.llm_provider == "deepseek" else self.ollama_base_url

    @property
    def active_api_key(self) -> str | None:
        key = self.deepseek_api_key.strip()
        return key or None

    @property
    def ollama_tags_url(self) -> str:
        return f"{self.ollama_base_url.rstrip('/')}/api/tags"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    app_root = Path(__file__).resolve().parents[2]
    env_values = _read_env_file(app_root / ".env")

    def read(name: str, default: str) -> str:
        return os.environ.get(name, env_values.get(name, default))

    def read_with_alias(primary: str, legacy: str, default: str) -> tuple[str, str]:
        if primary in os.environ:
            return primary, os.environ[primary]
        if legacy in os.environ:
            return legacy, os.environ[legacy]
        if primary in env_values:
            return primary, env_values[primary]
        if legacy in env_values:
            return legacy, env_values[legacy]
        return primary, default

    llm_timeout_key, llm_timeout_value = read_with_alias("LLM_TIMEOUT", "OLLAMA_TIMEOUT", "480")
    llm_temperature_key, llm_temperature_value = read_with_alias("LLM_TEMPERATURE", "OLLAMA_TEMPERATURE", "0.2")
    llm_num_ctx_key, llm_num_ctx_value = read_with_alias("LLM_NUM_CTX", "OLLAMA_NUM_CTX", "8192")
    binding_llm_enabled_value = read("BINDING_LLM_ENABLED", "true")

    runtime_dir = Path(read("APP_RUNTIME_DIR", str(app_root / ".runtime"))).resolve()
    domain_dir = Path(read("APP_DOMAIN_DIR", str(runtime_dir / "context_domain"))).resolve()
    return Settings(
        app_root=app_root,
        runtime_dir=runtime_dir,
        domain_dir=domain_dir,
        llm_provider=read("LLM_PROVIDER", "deepseek").strip() or "deepseek",
        llm_fallback_provider=read("LLM_FALLBACK_PROVIDER", "ollama").strip() or "ollama",
        deepseek_api_key=read("DEEPSEEK_API_KEY", ""),
        deepseek_base_url=read("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        deepseek_model=read("DEEPSEEK_MODEL", "deepseek-chat"),
        ollama_base_url=read("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        ollama_model=read("OLLAMA_MODEL", "gemma4:e4b"),
        llm_timeout=_parse_float(llm_timeout_key, llm_timeout_value),
        llm_temperature=_parse_float(llm_temperature_key, llm_temperature_value),
        llm_num_ctx=_parse_int(llm_num_ctx_key, llm_num_ctx_value),
        binding_llm_enabled=_parse_bool("BINDING_LLM_ENABLED", binding_llm_enabled_value),
        context_turn_window=int(read("CONTEXT_TURN_WINDOW", "6")),
        context_result_limit=int(read("CONTEXT_RESULT_LIMIT", "4")),
    )
