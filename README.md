# Full Stack Data Agent

This workspace fuses:

- `databao/` from Databao Agent
- `databao_context_engine/` from Databao Context Engine
- `full_stack_data_agent/` as the local host app

The default runtime path is `.runtime/context_domain`.
The default runtime provider is now DeepSeek's official API with `deepseek-chat`, and Ollama remains available as a fallback.
The current default analysis chain is:

`Streamlit UI -> ChatService -> DatabaoRuntime -> databao.agent(...).thread().ask(...) -> text / df / plot / meta`

## Run

1. Set up your `.env` with a DeepSeek API key and optional Ollama fallback.

2. Install dependencies:

```powershell
& 'C:\Program Files\MySQL\MySQL Shell 8.0\lib\Python3.13\Lib\venv\scripts\nt\python.exe' -m pip install -e . pytest
```

3. Launch the UI:

- Double-click `Launch Full Stack Data Agent.cmd`, or run:

```powershell
& 'C:\Program Files\MySQL\MySQL Shell 8.0\lib\Python3.13\Lib\venv\scripts\nt\python.exe' -m streamlit run full_stack_data_agent/ui/app.py
```

4. Optional smoke tests:

```powershell
& 'C:\Program Files\MySQL\MySQL Shell 8.0\lib\Python3.13\Lib\venv\scripts\nt\python.exe' -m pytest
```

## Runtime Notes

- The repository should be run from an installed Python environment. The checked-in code no longer depends on `.vendor` to resolve imports.
- `Launch Full Stack Data Agent.cmd` now assumes dependencies are installed into the selected interpreter, rather than injecting `.vendor` into `PYTHONPATH`.
- The launcher will auto-install missing Python dependencies with `python -m pip install -e .` unless `FSDA_AUTO_INSTALL=0` is set.
- Legacy host-side chat facade modules still exist for reference, but the default analysis path is Databao-backed.
- To switch back to pure local mode, set `LLM_PROVIDER=ollama` and keep `LLM_FALLBACK_PROVIDER=ollama`.
- The UI header and sidebar show the active provider, active model, and fallback provider for the current session.

## What the UI shows

- Databao-backed text answers
- Active provider/model/fallback/connection status
- Conversation ID, turn count, and runtime diagnostics
- Table previews and row/column metadata when a dataframe is returned
- Plot spec/debug metadata when a visualization path is used
- Error details if DeepSeek, Ollama fallback, Databao runtime, or model availability fails

## Legacy Modules

These modules are still present but are no longer the default runtime path:

- `full_stack_data_agent/agent/chat_agent.py`
- `full_stack_data_agent/context/context_packet_builder.py`
- `full_stack_data_agent/llm/ollama_provider.py`
