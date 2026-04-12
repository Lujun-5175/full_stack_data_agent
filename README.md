# Full Stack Data Agent

This workspace fuses:

- `databao/` from Databao Agent
- `databao_context_engine/` from Databao Context Engine
- `full_stack_data_agent/` as the local host app

The default runtime path is `.runtime/context_domain`, and the default model path is local Ollama with `gemma4:e4b`.
The current default analysis chain is:

`Streamlit UI -> ChatService -> DatabaoRuntime -> databao.agent(...).thread().ask(...) -> text / df / plot / meta`

## Run

1. Start Ollama:

```powershell
ollama serve
```

2. Ensure the model exists:

```powershell
ollama pull gemma4:e4b
```

3. Install dependencies:

```powershell
& 'C:\Program Files\MySQL\MySQL Shell 8.0\lib\Python3.13\Lib\venv\scripts\nt\python.exe' -m pip install -e . pytest
```

4. Launch the UI:

- Double-click `Launch Full Stack Data Agent.cmd`, or run:

```powershell
& 'C:\Program Files\MySQL\MySQL Shell 8.0\lib\Python3.13\Lib\venv\scripts\nt\python.exe' -m streamlit run full_stack_data_agent/ui/app.py
```

5. Optional smoke tests:

```powershell
& 'C:\Program Files\MySQL\MySQL Shell 8.0\lib\Python3.13\Lib\venv\scripts\nt\python.exe' -m pytest
```

## Runtime Notes

- The repository should be run from an installed Python environment. The checked-in code no longer depends on `.vendor` to resolve imports.
- `Launch Full Stack Data Agent.cmd` now assumes dependencies are installed into the selected interpreter, rather than injecting `.vendor` into `PYTHONPATH`.
- Legacy host-side chat facade modules still exist for reference, but the default analysis path is Databao-backed.

## What the UI shows

- Databao-backed text answers
- Provider/model/connection status
- Conversation ID, turn count, and runtime diagnostics
- Table previews and row/column metadata when a dataframe is returned
- Plot spec/debug metadata when a visualization path is used
- Error details if Ollama, Databao runtime, or model availability fails

## Legacy Modules

These modules are still present but are no longer the default runtime path:

- `full_stack_data_agent/agent/chat_agent.py`
- `full_stack_data_agent/context/context_packet_builder.py`
- `full_stack_data_agent/llm/ollama_provider.py`
