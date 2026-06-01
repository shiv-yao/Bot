from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

import uvicorn

from .api import create_app
from .config import Settings
from .executor import PaperExecutor
from .feeds import FeedHub
from .logging_utils import log_event, setup_logging
from .paper_portfolio import PaperPortfolio
from .risk import RiskManager
from .rtds_chainlink import chainlink_rtds_loop
from .state import BotState
from .strategy import LatencyStrategy


def effective_config(settings: Settings) -> dict[str, object]:
    return {
        "mode": "paper",
        "auto_discover_market": settings.auto_discover_market,
        "account_equity_usd": settings.account_equity_usd,
        "max_order_equity_fraction": settings.max_order_equity_fraction,
        "max_daily_loss_fraction": settings.max_daily_loss_fraction,
        "max_open_notional_usd": settings.max_open_notional_usd,
        "min_edge": settings.min_edge,
        "min_net_edge": settings.min_net_edge,
        "min_confidence": settings.min_confidence,
        "min_contract_price": settings.min_contract_price,
        "max_contract_price": settings.max_contract_price,
        "max_spread": settings.max_spread,
        "signal_cooldown_ms": settings.signal_cooldown_ms,
        "max_signal_age_ms": settings.max_signal_age_ms,
        "execution_workers": settings.execution_workers,
        "order_rate_per_sec": settings.order_rate_per_sec,
        "order_burst": settings.order_burst,
        "paper_hold_sec": settings.paper_hold_sec,
        "paper_max_open_positions": settings.paper_max_open_positions,
        "paper_mark_interval_sec": settings.paper_mark_interval_sec,
        "rtds_prediction_window_sec": settings.rtds_prediction_window_sec,
    }


async def run() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    logger = logging.getLogger("main")
    state = BotState()
    risk = RiskManager(settings)
    portfolio = PaperPortfolio(settings, state, risk, logging.getLogger("paper_portfolio"))
    strategy = LatencyStrategy(settings, state)
    executor = PaperExecutor(settings, state, risk, portfolio)

    async def evaluate() -> None:
        for intent in await strategy.build_intents():
            await executor.submit(intent)

    feeds = FeedHub(settings, state, evaluate)
    tasks: list[asyncio.Task[object]] = [
        asyncio.create_task(feeds.market_discovery_loop(), name="market-discovery"),
        asyncio.create_task(feeds.market_ws_loop(), name="market-ws"),
        asyncio.create_task(chainlink_rtds_loop(settings, state, feeds), name="rtds-chainlink"),
        asyncio.create_task(feeds.user_ws_loop(), name="user-ws"),
        asyncio.create_task(feeds.external_poll_loop(), name="external-poll"),
        asyncio.create_task(portfolio.mark_loop(), name="paper-portfolio-mark"),
    ]
    tasks += [
        asyncio.create_task(executor.worker(i), name=f"executor-{i}")
        for i in range(settings.execution_workers)
    ]
    if settings.enable_api:
        app = create_app(settings, state, feeds, risk)
        app.add_api_route("/config", lambda: effective_config(settings), methods=["GET"])
        server = uvicorn.Server(
            uvicorn.Config(app, host=settings.host, port=settings.port, log_level="warning")
        )
        tasks.append(asyncio.create_task(server.serve(), name="api"))
    log_event(
        logger,
        "bot_started",
        mode="paper",
        auto_discover_market=settings.auto_discover_market,
        rtds_feed="chainlink_btc_usd",
        paper_hold_sec=settings.paper_hold_sec,
        paper_max_open_positions=settings.paper_max_open_positions,
        min_edge=settings.min_edge,
        min_net_edge=settings.min_net_edge,
        min_confidence=settings.min_confidence,
        min_contract_price=settings.min_contract_price,
        max_contract_price=settings.max_contract_price,
        max_spread=settings.max_spread,
        signal_cooldown_ms=settings.signal_cooldown_ms,
        max_daily_loss_fraction=settings.max_daily_loss_fraction,
    )
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
