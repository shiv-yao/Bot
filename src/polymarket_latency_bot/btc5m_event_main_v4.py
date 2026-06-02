from __future__ import annotations

from . import btc5m_event_main as legacy
from .btc5m_analytics_v4 import build_paper_analytics as build_v4_paper_analytics


legacy.STRATEGY_NAME = "BTC_5M_EVENT_SCALE_IN_V4_HARDENED"
legacy.MODE_NAME = "btc_5m_prediction_market_paper_scale_in_v4_hardened"
legacy.build_paper_analytics = build_v4_paper_analytics


def main() -> None:
    legacy.main()


if __name__ == "__main__":
    main()
