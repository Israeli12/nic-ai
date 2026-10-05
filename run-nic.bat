@echo off
REM Double-click helper for Windows: starts Ollama if needed, then the web UI.
cd /d "%~dp0"

where ollama >nul 2>nul
if errorlevel 1 (
  echo Ollama is not installed. Get it from https://ollama.com/download
  pause
  exit /b 1
)

REM 'ollama serve' is harmless if one is already running.
start "" /min ollama serve

if not exist config.yaml (
  echo config.yaml not found. Copy config.example.yaml to config.yaml first.
  pause
  exit /b 1
)

python -m nic serve
pause
