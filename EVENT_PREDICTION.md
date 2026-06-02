# Event Prediction Paper Mode

This mode runs alongside the existing BTC 5-minute Balanced HF Paper strategy.

It is Paper-only. It does not place live orders.

## What it does

The scanner requests active Polymarket events from the Gamma API and extracts eligible binary YES / NO markets.

Discovery request:

```text
GET https://gamma-api.polymarket.com/events?active=true&closed=false&order=volume_24hr&ascending=false&limit=100
```

The first version keeps markets that satisfy:

```text
binary outcomes: YES / NO
active: true
closed: false
minimum liquidity: 1000
minimum 24h volume: 250
```

## Read-only pages

```text
/event-prediction/ui
/event-prediction/status
/event-prediction/markets
/event-prediction/signals
```

## AI forecast input

The prediction adapter accepts an external AI probability and generates a Paper signal.

```bash
curl -X POST "$BASE_URL/event-prediction/prediction" \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: $WEBHOOK_SECRET" \
  -d '{
    "market_slug":"example-market-slug",
    "probability_yes":0.67,
    "confidence":0.78,
    "source":"external_ai",
    "rationale":"Explain the evidence and uncertainty."
  }'
```

## Signal logic

```text
YES edge = AI probability YES - market YES price
NO edge  = (1 - AI probability YES) - market NO price
```

The initial Paper signal rule is:

```text
confidence >= 0.65
selected edge >= 0.05
```

Then:

```text
BUY_YES
BUY_NO
WAIT
```

## Current boundary

This first version discovers markets and produces Paper signals. It does not yet:

```text
automatically fetch news
call an LLM provider
parse resolution rules deeply
place Paper positions
settle resolved event positions
place live orders
```

## Next implementation stage

Choose the first event domain:

```text
politics
macro and central banks
crypto regulation and ETF events
sports
technology and product launches
```

Then connect domain-specific sources and an AI probability model before enabling Paper position simulation.
