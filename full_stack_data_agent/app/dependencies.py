from __future__ import annotations

from dataclasses import asdict, dataclass, field
from importlib.util import find_spec
from typing import Any


_REQUIRED_RUNTIME_MODULES = (
    "langchain_core",
    "langchain",
    "langgraph",
)


@dataclass(frozen=True)
class RuntimeDependencyStatus:
    missing_modules: list[str] = field(default_factory=list)
    available_modules: list[str] = field(default_factory=list)
    install_hint: str = "python -m pip install -e ."
    error: str | None = None

    @property
    def is_ok(self) -> bool:
        return not self.missing_modules

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_runtime_dependencies() -> RuntimeDependencyStatus:
    missing_modules: list[str] = []
    available_modules: list[str] = []
    for module_name in _REQUIRED_RUNTIME_MODULES:
        if find_spec(module_name) is None:
            missing_modules.append(module_name)
        else:
            available_modules.append(module_name)

    install_hint = "python -m pip install -e ."
    error = None
    if missing_modules:
        error = (
            "Missing runtime dependencies: "
            + ", ".join(missing_modules)
            + f". Install them in the same Python interpreter with `{install_hint}` and restart Streamlit."
        )

    return RuntimeDependencyStatus(
        missing_modules=missing_modules,
        available_modules=available_modules,
        install_hint=install_hint,
        error=error,
    )
