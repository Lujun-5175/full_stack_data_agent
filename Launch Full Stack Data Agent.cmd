@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "PORT=8501"

set "PYTHON_EXE="
set "PYTHON_ARGS="
if defined FSDA_PYTHON if exist "%FSDA_PYTHON%" set "PYTHON_EXE=%FSDA_PYTHON%"

if not defined PYTHON_EXE if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PYTHON_EXE if exist "%ProgramFiles%\Python313\python.exe" set "PYTHON_EXE=%ProgramFiles%\Python313\python.exe"
if not defined PYTHON_EXE if exist "%ProgramFiles(x86)%\Python313\python.exe" set "PYTHON_EXE=%ProgramFiles(x86)%\Python313\python.exe"
if not defined PYTHON_EXE if exist "C:\Program Files\MySQL\MySQL Shell 8.0\lib\Python3.13\Lib\venv\scripts\nt\python.exe" set "PYTHON_EXE=C:\Program Files\MySQL\MySQL Shell 8.0\lib\Python3.13\Lib\venv\scripts\nt\python.exe"
if not defined PYTHON_EXE for /f "delims=" %%I in ('where python 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%I"
if not defined PYTHON_EXE for /f "delims=" %%I in ('where py 2^>nul') do if not defined PYTHON_EXE (
  set "PYTHON_EXE=py"
  set "PYTHON_ARGS=-3.13"
)

if not defined PYTHON_EXE (
  echo Could not find a Python interpreter.
  echo Install Python 3.11+ or set FSDA_PYTHON to the full path of python.exe.
  pause
  exit /b 1
)

set "PS_EXE="
if exist "%ProgramFiles%\PowerShell\7\pwsh.exe" set "PS_EXE=%ProgramFiles%\PowerShell\7\pwsh.exe"
if not defined PS_EXE if exist "%ProgramW6432%\PowerShell\7\pwsh.exe" set "PS_EXE=%ProgramW6432%\PowerShell\7\pwsh.exe"
if not defined PS_EXE for /f "delims=" %%I in ('where pwsh 2^>nul') do if not defined PS_EXE set "PS_EXE=%%I"
if not defined PS_EXE set "PS_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

cd /d "%ROOT%"

echo PowerShell: "%PS_EXE%"
echo Using Python: "%PYTHON_EXE%"
echo Checking for stale Full Stack Data Agent processes on port %PORT%...

"%PS_EXE%" -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
  "$port = %PORT%;" ^
  "$connections = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue | Where-Object { $_.State -eq 'Listen' };" ^
  "foreach ($connection in $connections) {" ^
  "  $process = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $connection.OwningProcess) -ErrorAction SilentlyContinue;" ^
  "  if ($null -eq $process) { continue }" ^
  "  $commandLine = [string]$process.CommandLine;" ^
  "  if ($commandLine -match 'launch_fsda\.py' -or $commandLine -match 'full_stack_data_agent[\\/]+ui[\\/]+app\.py' -or $commandLine -match 'streamlit\s+run') {" ^
  "    Write-Host ('Stopping stale FSDA process ' + $process.ProcessId + ' on port ' + $port + '...');" ^
  "    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue;" ^
  "  }" ^
  "}"

echo Launching Full Stack Data Agent...
echo.

"%PYTHON_EXE%" %PYTHON_ARGS% "%ROOT%\launch_fsda.py"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo Launcher exited with code %EXIT_CODE%.
  pause
)

endlocal & exit /b %EXIT_CODE%
