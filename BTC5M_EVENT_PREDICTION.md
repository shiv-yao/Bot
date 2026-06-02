# BTC 5m Event Prediction + Adaptive Guarded Scale In

This is the active Railway BTC five-minute prediction-market Paper runtime.

It predicts whether BTC will move UP or DOWN in the current Polymarket five-minute market and simulates a capped three-stage scale-in position with quality gates.

```text
Stage 1: 50%
Stage 2: 30%
Stage 3: 20%
```

The Docker entrypoint is:

```text
python -m polymarket_latency_bot.btc5m_event_main
```

## Active runtime

```text
BTC five-minute market discovery
Polymarket market WebSocket
Chainlink BTC/USD RTDS
Binance BTC/USDT WebSocket
Coinbase BTC/USD WebSocket
multi-source fusion with outlier isolation
short-horizon BTC regime filter
Paper-only guarded scale-in engine
adaptive loss-streak cooldown
split performance analytics
read-only mobile dashboard and status API
```

The runtime does not start live orders, wallet signing or real-money execution.

## Strategy version

```text
BTC_5M_EVENT_SCALE_IN_V3_ADAPTIVE_GUARDED
```

## Prediction output

The dashboard and `/status` endpoint return:

```text
YES
NO
WAIT
```

`WAIT` means at least one quality gate or adaptive cooldown blocked the Paper entry.

## Guarded scale-in

Default timing:

```text
Stage 1: after 0 seconds
Stage 2: after 100 seconds
Stage 3: after 200 seconds
```

Default confidence and net-edge thresholds:

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

## Multi-source quality guard

Chainlink, Binance and Coinbase prices are compared against the cross-source median.

```text
normal source -> included
outlier source -> excluded
not enough clean sources -> WAIT
clean sources disagree too much -> WAIT
```

Defaults:

```text
FUSION_MIN_SOURCES=2
FUSION_OUTLIER_MAX_DEVIATION_BPS=35
FUSION_MAX_DISPERSION_BPS=20
```

## Short-horizon regime filter

The runtime tracks a rolling median BTC price series and classifies the current short-horizon market state.

```text
warming_up
trend_ready
regime_too_flat
regime_choppy
regime_too_volatile
```

Only suitable samples continue to publish directional fusion predictions. Noisy or uninformative regimes immediately replace the previous direction with a neutral `WAIT` signal.

Defaults:

```env
FUSION_REGIME_FILTER_ENABLED=true
FUSION_REGIME_WINDOW_SEC=12
FUSION_REGIME_MIN_SAMPLES=5
FUSION_REGIME_MAX_RANGE_BPS=45
FUSION_REGIME_MIN_ABS_MOVE_BPS=1.5
FUSION_REGIME_MAX_FLIP_RATIO=0.60
FUSION_REGIME_MIN_DIRECTION_CONSISTENCY=0.60
```

## Adaptive cooldown

After repeated losing Paper rounds, the runtime pauses new entries for a fixed cooling-off period.

```text
existing Paper rounds continue to settle
new entries pause
historical trades remain unchanged
auto-tuning remains disabled
```

Defaults:

```env
BTC5M_PAPER_ADAPTIVE_COOLDOWN_ENABLED=true
BTC5M_PAPER_COOLDOWN_AFTER_LOSSES=3
BTC5M_PAPER_COOLDOWN_SEC=900
BTC5M_PAPER_ANALYTICS_MIN_SAMPLES=30
```

## Performance analytics

Open:

```text
/paper/analytics
```

The endpoint reports:

```text
overall Paper win rate
sample status
current loss streak
cooldown recommendation
win rate by scale-in stage
win rate by net-edge bucket
win rate by YES / NO direction
win rate by signal source
```

The analytics layer is observational. It does not automatically tighten thresholds from a small sample.

## Mobile dashboard

Open:

```text
/
```

The dashboard shows:

```text
AI direction
probability up
confidence
current market
YES / NO asks
book age
scale-in count
signal age
net edge
order-book depth
BTC regime status
net move
range
flip ratio
direction consistency
adaptive cooldown status
loss streak
cooldown remaining time
Paper win rate
Paper PnL
rejection counters
```

## API

```text
/status
/healthz
/paper/status
/paper/winrate
/paper/analytics
/paper/rounds
/docs
```

`/healthz` is healthy only when the current market is ready, enough sources are connected, enough clean sources remain and fusion status is `ready`.

## Railway variables

Verify:

```env
AUTO_DISCOVER_MARKET=true
FORCE_BTC_5M_MARKET=true
MARKET_SLUG_PREFIX=btc-updown-5m-
MARKET_INTERVAL_SEC=300
ENABLE_BINANCE_WS=true
ENABLE_COINBASE_WS=true
ENABLE_MULTI_SOURCE_FUSION=true

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

SOURCE_RECONNECT_DELAY_SEC=1
SOURCE_RECONNECT_MAX_DELAY_SEC=30
FUSION_MIN_SOURCES=2
FUSION_OUTLIER_MAX_DEVIATION_BPS=35
FUSION_MAX_DISPERSION_BPS=20
FUSION_REGIME_FILTER_ENABLED=true
FUSION_REGIME_WINDOW_SEC=12
FUSION_REGIME_MIN_SAMPLES=5
FUSION_REGIME_MAX_RANGE_BPS=45
FUSION_REGIME_MIN_ABS_MOVE_BPS=1.5
FUSION_REGIME_MAX_FLIP_RATIO=0.60
FUSION_REGIME_MIN_DIRECTION_CONSISTENCY=0.60

BTC5M_PAPER_ADAPTIVE_COOLDOWN_ENABLED=true
BTC5M_PAPER_COOLDOWN_AFTER_LOSSES=3
BTC5M_PAPER_COOLDOWN_SEC=900
BTC5M_PAPER_ANALYTICS_MIN_SAMPLES=30
```

## Safety boundary

This deployment remains Paper-only. It simulates prediction, scale-in, settlement, PnL and cooldown behavior, but it does not place live Polymarket orders, sign wallets or move funds.
