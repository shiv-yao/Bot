import asyncio

from polymarket_latency_bot.config import Settings
from polymarket_latency_bot.models import BookTop, Prediction, now_ms
from polymarket_latency_bot.state import BotState
from polymarket_latency_bot.strategy import LatencyStrategy


def test_build_buy_yes_intent():
    async def scenario():
        settings = Settings(
            yes_token_id="yes",
            no_token_id="no",
            min_edge=0.003,
            min_confidence=0.5,
        )
        state = BotState()
        t = now_ms()
        state.books["yes"] = BookTop("yes", best_bid=0.49, best_ask=0.50, timestamp_ms=t)
        state.books["no"] = BookTop("no", best_bid=0.49, best_ask=0.51, timestamp_ms=t)
        state.predictions["a"] = Prediction("a", probability_up=0.55, confidence=0.8, timestamp_ms=t)
        strategy = LatencyStrategy(settings, state)
        intents = await strategy.build_intents()
        assert intents
        assert intents[0].token_id == "yes"
    asyncio.run(scenario())
