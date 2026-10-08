# Flipscan

Scan a stack through an ADF, flip it, scan the backs, then review everything later
in a local web UI (delete pages, set document cuts, rotate, name) and upload the
resulting PDFs to Paperless-ngx. Built for ADF scanners **without duplex**.

Originals are never modified; all edits live in the session's `session.json`.

## Install
Needs SANE (`scanimage`), Python >= 3.11 and [uv](https://docs.astral.sh/uv/).
Optional: `tesseract` (+ `deu`/`eng` data) for page-number detection.

    uv tool install .        # or: uv sync  (development, then `uv run flipscan ...`)
    flipscan init-config     # writes ~/.config/flipscan/config.toml
    scanimage -L             # find your scanner id, put it into the config

## Workflow
    flipscan scan [--new]       # add batches (ADF / flatbed) to the latest session
    flipscan review             # http://127.0.0.1:5000
    flipscan ocr                # optional: suggest cuts from "Seite 1 von N"
    flipscan upload [--export]  # retry failed uploads
    flipscan sessions           # list sessions;  --session NAME selects one

Review keys: `←/→` navigate, `c` cut before sheet, `x` delete, `r` rotate, `u` undo,
`n` name document, `R` rotate all back sides by 180° (fixes a wrong flip direction without rescanning), `B` delete suggested blanks, `C` accept suggested cuts, `space` zoom.
Blank pages and OCR cuts are only *suggestions*, never applied automatically.

## Configuration
Resolved from `--config`, `$FLIPSCAN_CONFIG`, then `~/.config/flipscan/config.toml`.
All keys and defaults are documented in `src/flipscan/config.example.toml`:
scanner, dpi, page size, scan format, back-side rotation, sessions dir, and one
`[upload]` backend: `scp`, `folder` or `paperless` (API URL + token).
Unknown keys and invalid values are rejected at startup.

Scan height and back-side rotation depend on your scanner and how you flip the stack, so
they are adjustable at three levels: config file, per-run flags
(`flipscan scan --height 290 --back-rotation 180 --dpi 600`), and afterwards in the
review UI (`R`, or `r` per page) without rescanning.

## Development
    uv sync && uv run pytest

MIT licensed.
