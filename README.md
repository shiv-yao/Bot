# Jupiter semi-live dashboard (Railway)

This is a semi-live Solana trading dashboard:
- token search and price data from Jupiter
- buy/sell buttons
- manual confirmation gate
- DRY_RUN enabled by default
- Railway-ready FastAPI backend + simple Streamlit UI

## Safety
- Keep `DRY_RUN=true` until you verify responses and limits.
- Do not put secrets in code.
- Use Railway Variables for `JUP_API_KEY` and any future wallet secrets.

## Local run
```bash
cp .env.example .env
pip install -r requirements.txt
uvicorn app.api:app --reload --port 8000
streamlit run app/dashboard.py
```

## Railway
1. Push this folder to GitHub
2. In Railway, create a new project from the repo
3. Add Variables from `.env.example`
4. Deploy
5. Open the service URL for the API
6. For the UI, either:
   - deploy `app/dashboard.py` as a separate Railway service, or
   - run the UI locally and point it to the Railway API

## Current behavior
- `/trade/buy` and `/trade/sell` call Jupiter quote/order endpoints.
- With `DRY_RUN=true`, the API returns the intended request without executing anything.
- `MANUAL_CONFIRM=true` requires `confirm=true` in the request body before order creation.
