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

    auto_discover_market: bool = True
    market_slug_prefix: str = "btc-updown-15m-"
    market_interval_sec: int = Field(default=900, ge=60)
    market_discovery_refresh_sec: float = Field(default=10.0, gt=0)
    market_discovery_timeout_sec: float = Field(default=3.0, gt=0)

    live_trading: bool = False
    live_confirmation: str = ""
    account_equity_usd: float = Field(default=1000.0, gt=0)
    max_order_equity_fraction: float = Field(default=0.005, gt=0, le=0.005)
    max_daily_loss_fraction: float = Field(default=0.02, gt=0, le=0.02)
    max_open_notional_usd: float = Field(default=50.0, gt=0)
    min_edge: float = Field(default=0.003, gt=0)
    min_confidence: float = Field(default=0.55, ge=0, le=1)
    signal_cooldown_ms: int = Field(default=250, ge=0)
    max_signal_age_ms: int = Field(default=1000, gt=0)
    order_timeout_ms: int = Field(default=1500, gt=0)
    max_queue_size: int = Field(default=10000, gt=0)
    execution_workers: int = Field(default=8, gt=0, le=128)
    order_rate_per_sec: float = Field(default=70.0, gt=0, le=80)
    order_burst: int = Field(default=350, gt=0, le=500)

    # Paper portfolio simulation
    paper_hold_sec: int = Field(default=30, ge=5, le=900)
    paper_max_open_positions: int = Field(default=4, ge=1, le=50)
    paper_mark_interval_sec: float = Field(default=1.0, gt=0)

    market_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    user_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
    rtds_ws_url: str = "wss://ws-live-data.polymarket.com"
    enable_rtds_momentum_prediction: bool = True
    rtds_prediction_window_sec: int = Field(default=60, ge=10)

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

    @model_validator(mode="after")
    def validate_live(self) -> "Settings":
        if self.live_trading and self.live_confirmation != "I_UNDERSTAND_LIVE_ORDERS":
            raise ValueError("LIVE_TRADING requires LIVE_CONFIRMATION=I_UNDERSTAND_LIVE_ORDERS")
        if self.live_enabled:
            missing = [
                key for key, value in {
                    "PK": self.pk,
                    "FUNDER_ADDRESS": self.funder_address,
                }.items() if not value
            ]
            if not self.auto_discover_market:
                missing.extend(
                    key for key, value in {
                        "YES_TOKEN_ID": self.yes_token_id,
                        "NO_TOKEN_ID": self.no_token_id,
                    }.items() if not value
                )
            if missing:
                raise ValueError(f"Live mode missing required settings: {', '.join(missing)}")
        return self
