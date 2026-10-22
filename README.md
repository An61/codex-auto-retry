# codex-auto-retry

`codex-auto-retry` monitors a Codex desktop screen for the capacity message
`Selected model is at capacity. Please try a different model.` and clicks the
target's play icon when the target is paused.

The monitor uses OCR to detect the capacity message and image matching to
recognize the target control. It keeps a separate pause template as a negative
check, so a running target with a pause icon is not clicked.

## Features

- Continuous screen monitoring with a configurable scan interval.
- Clicks only after the capacity message has been detected.
- Requires the play icon glyph to match and rejects the pause icon template.
- Uses stable-position checks and a 10-second minimum interval between clicks.
- Supports dry-run mode, bounded log rotation, screen regions, and a failsafe
  stop gesture.

## Requirements

- Windows with Python 3.10 or newer.
- A desktop session that is visible and unlocked. Screen matching cannot be
  relied on while Windows is locked or the display is asleep.
- Dependencies listed in `requirements.txt`.

Install them with:

```powershell
python -m pip install -r requirements.txt
```

## Run

For the bundled Windows launcher:

```text
codex_retry.bat
```

For a safe detection-only run:

```powershell
python retry_watcher.py --trigger "at capacity" --dry-run
```

The bundled templates are resolved relative to the script directory:

- `restore_target.png`: the play icon, which means the target is paused and
  can be started.
- `pause_target.png`: the pause icon, which means the target is running and
  must not be clicked.

Use `--log retry.log` to save output. Logs rotate at 5 MB by default; the
previous file is retained as `retry.log.1`. Set `--log-max-mb 0` only when an
unbounded log is intentional.

## Stop

- Press `Ctrl+C` in the console window.
- Move the mouse to the top-left corner to trigger PyAutoGUI's failsafe.

## Privacy

The program processes screenshots locally. It does not upload screenshots,
OCR results, logs, or credentials. Review custom templates and log paths before
sharing a public repository or issue report.

## License

MIT. See [LICENSE](LICENSE).
