from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _read_env_file(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.is_file():
        return values
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


@dataclass(frozen=True)
class Settings:
    app_root: Path
    runtime_dir: Path
    domain_dir: Path
    ollama_base_url: str
    ollama_model: str
    ollama_timeout: float
    ollama_temperature: float
    ollama_num_ctx: int
    context_turn_window: int
    context_result_limit: int
    provider_name: str = "ollama"

    @property
    def ollama_tags_url(self) -> str:
        return f"{self.ollama_base_url.rstrip('/')}/api/tags"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    app_root = Path(__file__).resolve().parents[2]
    env_values = _read_env_file(app_root / ".env")

    def read(name: str, default: str) -> str:
        return os.environ.get(name, env_values.get(name, default))

    runtime_dir = Path(read("APP_RUNTIME_DIR", str(app_root / ".runtime"))).resolve()
    domain_dir = Path(read("APP_DOMAIN_DIR", str(runtime_dir / "context_domain"))).resolve()
    return Settings(
        app_root=app_root,
        runtime_dir=runtime_dir,
        domain_dir=domain_dir,
        ollama_base_url=read("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        ollama_model=read("OLLAMA_MODEL", "gemma4:e4b"),
        ollama_timeout=float(read("OLLAMA_TIMEOUT", "480")),
        ollama_temperature=float(read("OLLAMA_TEMPERATURE", "0.2")),
        ollama_num_ctx=int(read("OLLAMA_NUM_CTX", "8192")),
        context_turn_window=int(read("CONTEXT_TURN_WINDOW", "6")),
        context_result_limit=int(read("CONTEXT_RESULT_LIMIT", "4")),
    )
