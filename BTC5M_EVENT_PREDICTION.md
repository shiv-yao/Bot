# BTC 5m Event Prediction Only

This is the active Railway runtime.

It has one purpose:

```text
Predict whether BTC will move UP or DOWN in the current Polymarket 5-minute market.
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
read-only dashboard and status API
```

It does not start:

```text
general event market scanner
Paper order workers
position simulation
trade replay
trade settlement
live orders
wallet signing
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
YES edge = fused probability up - YES ask
NO edge  = (1 - fused probability up) - NO ask
```

A directional signal is returned only when:

```text
confidence >= MIN_CONFIDENCE
selected edge >= MIN_EDGE
```

Otherwise the output is:

```text
WAIT
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
source health
```

## Read-only API

```text
/status
/healthz
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

## Railway Variables

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

Live trading variables are not used by this runtime.

## Safety boundary

This deployment is prediction-only. It does not place Paper or live orders.
