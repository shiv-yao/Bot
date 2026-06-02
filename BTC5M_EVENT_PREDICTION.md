# BTC 5m Event Prediction + Guarded Scale In

This is the active Railway BTC five-minute prediction-market Paper runtime.

It has one purpose:

```text
Predict whether BTC will move UP or DOWN in the current Polymarket 5-minute market
and simulate a capped three-stage scale-in position with quality gates.
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
Paper-only guarded BTC 5m scale-in engine
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
WAIT = one or more quality gates rejected the Paper entry
```

## Guarded scale-in strategy

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

Default confidence and net-edge requirements become stricter for later entries:

```text
Stage 1: confidence >= 58%, net edge >= 0.8%
Stage 2: confidence >= 62%, net edge >= 1.2%
Stage 3: confidence >= 66%, net edge >= 1.8%
```

Every stage revalidates:

```text
signal freshness
order-book freshness
same direction as the first entry
contract price range
bid-ask spread
order-book depth
estimated VWAP
net edge after slippage buffer
```

Stage 2 and Stage 3 are rejected when the prediction direction changes after the first entry. The engine does not create unlimited micro-orders and does not average down blindly.

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
current BTC 5-minute market
YES / NO asks
market book age
Paper portfolio
scale-in entries
latest signal quality
signal source
signal age
book age
spread
edge and net edge
order-book depth
rejection counters
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

The `/healthz` endpoint reports healthy only when the current market is ready and enough external price sources are connected.

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
BTC5M_PAPER_SCALE_IN_MIN_CONFIDENCE=0.58,0.62,0.66
BTC5M_PAPER_SCALE_IN_MIN_NET_EDGE=0.008,0.012,0.018
BTC5M_PAPER_CLOSE_BUFFER_SEC=15
BTC5M_PAPER_MIN_CONFIDENCE=0.58
BTC5M_PAPER_MIN_PROBABILITY_MARGIN=0.015
BTC5M_PAPER_MAX_SIGNAL_AGE_MS=1200
BTC5M_PAPER_MAX_BOOK_AGE_MS=2500
BTC5M_PAPER_MIN_CONTRACT_PRICE=0.10
BTC5M_PAPER_MAX_CONTRACT_PRICE=0.90
BTC5M_PAPER_MAX_SPREAD=0.04
BTC5M_PAPER_SLIPPAGE_BUFFER=0.003
BTC5M_PAPER_REQUIRE_BOOK_DEPTH=true
BTC5M_PAPER_MIN_DEPTH_MULTIPLE=1.50
BTC5M_PAPER_LOOP_INTERVAL_MS=200
```

Live-trading variables are not used by this runtime.

## Safety boundary

This deployment remains Paper-only. It simulates entries and settlement for evaluation, but it does not place live Polymarket orders, sign wallets or move funds.
