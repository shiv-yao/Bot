import json
import threading
from collections import deque

class EngineState:
    def __init__(self):
        self._lock = threading.Lock()

        self.running = True
        self.mode = "PAPER"

        self.sol_balance = 1.0
        self.capital = 1.0

        self.last_signal = ""
        self.last_trade = ""

        self.positions = []
        self.trade_history = []

        self.logs = deque(maxlen=300)

        self.stats = {
            "signals": 0,
            "buys": 0,
            "sells": 0,
            "errors": 0,
            "adds": 0,
        }

        self.bot_ok = True
        self.bot_error = ""

        self.engine_stats = {}
        self.engine_allocator = {}
        self.candidate_count = 0

    def log(self, message: str):
        with self._lock:
            self.logs.append(str(message))

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
                "trade_history": list(self.trade_history[-100:]),
                "bot_ok": self.bot_ok,
                "bot_error": self.bot_error,
                "engine_stats": self.engine_stats,
                "engine_allocator": self.engine_allocator,
                "candidate_count": self.candidate_count,
            }

engine = EngineState()
