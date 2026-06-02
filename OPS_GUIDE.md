# Polymarket BTC 5m Balanced HF Paper Bot Ops Guide

## Runtime mode

The current application runs `MeasuredPaperExecutor` only. It does not send live orders.

The active Paper profile is:

```text
balanced_btc5m_hf
```

It forces BTC Up / Down 5-minute market discovery, AI single-direction YES / NO decisions, balanced high-frequency Paper sampling, and an isolated SQLite history file.

## Railway Volume

Attach a Railway Volume at:

```text
/data
```

The active 5-minute history file is:

```text
/data/polymarket_paper_btc5m_balanced.db
```

The legacy file remains preserved for comparison:

```text
/data/polymarket_paper.db
```

## Railway Variables

Use the repository template:

```text
.env.btc5m-balanced.example
```

Important values:

```env
LIVE_TRADING=false
AUTO_DISCOVER_MARKET=true
FORCE_BTC_5M_MARKET=true
MARKET_INTERVAL_SEC=300
PAPER_HIGH_FREQUENCY_PROFILE=true
PAPER_DB_PATH=/data/polymarket_paper_btc5m_balanced.db
PAPER_MAX_TRADES_PER_MARKET=0
PAPER_MAX_OPEN_POSITIONS=0
PAPER_DISABLE_ORDER_RATE_LIMIT=true
WEBHOOK_SECRET=replace-with-a-random-secret-at-least-16-characters
```

## Mobile dashboard

Open:

```text
/dashboard5m
```

The root page `/` redirects to this dashboard.

The page shows:

- BTC live price
- AI decision: `BUY_YES`, `BUY_NO`, or `WAIT`
- realized PnL and win rate for the isolated BTC 5-minute history series
- current market slug and question
- balanced high-frequency profile parameters
- whether the isolated database is active

## Evaluation dashboard

Open:

```text
/evaluation
```

This page compares the new isolated BTC 5-minute Balanced HF history with the preserved legacy history. The databases are shown separately and are never merged.

The sample stage is also available as JSON:

```text
/evaluation/status
```

Stages:

- `collecting_initial_sample`: fewer than 100 closed trades
- `early_evaluation`: 100 to 499 closed trades
- `evaluation_ready`: 500 or more closed trades

The separated performance comparison is available at:

```text
/performance/compare
```

## Read-only pages

- `/dashboard5m` BTC 5-minute mobile dashboard
- `/evaluation` BTC 5-minute evaluation dashboard
- `/evaluation/status` isolated sample stage and recommendation
- `/performance/compare` new BTC 5-minute series versus preserved legacy history
- `/ai/status` current AI YES / NO decision
- `/risk/profile` effective Paper high-frequency risk profile
- `/history/status` active isolated database and legacy database status
- `/monitor` market feed, fusion, order book, VWAP and rejection dashboard
- `/ops` latency, performance, risk and security dashboard
- `/diagnostics` consolidated runtime warnings
- `/startup-check` startup diagnostics
- `/latency` bounded latency statistics, throughput and queue high-water mark
- `/performance` SQLite-backed Paper performance statistics
- `/security/status` mode, secret configuration state and database path
- `/risk/status` current risk state
- `/debug/sources` source connection diagnostics
- `/debug/rejections` strategy and Paper rejection counts
- `/watchdog` watchdog status
- `/alerts` recent watchdog alerts
- `/metrics/runtime` runtime JSON metrics
- `/metrics/prometheus` Prometheus text metrics
- `/healthz` external health endpoint

## Protected write APIs

Write APIs require the `X-Webhook-Secret` header. They remain disabled while `WEBHOOK_SECRET` is blank or still set to the default value.

### Halt Paper entries

```bash
curl -X POST "$BASE_URL/risk/halt" \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: $WEBHOOK_SECRET" \
  -d '{"reason":"manual_test"}'
```

### Resume Paper entries

```bash
curl -X POST "$BASE_URL/risk/resume" \
  -H "X-Webhook-Secret: $WEBHOOK_SECRET"
```

### TradingView prediction adapter

```bash
curl -X POST "$BASE_URL/feeds/tradingview" \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: $WEBHOOK_SECRET" \
  -d '{"probability_up":0.58,"confidence":0.72}'
```

### CryptoQuant prediction adapter

```bash
curl -X POST "$BASE_URL/feeds/cryptoquant" \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: $WEBHOOK_SECRET" \
  -d '{"probability_up":0.54,"confidence":0.68}'
```

### Dry-run Paper load test

This endpoint does not create orders or positions. It only measures async task throughput.

```bash
curl -X POST "$BASE_URL/loadtest/pipeline" \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: $WEBHOOK_SECRET" \
  -d '{"operations":1000,"concurrency":100}'
```

## Performance fields

`/performance` returns:

- closed trades
- gross profit
- gross loss
- profit factor
- net PnL
- average trade PnL
- average hold time
- maximum drawdown
- win rate

## Latency fields

`/latency` includes bounded in-memory percentile statistics for:

- strategy evaluation
- queue wait
- Paper execution
- signal-to-result latency
- dry-run pipeline timing
