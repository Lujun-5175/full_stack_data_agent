from databao.agent.executors.dbt.executor import DbtProjectExecutor
from databao.agent.executors.lighthouse.executor import LighthouseExecutor
from databao.agent.executors.react_duckdb.executor import ReactDuckDBExecutor

__all__ = ["ClaudeCodeExecutor", "DbtProjectExecutor", "LighthouseExecutor", "ReactDuckDBExecutor"]


def __getattr__(name: str):
    if name == "ClaudeCodeExecutor":
        from databao.agent.executors.claude_code.executor import ClaudeCodeExecutor

        return ClaudeCodeExecutor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
