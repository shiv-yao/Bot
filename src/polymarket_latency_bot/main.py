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
from .monitoring import register_monitoring_routes
from .multi_source import MultiSourceFusion, binance_ws_loop, coinbase_ws_loop
from .paper_portfolio import PaperPortfolio
from .risk import RiskManager
from .rtds_chainlink import chainlink_rtds_loop
from .state import BotState
from .strategy import LatencyStrategy


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
    fusion = MultiSourceFusion(settings, state, feeds)

    tasks: list[asyncio.Task[object]] = [
        asyncio.create_task(feeds.market_discovery_loop(), name="market-discovery"),
        asyncio.create_task(feeds.market_ws_loop(), name="market-ws"),
        asyncio.create_task(chainlink_rtds_loop(settings, state, feeds, fusion), name="rtds-chainlink"),
        asyncio.create_task(binance_ws_loop(settings, state, fusion), name="binance-ws"),
        asyncio.create_task(coinbase_ws_loop(settings, state, fusion), name="coinbase-ws"),
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
        register_monitoring_routes(app, settings, state, risk, portfolio)
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
        binance_ws=settings.enable_binance_ws,
        coinbase_ws=settings.enable_coinbase_ws,
        multi_source_fusion=settings.enable_multi_source_fusion,
        depth_levels=settings.depth_levels,
        min_depth_multiple=settings.min_depth_multiple,
        max_slippage=settings.max_slippage,
        slippage_buffer=settings.slippage_buffer,
        paper_db_path=portfolio.store.db_path,
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
