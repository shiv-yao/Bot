# Bot-main organized

This cleaned package keeps your features but makes deployment deterministic.

## Run locally
```bash
cp .env.example .env
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Production flow
`main.py` -> starts FastAPI + launches `bot.bot_loop()` on startup.

## Important
Do not deploy `legacy/app_demo.py`. That file is preserved only for reference.
