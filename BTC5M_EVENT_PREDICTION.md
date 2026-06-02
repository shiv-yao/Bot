# BTC 5m Event Prediction — V4 Hardened Scale In

This is the active Railway BTC five-minute prediction-market Paper runtime.

It predicts whether BTC will move UP or DOWN in the current Polymarket five-minute market and simulates a capped three-stage scale-in position:

```text
Stage 1: 50%
Stage 2: 30%
Stage 3: 20%
```

The Railway and Docker entrypoint is:

```text
python -m polymarket_latency_bot.btc5m_event_main_v4
```

## Active strategy

```text
BTC_5M_EVENT_SCALE_IN_V4_HARDENED
```

The runtime remains Paper-only. It does not place live orders, sign wallets or move funds.

## Prediction output

The dashboard and `/status` endpoint return:

```text
YES
NO
WAIT
```

`WAIT` means at least one data-quality, liquidity, fusion, regime or scale-in quality gate rejected the Paper entry.

## Core runtime

```text
BTC five-minute market discovery
Polymarket market WebSocket
Chainlink BTC/USD RTDS
Binance BTC/USDT WebSocket
Coinbase BTC/USD WebSocket
safe multi-source fusion with outlier isolation
short-horizon BTC regime filter
V4 Hardened Paper scale-in engine
EV, calibration, drift and walk-forward analytics
Shadow A/B observational profiles
read-only V4 mobile dashboard and API
```

## V4 Hardened quality gates

Every entry revalidates:

```text
signal freshness
order-book freshness
best bid and best ask presence
valid spread
contract price range
order-book depth
estimated VWAP
net edge after slippage buffer
order-book imbalance
BTC open / close sample quality
```

Later scale-in stages also require:

```text
persistent direction confirmation
maximum direction-flip count
minimum clean-source count
multi-source fusion readiness
maximum price worsening
maximum net-edge decay
```

## Stage defaults

```text
Stage 1: 50%, after 0 seconds
Stage 2: 30%, after 100 seconds
Stage 3: 20%, after 200 seconds
```

```text
Stage 1: confirmation 1 sample, clean sources 0, fusion optional
Stage 2: confirmation 3 samples, clean sources 2, fusion required
Stage 3: confirmation 5 samples, clean sources 3, fusion required
```

Stage 1 may use a fresh RTDS fallback signal. Stages 2 and 3 remain strict.

## BTC open / close data quality

BTC open and settlement samples must arrive within the configured delay window. Invalid samples are excluded from win rate, PnL and EV analytics.

```text
invalid_btc_open_missing
invalid_btc_open_delayed
invalid_btc_close_missing
invalid_btc_close_delayed
```

Defaults:

```env
BTC5M_PAPER_OPEN_PRICE_MAX_DELAY_MS=2000
BTC5M_PAPER_SETTLEMENT_MAX_DELAY_MS=2000
```

## Multi-source fusion

Chainlink, Binance and Coinbase prices are compared against the cross-source median.

```text
normal source -> included
outlier source -> excluded
not enough clean sources -> WAIT
clean sources disagree too much -> WAIT
websocket disconnect -> stale source removed immediately
```

Defaults:

```env
FUSION_MIN_SOURCES=2
FUSION_OUTLIER_MAX_DEVIATION_BPS=35
FUSION_MAX_DISPERSION_BPS=20
```

## Adaptive Cooldown

Adaptive Cooldown is intentionally disabled.

```env
BTC5M_PAPER_ADAPTIVE_COOLDOWN_ENABLED=false
```

Loss streaks remain visible in analytics, but they do not pause new Paper evaluations. Auto-tuning also remains disabled.

## Shadow A/B

Shadow A/B is observational only. It never creates extra Paper positions.

```text
baseline
conservative
strict
```

The dashboard and analytics report each profile's settled orders, win rate, realized PnL and realized EV.

## Analytics

Open:

```text
/paper/analytics
```

The endpoint reports:

```text
overall Paper win rate
win rate by stage, net-edge bucket, direction and signal source
Brier Score and probability calibration
rolling performance
performance drift
walk-forward validation
realized EV
expected EV
EV calibration gap
Profit Factor
average win and average loss
maximum drawdown
invalid BTC data counts
Shadow A/B profiles
```

The analytics layer is observational only. It does not automatically tighten thresholds, pause the strategy or change position size.

## Mobile dashboard

Open:

```text
/
```

The V4 dashboard shows:

```text
AI direction
countdown
Paper win rate
realized EV
Profit Factor
maximum drawdown
signal age
book age
spread
net edge
clean sources
fusion readiness
book imbalance
top-3 imbalance
stage confirmation
price worsening
edge decay
invalid BTC data
validity rate
Shadow A/B profiles
Paper-only safety state
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

## Railway variables

Use the dedicated V4 fragment:

```text
env/btc5m_v4_hardened.env.example
```

Recommended defaults:

```env
BTC5M_PAPER_ADAPTIVE_COOLDOWN_ENABLED=false
BTC5M_PAPER_OPEN_PRICE_MAX_DELAY_MS=2000
BTC5M_PAPER_SETTLEMENT_MAX_DELAY_MS=2000
BTC5M_PAPER_STAGE_CONFIRM_SAMPLES=1,3,5
BTC5M_PAPER_STAGE_CONFIRM_WINDOW_SEC=0,8,15
BTC5M_PAPER_STAGE_MAX_DIRECTION_FLIPS=0,0,0
BTC5M_PAPER_SCALE_IN_MAX_PRICE_WORSENING=0,0.025,0.015
BTC5M_PAPER_SCALE_IN_MAX_EDGE_DECAY=0,0.004,0.002
BTC5M_PAPER_STAGE_MIN_CLEAN_SOURCES=0,2,3
BTC5M_PAPER_STAGE_REQUIRE_FUSION=false,true,true
BTC5M_PAPER_STAGE_MIN_BOOK_IMBALANCE=0.20,0.30,0.35
BTC5M_PAPER_SHADOW_AB_ENABLED=true
```

## Safety boundary

```text
Paper predictions: ON
Paper positions: ON
50 / 30 / 20 scale-in: ON
Adaptive Cooldown: OFF
Auto-tuning: OFF
Live orders: OFF
Wallet signing: OFF
Live trading: OFF
```
