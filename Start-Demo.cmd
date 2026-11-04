@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Create the Python environment first. See docs\pre_alpha_demo.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m fairway_fusion.demo.server --port 8765 --open
pause
