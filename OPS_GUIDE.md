# Polymarket Paper Bot Ops Guide

## Runtime mode

The current application starts `PaperExecutor` only. It does not send live orders.

## Railway Variables to verify

```env
LIVE_TRADING=false
PAPER_DB_PATH=/data/polymarket_paper.db
PAPER_MAX_TRADES_PER_MARKET=0
PAPER_MAX_OPEN_POSITIONS=0
PAPER_DISABLE_ORDER_RATE_LIMIT=true
WEBHOOK_SECRET=replace-with-a-random-secret-at-least-16-characters
```

A Railway Volume should be attached at `/data`.

## Read-only pages

- `/monitor` market feed, fusion, order book, VWAP and rejection dashboard
- `/ops` latency, performance, risk and security dashboard
- `/latency` bounded latency statistics and runtime counters
- `/performance` SQLite-backed Paper performance statistics
- `/security/status` mode, secret configuration state and database path
- `/risk/status` current risk state
- `/debug/sources` source connection diagnostics
- `/debug/rejections` strategy and Paper rejection counts

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
curl -X POST "$BASE_URL/loadtest/paper" \
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

`/latency` currently includes bounded in-memory percentile statistics for strategy evaluation. Additional queue and execution timing instrumentation can be added without enabling live orders.
