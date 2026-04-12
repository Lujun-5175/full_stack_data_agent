@echo off
setlocal

set "ROOT=D:\data_bao\full_stack_data_agent"
set "PYTHON_EXE=C:\Program Files\MySQL\MySQL Shell 8.0\lib\Python3.13\Lib\venv\scripts\nt\python.exe"

cd /d "%ROOT%"

echo Starting Full Stack Data Agent...
echo.
echo UI URL: http://127.0.0.1:8501
echo Provider: ollama
echo Model: gemma4:e4b
echo Runtime: editable install from current environment
echo.
echo If this is the first run, install dependencies with:
echo   "%PYTHON_EXE%" -m pip install -e . pytest
echo.

start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep 8; Start-Process 'http://127.0.0.1:8501'"
"%PYTHON_EXE%" -m streamlit run full_stack_data_agent\ui\app.py --global.developmentMode false

endlocal
