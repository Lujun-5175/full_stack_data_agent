from __future__ import annotations

import ast
from pathlib import Path

from full_stack_data_agent.app.chat_service import ChatService
from full_stack_data_agent.app.databao_runtime import DatabaoRuntime
from full_stack_data_agent.bootstrap import bootstrap
from full_stack_data_agent.config.settings import get_settings


def test_bootstrap_returns_chat_service_backed_by_databao_runtime() -> None:
    service = bootstrap(get_settings())
    assert isinstance(service, ChatService)
    assert isinstance(service._runtime, DatabaoRuntime)  # type: ignore[attr-defined]


def test_ui_submit_path_calls_send_message_stream() -> None:
    package_root = Path(__file__).resolve().parents[1] / "full_stack_data_agent"
    ui_source = (package_root / "ui" / "app.py").read_text(encoding="utf-8")
    assert "service.send_message_stream(" in ui_source


def test_active_package_has_no_legacy_runtime_imports() -> None:
    package_root = Path(__file__).resolve().parents[1] / "full_stack_data_agent"
    retired_module_files = (
        package_root / "agent" / "chat_agent.py",
        package_root / "agent" / "models.py",
        package_root / "llm" / "ollama_provider.py",
        package_root / "llm" / "base.py",
        package_root / "context" / "context_packet_builder.py",
    )
    assert all(not path.exists() for path in retired_module_files)

    banned_imports = {
        "full_stack_data_agent.agent.chat_agent",
        "full_stack_data_agent.agent.models",
        "full_stack_data_agent.llm.ollama_provider",
        "full_stack_data_agent.llm.base",
        "full_stack_data_agent.context.context_packet_builder",
    }

    violations: list[str] = []
    for py_file in package_root.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8-sig"), filename=str(py_file))
        rel = py_file.relative_to(package_root)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in banned_imports:
                        violations.append(f"{rel}:{node.lineno} imports {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level != 0:
                    continue
                if module in banned_imports:
                    violations.append(f"{rel}:{node.lineno} imports from {module}")

    assert not violations, "Unexpected legacy runtime imports:\n" + "\n".join(violations)
