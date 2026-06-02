# BTC 5m Event Prediction + Scale In

This is the active Railway BTC five-minute prediction-market Paper runtime.

It has one purpose:

```text
Predict whether BTC will move UP or DOWN in the current Polymarket 5-minute market
and simulate a capped three-stage scale-in position.
```

The Docker entrypoint is:

```text
python -m polymarket_latency_bot.btc5m_event_main
```

## Active behavior

The runtime starts only:

```text
BTC 5-minute Polymarket market discovery
Polymarket market WebSocket
Chainlink BTC/USD RTDS
Binance BTC/USDT WebSocket
Coinbase BTC/USD WebSocket
multi-source fusion
TradingView forecast adapter
CryptoQuant forecast adapter
Paper-only BTC 5m scale-in engine
read-only dashboard and status API
```

It does not start:

```text
general event market scanner
live orders
wallet signing
real-money execution
```

## Prediction output

The dashboard and `/status` endpoint return one of:

```text
YES
NO
WAIT
```

Logic:

```text
YES = predicted probability up is above the configured direction margin
NO  = predicted probability up is below the configured direction margin
WAIT = confidence or direction margin is insufficient
```

## Scale-in strategy

Each BTC five-minute market has one capped Paper budget. The engine may create at most three simulated entries:

```text
Stage 1: 50% of the round budget
Stage 2: 30% of the round budget
Stage 3: 20% of the round budget
```

Default timing:

```text
Stage 1: after 0 seconds
Stage 2: after 100 seconds
Stage 3: after 200 seconds
```

Every stage revalidates the signal. Stage 2 and Stage 3 are rejected when the prediction direction changes after the first entry. The engine does not create unlimited micro-orders and does not average down blindly.

Example with a `100 USDC` Paper budget:

```text
Stage 1: 50 USDC
Stage 2: 30 USDC
Stage 3: 20 USDC
Total:   100 USDC
```

## Dashboard

Open:

```text
/
```

The page shows:

```text
AI direction
probability up
confidence
selected edge
current BTC 5-minute market
market slug
market question
Paper portfolio
scale-in entries
source health
```

## API

```text
/status
/healthz
/paper/status
/paper/winrate
/paper/rounds
/docs
```

## Optional external forecasts

Write requests require `X-Webhook-Secret`.

### TradingView

```bash
curl -X POST "$BASE_URL/feeds/tradingview" \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: $WEBHOOK_SECRET" \
  -d '{"probability_up":0.58,"confidence":0.72}'
```

### CryptoQuant

```bash
curl -X POST "$BASE_URL/feeds/cryptoquant" \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: $WEBHOOK_SECRET" \
  -d '{"probability_up":0.54,"confidence":0.68}'
```

## Railway variables

Keep:

```env
AUTO_DISCOVER_MARKET=true
FORCE_BTC_5M_MARKET=true
MARKET_SLUG_PREFIX=btc-updown-5m-
MARKET_INTERVAL_SEC=300
ENABLE_BINANCE_WS=true
ENABLE_COINBASE_WS=true
ENABLE_MULTI_SOURCE_FUSION=true
MIN_CONFIDENCE=0.56
MIN_EDGE=0.02
WEBHOOK_SECRET=replace-with-a-random-secret-at-least-16-characters
```

Add or verify:

```env
BTC5M_PAPER_MAX_ROUND_NOTIONAL_USD=25
BTC5M_PAPER_SCALE_IN_WEIGHTS=0.50,0.30,0.20
BTC5M_PAPER_SCALE_IN_AFTER_SEC=0,100,200
BTC5M_PAPER_CLOSE_BUFFER_SEC=15
BTC5M_PAPER_MIN_CONFIDENCE=0.58
BTC5M_PAPER_MIN_PROBABILITY_MARGIN=0.015
```

Live-trading variables are not used by this runtime.

## Safety boundary

This deployment remains Paper-only. It simulates entries and settlement for evaluation, but it does not place live Polymarket orders, sign wallets or move funds.
