from __future__ import annotations

from typing import Any


def apply_balanced_btc5m_paper_profile(settings: Any) -> None:
    """Apply a Paper-only BTC 5m profile without weakening Live safeguards."""
    if settings.live_enabled:
        return

    settings.force_btc_5m_market = True
    settings.market_slug_prefix = "btc-updown-5m-"
    settings.market_interval_sec = 300
    settings.market_discovery_refresh_sec = 3.0

    settings.paper_high_frequency_profile = True
    settings.paper_hf_max_order_equity_fraction = 0.0025
    settings.paper_hf_max_daily_loss_fraction = 0.02
    settings.paper_hf_max_open_notional_usd = 30.0

    settings.min_edge = 0.02
    settings.min_net_edge = 0.008
    settings.min_confidence = 0.56
    settings.ai_min_probability_margin = 0.006
    settings.max_spread = 0.04
    settings.signal_cooldown_ms = 500
    settings.strategy_evaluation_interval_ms = 75
    settings.min_depth_multiple = 1.5
    settings.max_slippage = 0.015
    settings.slippage_buffer = 0.003

    settings.max_queue_size = 2000
    settings.execution_workers = 4
    settings.paper_disable_order_rate_limit = True

    settings.paper_hold_sec = 30
    settings.paper_mark_interval_sec = 0.5
    settings.paper_take_profit_pct = 0.035
    settings.paper_stop_loss_pct = 0.02
    settings.paper_trailing_stop_pct = 0.02
    settings.paper_open_buffer_sec = 3
    settings.paper_close_buffer_sec = 15
    settings.paper_max_trades_per_market = 0
    settings.paper_max_open_positions = 0
    settings.paper_max_consecutive_losses_per_market = 4

    # Keep the old database untouched and start a clean 5m balanced-HF series.
    settings.paper_db_path = "/data/polymarket_paper_btc5m_balanced.db"
