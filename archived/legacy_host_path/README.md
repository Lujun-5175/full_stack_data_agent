## Legacy Host-Side Path Archive

This directory stores the retired host-side legacy modules that are no longer part of the active runtime path.

Archived modules:

- `agent/chat_agent.py`
- `agent/models.py`
- `llm/ollama_provider.py`
- `llm/base.py`
- `context/context_packet_builder.py`

Archived tests:

- `tests/legacy_provider_ollama.py`
- `tests/legacy_context_packet_builder.py`

Current active runtime chain:

`ui/app.py -> bootstrap.py -> app/chat_service.py -> app/databao_runtime.py -> Databao runtime/agent`

Do not reintroduce archived modules into the active package import path. Keep future work on the Databao runtime chain and its supporting layers.
