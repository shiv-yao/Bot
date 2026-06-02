from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

import uvicorn

from .ai_api import register_ai_routes
from .api import create_app
from .config import Settings
from .dashboard5m import register_dashboard5m
from .history_api import register_history_routes
from .logging_utils import log_event, setup_logging
from .measured_feeds import MeasuredFeedHub
from .models import now_ms
from .monitoring import register_monitoring_routes
from .multi_source import MultiSourceFusion, binance_ws_loop, coinbase_ws_loop
from .ops_api import register_ops_routes
from .paper_metrics import MeasuredPaperExecutor
from .paper_portfolio import PaperPortfolio
from .risk import RiskManager
from .rtds_chainlink import chainlink_rtds_loop
from .runtime_profile import apply_balanced_btc5m_paper_profile
from .state import BotState
from .strategy import LatencyStrategy
from .watchdog import RuntimeWatchdog
from .watchdog_api import register_watchdog_routes


async def run() -> None:
    settings = Settings()
    apply_balanced_btc5m_paper_profile(settings)
    setup_logging(settings.log_level)
    logger = logging.getLogger("main")
    state = BotState()
    risk = RiskManager(settings)
    portfolio = PaperPortfolio(settings, state, risk, logging.getLogger("paper_portfolio"))
    strategy = LatencyStrategy(settings, state)
    executor = MeasuredPaperExecutor(settings, state, risk, portfolio)
    watchdog = RuntimeWatchdog(settings, state, risk, portfolio)

    async def evaluate() -> None:
        started_ms = now_ms()
        await state.record_event("strategy_evaluation")
        intents = await strategy.build_intents()
        await state.record_latency("strategy_ms", now_ms() - started_ms)
        if intents:
            await state.increment_counter("strategy_intents", len(intents))
            for _ in intents:
                await state.record_event("strategy_intent")
        for intent in intents:
            await executor.submit(intent)

    feeds = MeasuredFeedHub(settings, state, evaluate)
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
        asyncio.create_task(watchdog.loop(), name="runtime-watchdog"),
    ]
    tasks += [
        asyncio.create_task(executor.worker(i), name=f"executor-{i}")
        for i in range(settings.execution_workers)
    ]

    if settings.enable_api:
        app = create_app(settings, state, feeds, risk)
        register_dashboard5m(app)
        register_monitoring_routes(app, settings, state, risk, portfolio)
        register_ops_routes(app, settings, state, feeds, risk, portfolio)
        register_watchdog_routes(app, watchdog)
        register_ai_routes(app, settings, state)
        register_history_routes(app, settings, portfolio)
        server = uvicorn.Server(
            uvicorn.Config(app, host=settings.host, port=settings.port, log_level="warning")
        )
        tasks.append(asyncio.create_task(server.serve(), name="api"))

    log_event(
        logger,
        "bot_started",
        mode="paper",
        market_slug_prefix=settings.market_slug_prefix,
        market_interval_sec=settings.market_interval_sec,
        ai_mode="single_direction_yes_no",
        paper_profile="balanced_btc5m_hf",
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
