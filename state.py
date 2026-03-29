import json
import threading
from collections import deque


class EngineState:
    def __init__(self):
        self._lock = threading.Lock()

        self.running = True
        self.mode = "PAPER"

        self.sol_balance = 30.0
        self.capital = 30.0

        self.last_signal = ""
        self.last_trade = ""

        self.positions = []
        self.trade_history = []

        self.logs = deque(maxlen=500)

        self.stats = {
            "signals": 0,
            "buys": 0,
            "sells": 0,
            "errors": 0,
            "adds": 0,
        }

        self.engine_stats = {
            "stable": {"pnl": 0.0, "trades": 0, "wins": 0},
            "degen": {"pnl": 0.0, "trades": 0, "wins": 0},
            "sniper": {"pnl": 0.0, "trades": 0, "wins": 0},
        }

        self.engine_allocator = {
            "stable": 0.4,
            "degen": 0.4,
            "sniper": 0.2,
        }

        self.candidate_count = 0

        self.bot_ok = True
        self.bot_error = ""

    def log(self, message: str):
        with self._lock:
            self.logs.append(str(message))

    def set_error(self, message: str):
        with self._lock:
            self.bot_ok = False
            self.bot_error = str(message)
            self.logs.append(f"ERROR: {message}")

    def clear_error(self):
        with self._lock:
            self.bot_ok = True
            self.bot_error = ""

    def snapshot(self):
        with self._lock:
            return {
                "running": self.running,
                "mode": self.mode,
                "sol_balance": self.sol_balance,
                "capital": self.capital,
                "last_signal": self.last_signal,
                "last_trade": self.last_trade,
                "positions": list(self.positions),
                "logs": list(self.logs),
                "stats": dict(self.stats),
                "trade_history": list(self.trade_history[-200:]),
                "bot_ok": self.bot_ok,
                "bot_error": self.bot_error,
                "engine_stats": dict(self.engine_stats),
                "engine_allocator": dict(self.engine_allocator),
                "candidate_count": self.candidate_count,
            }

    def to_json(self):
        return json.dumps(self.snapshot(), ensure_ascii=False)


engine = EngineState()
