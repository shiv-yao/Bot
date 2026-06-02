# BTC 5m Balanced HF Runtime Snapshots

The Paper bot writes one read-only runtime snapshot every 60 seconds.

Snapshots are stored in the same durable Railway Volume database used by the isolated BTC 5-minute Paper profile:

```text
/data/polymarket_paper_btc5m_balanced.db
```

The snapshot recorder creates a separate SQLite table:

```text
paper_runtime_snapshots
```

It does not modify Paper trades, positions, PnL, risk checks, or order execution.

## Read-only endpoints

### Profile status

```text
/profile/status
```

Returns:

- profile name
- profile version
- active database path
- snapshot interval

### Snapshot status

```text
/snapshots/status
```

Returns:

- snapshot count
- last snapshot timestamp
- active database path
- snapshot interval

### Recent snapshots

```text
/snapshots/recent?limit=120
```

Returns the newest snapshots first.

## Captured fields

Each snapshot includes:

- profile name and version
- BTC 5-minute market slug
- current AI direction: `BUY_YES`, `BUY_NO`, or `WAIT`
- AI decision, probability, confidence and rejection reason
- realized and unrealized Paper PnL
- risk state and halt reason
- queue depth and queue high-water mark
- rolling throughput metrics
- latency percentiles
- WebSocket connection state
- fusion state

## Verification

After deployment, open:

```text
https://bot-production-38e3.up.railway.app/profile/status
https://bot-production-38e3.up.railway.app/snapshots/status
https://bot-production-38e3.up.railway.app/snapshots/recent?limit=5
```

Expected profile:

```text
balanced_btc5m_hf
```

Expected profile version:

```text
2026-06-02.1
```

Expected database:

```text
/data/polymarket_paper_btc5m_balanced.db
```

Wait at least 60 seconds between checks. `snapshot_count` should increase over time.

## Failure behavior

Snapshot failures are isolated from the Paper trading loop. A snapshot write failure does not stop market discovery, data feeds, strategy evaluation, or Paper execution.
