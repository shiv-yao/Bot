# Polymarket Latency Bot — paper-first CLOB V2 reference

A deployable Python reference implementation for monitoring BTC prediction signals and comparing them with configured Polymarket outcome token prices.

## Important boundaries

This is not a guaranteed-arbitrage system. A difference between a prediction and a market price is a model edge estimate, not a risk-free spread. Network latency, queueing, slippage, fill risk, stale data, fees, market resolution rules, and provider licensing all matter.

The project defaults to `LIVE_TRADING=false`. Live order placement is enabled only when:

```env
LIVE_TRADING=true
LIVE_CONFIRMATION=I_UNDERSTAND_LIVE_ORDERS
```

## What is included

- CLOB V2 integration through `py-clob-client-v2`
- public market WebSocket orderbook updates
- Polymarket RTDS BTC feed subscription
- authenticated user WebSocket listener for order and trade events
- webhook endpoint for TradingView-style prediction alerts
- optional generic JSON polling adapter for CryptoQuant or another licensed provider
- consensus prediction engine
- dynamic regime multiplier
- single-order and daily-loss risk limits
- token bucket rate limiting, bounded queue, cooldown, timeout, reconnects, JSON logs
- FastAPI health, state, signal, and manual PnL adjustment endpoints
- Docker deployment files

## Setup

```bash
cp .env.example .env
# Fill YES_TOKEN_ID and NO_TOKEN_ID first.
# Keep LIVE_TRADING=false during validation.
docker compose up --build
```

Health endpoint:

```bash
curl http://localhost:8080/health
```

Inject a prediction signal:

```bash
curl -X POST http://localhost:8080/feeds/prediction \
  -H 'Content-Type: application/json' \
  -H 'X-Webhook-Secret: change-me' \
  -d '{"source":"tradingview","probability_up":0.61,"confidence":0.75}'
```

## Live credentials

For live mode, set wallet and CLOB V2 values in `.env`. Do not commit `.env`. New Polymarket API users should follow the deposit-wallet flow described in current official documentation; existing wallet types must use the matching signature type and correct funder address.

## Performance notes

The event loop is asynchronous, but end-to-end latency is not guaranteed. Place the service near the exchange edge, measure p50/p95/p99 latency, use a stable host, and obey current endpoint rate limits. This code intentionally applies backpressure rather than sending uncontrolled order floods.

## TradingView and CryptoQuant

TradingView alerts can POST to `/feeds/prediction`. The external polling adapter is intentionally generic because provider plans, schemas, and entitlements differ. Configure the JSON path fields rather than scraping provider websites.

## Disclaimer

Use a small isolated wallet and paper mode first. You are responsible for jurisdiction, provider terms, exchange terms, taxes, and financial risk.
