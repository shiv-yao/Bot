# Merge report

This version merges the two uploaded files with a single-production-entry approach.

## What was kept from `bot.py`
- Real trading core
- Wallet loading and RPC config
- Jupiter order / execute flow
- Candidate discovery, monitor, AI loop, risk checks
- `bot_loop()` as the only trading loop

## What was kept from `app.py`
- FastAPI-style status endpoints idea
- `/ping`
- simple JSON snapshot endpoint behavior

## What changed
- Production entry is `main:app`
- Root `app.py` is now only a compatibility launcher
- Uploaded demo `app.py` was preserved as `legacy/app_demo_uploaded.py`
- Added `/debug` as alias of `/data` so Railway debugging is easier

## Deploy command
`uvicorn main:app --host 0.0.0.0 --port $PORT`
