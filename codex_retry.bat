@echo off
rem ============================================================
rem  codex_retry.bat -- restore a stalled target after Codex capacity appears
rem  "Selected model is at capacity" on screen.
rem  Tuned for Codex: trigger "at capacity".
rem  Extra args pass through to retry_watcher.py, e.g.
rem      codex_retry.bat --dry-run        detect only, no click
rem      codex_retry.bat --restore-delay 3  wait longer for the button
rem  Stop: Ctrl+C in this window, or slam the mouse into
rem  the top-left screen corner (failsafe).
rem  NOTE: keep this file ASCII-only with CRLF line endings,
rem  otherwise cmd.exe misparses it.
rem ============================================================
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 "%~dp0retry_watcher.py" --trigger "at capacity" %*
    exit /b %errorlevel%
)
python "%~dp0retry_watcher.py" --trigger "at capacity" %*
