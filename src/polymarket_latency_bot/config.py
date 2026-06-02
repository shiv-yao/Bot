from __future__ import annotations

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"

    clob_host: str = "https://clob.polymarket.com"
    gamma_api_url: str = "https://gamma-api.polymarket.com"
    chain_id: int = 137
    pk: str = ""
    clob_api_key: str = ""
    clob_secret: str = ""
    clob_pass_phrase: str = ""
    signature_type: int = 0
    funder_address: str = ""
    yes_token_id: str = ""
    no_token_id: str = ""
    condition_id: str = ""
    tick_size: str = "0.01"
    neg_risk: bool = False

    # BTC Up/Down 5-minute market discovery.
    auto_discover_market: bool = True
    force_btc_5m_market: bool = True
    market_slug_prefix: str = "btc-updown-5m-"
    market_interval_sec: int = Field(default=300, ge=60)
    market_discovery_refresh_sec: float = Field(default=3.0, gt=0)
    market_discovery_timeout_sec: float = Field(default=3.0, gt=0)

    live_trading: bool = False
    live_confirmation: str = ""
    account_equity_usd: float = Field(default=1000.0, gt=0)

    # Conservative baseline limits. Live mode always uses these values.
    max_order_equity_fraction: float = Field(default=0.005, gt=0, le=0.005)
    max_daily_loss_fraction: float = Field(default=0.005, gt=0, le=0.02)
    max_open_notional_usd: float = Field(default=10.0, gt=0)

    # Paper-only high-frequency profile. It never applies to Live mode.
    paper_high_frequency_profile: bool = True
    paper_hf_max_order_equity_fraction: float = Field(default=0.0025, gt=0, le=0.005)
    paper_hf_max_daily_loss_fraction: float = Field(default=0.02, gt=0, le=0.02)
    paper_hf_max_open_notional_usd: float = Field(default=100.0, gt=0)

    # AI YES/NO decision filters tuned for faster Paper sampling.
    min_edge: float = Field(default=0.012, gt=0)
    min_net_edge: float = Field(default=0.004, ge=0)
    min_confidence: float = Field(default=0.52, ge=0, le=1)
    ai_min_probability_margin: float = Field(default=0.003, ge=0, le=0.49)
    min_contract_price: float = Field(default=0.10, ge=0, le=1)
    max_contract_price: float = Field(default=0.90, ge=0, le=1)
    max_spread: float = Field(default=0.06, ge=0, le=1)
    signal_cooldown_ms: int = Field(default=250, ge=0)
    max_signal_age_ms: int = Field(default=1200, gt=0)
    strategy_evaluation_interval_ms: int = Field(default=50, ge=25, le=5000)
    prefer_fusion_prediction: bool = True

    # Paper order book depth and execution simulation.
    depth_levels: int = Field(default=5, ge=1, le=20)
    min_depth_multiple: float = Field(default=1.25, ge=1.0, le=20.0)
    max_slippage: float = Field(default=0.03, ge=0, le=0.20)
    slippage_buffer: float = Field(default=0.002, ge=0, le=0.20)

    order_timeout_ms: int = Field(default=1500, gt=0)
    max_queue_size: int = Field(default=5000, gt=0)
    execution_workers: int = Field(default=8, gt=0, le=128)
    order_rate_per_sec: float = Field(default=10.0, gt=0, le=80)
    order_burst: int = Field(default=20, gt=0, le=500)
    paper_disable_order_rate_limit: bool = True

    # Hardened Paper portfolio simulation.
    paper_hold_sec: int = Field(default=20, ge=5, le=900)
    paper_max_open_positions: int = Field(default=0, ge=0, le=100000)
    paper_mark_interval_sec: float = Field(default=0.25, gt=0)
    paper_take_profit_pct: float = Field(default=0.025, gt=0, le=1)
    paper_stop_loss_pct: float = Field(default=0.02, gt=0, le=1)
    paper_trailing_stop_pct: float = Field(default=0.015, gt=0, le=1)
    paper_open_buffer_sec: int = Field(default=2, ge=0, le=600)
    paper_close_buffer_sec: int = Field(default=10, ge=0, le=600)
    paper_max_trades_per_market: int = Field(default=0, ge=0, le=100000)
    paper_max_consecutive_losses_per_market: int = Field(default=8, ge=1, le=20)

    paper_db_path: str = "/data/polymarket_paper.db"
    recent_trade_limit: int = Field(default=100, ge=10, le=1000)

    market_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    user_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
    rtds_ws_url: str = "wss://ws-live-data.polymarket.com"
    enable_rtds_momentum_prediction: bool = True
    rtds_prediction_window_sec: int = Field(default=20, ge=10)

    enable_binance_ws: bool = True
    enable_coinbase_ws: bool = True
    binance_ws_url: str = "wss://stream.binance.com:9443/ws/btcusdt@trade"
    binance_ws_fallback_urls: str = "wss://stream.binance.com:443/ws/btcusdt@trade,wss://data-stream.binance.vision/ws/btcusdt@trade"
    coinbase_ws_url: str = "wss://ws-feed.exchange.coinbase.com"
    source_reconnect_delay_sec: float = Field(default=1.0, gt=0, le=60)
    source_reconnect_max_delay_sec: float = Field(default=30.0, gt=0, le=300)
    external_price_max_age_ms: int = Field(default=3000, ge=250)
    external_price_window_sec: int = Field(default=20, ge=10, le=900)

    enable_multi_source_fusion: bool = True
    fusion_min_sources: int = Field(default=2, ge=1, le=8)
    fusion_agreement_threshold: float = Field(default=0.55, ge=0.5, le=1.0)
    fusion_probability_scale: float = Field(default=40.0, gt=0, le=500)
    fusion_base_confidence: float = Field(default=0.52, ge=0, le=1)
    fusion_source_weight_chainlink: float = Field(default=1.0, ge=0)
    fusion_source_weight_binance: float = Field(default=1.0, ge=0)
    fusion_source_weight_coinbase: float = Field(default=1.0, ge=0)
    fusion_outlier_max_deviation_bps: float = Field(default=35.0, ge=0, le=10000)
    fusion_max_dispersion_bps: float = Field(default=20.0, ge=0, le=10000)

    external_poll_url: str = ""
    external_poll_api_key: str = ""
    external_poll_source: str = "cryptoquant"
    external_poll_interval_sec: float = Field(default=5.0, gt=0)
    external_probability_json_path: str = "probability_up"
    external_confidence_json_path: str = "confidence"
    webhook_secret: str = "change-me"
    enable_api: bool = True

    @property
    def live_enabled(self) -> bool:
        return self.live_trading and self.live_confirmation == "I_UNDERSTAND_LIVE_ORDERS"

    @property
    def effective_max_order_equity_fraction(self) -> float:
        if self.paper_high_frequency_profile and not self.live_enabled:
            return self.paper_hf_max_order_equity_fraction
        return self.max_order_equity_fraction

    @property
    def effective_max_daily_loss_fraction(self) -> float:
        if self.paper_high_frequency_profile and not self.live_enabled:
            return self.paper_hf_max_daily_loss_fraction
        return self.max_daily_loss_fraction

    @property
    def effective_max_open_notional_usd(self) -> float:
        if self.paper_high_frequency_profile and not self.live_enabled:
            return self.paper_hf_max_open_notional_usd
        return self.max_open_notional_usd

    @model_validator(mode="after")
    def validate_live(self) -> "Settings":
        if self.force_btc_5m_market:
            self.market_slug_prefix = "btc-updown-5m-"
            self.market_interval_sec = 300
        if self.max_contract_price <= self.min_contract_price:
            raise ValueError("MAX_CONTRACT_PRICE must be greater than MIN_CONTRACT_PRICE")
        if self.paper_open_buffer_sec + self.paper_close_buffer_sec >= self.market_interval_sec:
            raise ValueError("paper market buffers must be shorter than MARKET_INTERVAL_SEC")
        if self.source_reconnect_max_delay_sec < self.source_reconnect_delay_sec:
            raise ValueError("SOURCE_RECONNECT_MAX_DELAY_SEC must be >= SOURCE_RECONNECT_DELAY_SEC")
        if self.live_trading and self.live_confirmation != "I_UNDERSTAND_LIVE_ORDERS":
            raise ValueError("LIVE_TRADING requires LIVE_CONFIRMATION=I_UNDERSTAND_LIVE_ORDERS")
        if self.live_enabled:
            missing = [key for key, value in {"PK": self.pk, "FUNDER_ADDRESS": self.funder_address}.items() if not value]
            if not self.auto_discover_market:
                missing.extend(key for key, value in {"YES_TOKEN_ID": self.yes_token_id, "NO_TOKEN_ID": self.no_token_id}.items() if not value)
            if missing:
                raise ValueError(f"Live mode missing required settings: {', '.join(missing)}")
        return self
