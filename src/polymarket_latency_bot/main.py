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
    strategy = LatencyStrategy(settings, state)
    executor = PaperExecutor(settings, state, risk)

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
    ]
    tasks += [
        asyncio.create_task(executor.worker(i), name=f"executor-{i}")
        for i in range(settings.execution_workers)
    ]
    if settings.enable_api:
        app = create_app(settings, state, feeds, risk)
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
