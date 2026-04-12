# Full Stack Data Agent

This workspace fuses:

- `databao/` from Databao Agent
- `databao_context_engine/` from Databao Context Engine
- `full_stack_data_agent/` as the local host app

The default runtime path is `.runtime/context_domain`, and the default model path is local Ollama with `gemma4:e4b`.

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
& 'C:\Program Files\MySQL\MySQL Shell 8.0\lib\Python3.13\Lib\venv\scripts\nt\python.exe' -m pip install -e .
```

4. Launch the UI:

```powershell
& 'C:\Program Files\MySQL\MySQL Shell 8.0\lib\Python3.13\Lib\venv\scripts\nt\python.exe' -m streamlit run full_stack_data_agent/ui/app.py
```

## What the UI shows

- Chat messages
- Provider/model/connection status
- Conversation ID and turn count
- Context packet details
- Agent request/response debug payloads
- Error details if Ollama or model availability fails
