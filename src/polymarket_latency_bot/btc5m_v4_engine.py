from __future__ import annotations

from typing import Any

from .btc5m_hardened_round_prediction import BTC5mHardenedRoundPredictionEngine


class BTC5mV4Engine(BTC5mHardenedRoundPredictionEngine):
    """V4 hardened Paper engine. Adaptive cooldown remains disabled."""

    STRATEGY_NAME = "BTC_5M_EVENT_SCALE_IN_V4_HARDENED"

    def __init__(self, settings: Any, state: Any, db_path: str | None = None) -> None:
        super().__init__(settings, state, db_path=db_path)
        self.cooldown_enabled = False
