@echo off
rem Opens the window as it was before the October 2026 redesign (version 5.1), in case the new one misbehaves.
rem It reads the same roster and the same saved corrections, and writes the same files.
setlocal
set "HOME_DIR=%~dp0"
where pythonw >nul 2>nul
if errorlevel 1 goto console
start "" pythonw "%HOME_DIR%tool\app5.pyw"
exit /b 0

:console
python "%HOME_DIR%tool\app5.pyw"
if errorlevel 1 pause
exit /b
