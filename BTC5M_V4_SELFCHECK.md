# BTC 5m V4 Hardened Selfcheck

Open the read-only selfcheck endpoint:

```text
/selfcheck
```

The endpoint verifies the active Paper runtime manifest without placing orders or changing settings.

Expected top-level result:

```json
{
  "ok": true,
  "strategy": "BTC_5M_EVENT_SCALE_IN_V4_HARDENED",
  "mode": "btc_5m_prediction_market_paper_scale_in_v4_hardened",
  "entrypoint": "python -m polymarket_latency_bot.btc5m_event_main_v4",
  "execution": "hardened_three_stage_scale_in_50_30_20",
  "scale_in_weights": [0.5, 0.3, 0.2]
}
```

## Safety checks

The following values must remain true:

```text
paper_only = true
adaptive_cooldown_off = true
auto_tuning_off = true
live_orders_off = true
wallet_signing_off = true
```

## V4 quality gates

The endpoint reports whether these V4 Hardened controls are present:

```text
persistent stage confirmation
clean sources by stage
fusion required for later scale-in
order-book imbalance guard
price-chasing prevention
net-edge decay prevention
BTC open / close data-quality checks
```

## Analytics manifest

The endpoint also confirms these read-only analytics are enabled:

```text
EV
Profit Factor
Maximum Drawdown
Data Quality
Shadow A/B
Calibration
Drift
Walk-forward validation
```

## Safety boundary

`/selfcheck` is a static, read-only verification manifest. It does not submit Paper entries, place live orders, sign wallets, change thresholds or modify Railway variables.
