@echo off
rem ============================================================
rem  codex_retry.bat -- restore a stalled target after Codex capacity appears
rem  "Selected model is at capacity" on screen.
rem  Tuned for Codex: trigger "at capacity".
rem  The launcher auto-detects 1-4 visible Codex windows by default.
rem  Extra args pass through to retry_watcher.py, e.g.
rem      codex_retry.bat --dry-run        detect only, no click
rem      codex_retry.bat --restore-delay 3  wait longer for the button
rem      codex_retry.bat --windows 1      monitor the full screen as one area
rem  Stop: Ctrl+C in this window, or slam the mouse into
rem  the top-left screen corner (failsafe).
rem  NOTE: keep this file ASCII-only with CRLF line endings,
rem  otherwise cmd.exe misparses it.
rem ============================================================
setlocal
if exist "%~dp0codex_retry.local.bat" call "%~dp0codex_retry.local.bat"

if defined CODEX_AUTO_RETRY_PYTHON (
    "%CODEX_AUTO_RETRY_PYTHON%" "%~dp0retry_watcher.py" --trigger "at capacity" --windows auto %*
    set "EXIT_CODE=%ERRORLEVEL%"
    goto finish
)

where py >nul 2>&1
if %errorlevel%==0 (
    py -3 "%~dp0retry_watcher.py" --trigger "at capacity" --windows auto %*
    set "EXIT_CODE=%ERRORLEVEL%"
    goto finish
)
python "%~dp0retry_watcher.py" --trigger "at capacity" --windows auto %*
set "EXIT_CODE=%ERRORLEVEL%"

:finish
if not "%EXIT_CODE%"=="0" (
    echo.
    echo codex-auto-retry exited with code %EXIT_CODE%.
    pause
)
endlocal & exit /b %EXIT_CODE%
