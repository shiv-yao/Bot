# Deployment notes

## Official production entrypoint
- API entry: `main.py`
- ASGI app: `main:app`
- Trading loop: `bot.bot_loop`

## Legacy/demo files
These were moved to `legacy/` so they do not accidentally become the deploy target:
- `legacy/app_demo.py`
- `legacy/main_upgraded.py`
- `legacy/paper_engine.py`

## Railway
Use either:
- Procfile: `web: uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}`
- or railway.json `startCommand`

## API endpoints
- `GET /` basic status
- `GET /health` health summary
- `GET /data` engine snapshot
