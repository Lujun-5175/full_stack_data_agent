import importlib.metadata

try:
    __version__ = importlib.metadata.version("databao-agent")
except importlib.metadata.PackageNotFoundError:
    __version__ = "0.0.0"


__all__ = ["__version__"]

try:
    from databao.agent.api import agent, domain
    from databao.agent.configs.llm import LLMConfig
    from databao.agent.core import (
        Agent,
        Domain,
        ExecutionResult,
        Executor,
        Opa,
        Thread,
        VisualisationResult,
        Visualizer,
    )
    from databao.agent.databases import DBConnection, DBConnectionConfig, DBConnectionRuntime

    __all__ += [
        "Agent",
        "DBConnection",
        "DBConnectionConfig",
        "DBConnectionRuntime",
        "Domain",
        "ExecutionResult",
        "Executor",
        "LLMConfig",
        "Opa",
        "Thread",
        "VisualisationResult",
        "Visualizer",
        "agent",
        "domain",
    ]
except Exception:
    # Preserve light-weight package imports for host applications that only need
    # specific submodules without pulling in every optional Databao dependency.
    pass
