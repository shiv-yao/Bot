# Cleanup report

## What changed
- Switched deployment to `main:app`.
- Replaced root `app.py` with a small compatibility wrapper.
- Moved demo / alternate entrypoints into `legacy/`.
- Removed committed `.env` and replaced it with `.env.example`.

## Why
The repository had multiple entrypoints and Railway was likely running the wrong one.
That caused quote-only/demo behavior to appear like real trading.

## Kept intact
- `bot.py` remains the real trading core.
- `core/` architecture layer remains available.
- `state.py` and existing API shape remain compatible.
