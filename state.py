import json
import threading
from collections import deque


class EngineState:
    def __init__(self):
        self._lock = threading.Lock()

        self.running = True
        self.mode = "PAPER"

        self.sol_balance = 0.0
        self.capital = 0.0

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
                "trade_history": list(self.trade_history[-100:]),
                "bot_ok": self.bot_ok,
                "bot_error": self.bot_error,
            }

    def to_json(self):
        return json.dumps(self.snapshot(), ensure_ascii=False)


engine = EngineState()
