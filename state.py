import json
import threading
from collections import deque


class EngineState:
    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def _ensure_list(self, x):
        return x if isinstance(x, list) else []

    def _ensure_dict(self, x):
        return x if isinstance(x, dict) else {}

    def _ensure_str(self, x, default=""):
        try:
            return str(x)
        except Exception:
            return default

    def _ensure_float(self, x, default=0.0):
        try:
            return float(x)
        except Exception:
            return default

    def _ensure_int(self, x, default=0):
        try:
            return int(x)
        except Exception:
            return default

    def reset(self):
        with self._lock:
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

    def repair(self):
        with self._lock:
            self.running = bool(getattr(self, "running", True))
            self.mode = self._ensure_str(getattr(self, "mode", "PAPER"), "PAPER")

            self.sol_balance = self._ensure_float(getattr(self, "sol_balance", 0.0), 0.0)
            self.capital = self._ensure_float(getattr(self, "capital", 0.0), 0.0)

            self.last_signal = self._ensure_str(getattr(self, "last_signal", ""))
            self.last_trade = self._ensure_str(getattr(self, "last_trade", ""))

            self.positions = [
                p for p in self._ensure_list(getattr(self, "positions", []))
                if isinstance(p, dict)
            ]
            self.trade_history = self._ensure_list(getattr(self, "trade_history", []))

            current_logs = getattr(self, "logs", deque(maxlen=300))
            if isinstance(current_logs, deque):
                fixed_logs = deque([self._ensure_str(x) for x in list(current_logs)[-300:]], maxlen=300)
            elif isinstance(current_logs, list):
                fixed_logs = deque([self._ensure_str(x) for x in current_logs[-300:]], maxlen=300)
            else:
                fixed_logs = deque(maxlen=300)
            self.logs = fixed_logs

            raw_stats = self._ensure_dict(getattr(self, "stats", {}))
            self.stats = {
                "signals": self._ensure_int(raw_stats.get("signals", 0)),
                "buys": self._ensure_int(raw_stats.get("buys", 0)),
                "sells": self._ensure_int(raw_stats.get("sells", 0)),
                "errors": self._ensure_int(raw_stats.get("errors", 0)),
                "adds": self._ensure_int(raw_stats.get("adds", 0)),
            }

            self.bot_ok = bool(getattr(self, "bot_ok", True))
            self.bot_error = self._ensure_str(getattr(self, "bot_error", ""))

            raw_engine_stats = self._ensure_dict(getattr(self, "engine_stats", {}))
            self.engine_stats = {
                "stable": {
                    "pnl": self._ensure_float(self._ensure_dict(raw_engine_stats.get("stable", {})).get("pnl", 0.0)),
                    "trades": self._ensure_int(self._ensure_dict(raw_engine_stats.get("stable", {})).get("trades", 0)),
                    "wins": self._ensure_int(self._ensure_dict(raw_engine_stats.get("stable", {})).get("wins", 0)),
                },
                "degen": {
                    "pnl": self._ensure_float(self._ensure_dict(raw_engine_stats.get("degen", {})).get("pnl", 0.0)),
                    "trades": self._ensure_int(self._ensure_dict(raw_engine_stats.get("degen", {})).get("trades", 0)),
                    "wins": self._ensure_int(self._ensure_dict(raw_engine_stats.get("degen", {})).get("wins", 0)),
                },
                "sniper": {
                    "pnl": self._ensure_float(self._ensure_dict(raw_engine_stats.get("sniper", {})).get("pnl", 0.0)),
                    "trades": self._ensure_int(self._ensure_dict(raw_engine_stats.get("sniper", {})).get("trades", 0)),
                    "wins": self._ensure_int(self._ensure_dict(raw_engine_stats.get("sniper", {})).get("wins", 0)),
                },
            }

            raw_allocator = self._ensure_dict(getattr(self, "engine_allocator", {}))
            self.engine_allocator = {
                "stable": self._ensure_float(raw_allocator.get("stable", 0.4), 0.4),
                "degen": self._ensure_float(raw_allocator.get("degen", 0.4), 0.4),
                "sniper": self._ensure_float(raw_allocator.get("sniper", 0.2), 0.2),
            }

            self.candidate_count = self._ensure_int(getattr(self, "candidate_count", 0), 0)

    def log(self, message: str):
        with self._lock:
            if not isinstance(self.logs, deque):
                self.logs = deque(maxlen=300)
            self.logs.append(self._ensure_str(message))

    def set_error(self, message: str):
        with self._lock:
            self.bot_ok = False
            self.bot_error = self._ensure_str(message)
            if not isinstance(self.logs, deque):
                self.logs = deque(maxlen=300)
            self.logs.append(f"ERROR: {self.bot_error}")

            if not isinstance(self.stats, dict):
                self.stats = {"signals": 0, "buys": 0, "sells": 0, "errors": 0, "adds": 0}
            self.stats["errors"] = self._ensure_int(self.stats.get("errors", 0)) + 1

    def clear_error(self):
        with self._lock:
            self.bot_ok = True
            self.bot_error = ""

    def snapshot(self):
        self.repair()
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
                "engine_stats": dict(self.engine_stats),
                "engine_allocator": dict(self.engine_allocator),
                "candidate_count": self.candidate_count,
            }

    def to_json(self):
        return json.dumps(self.snapshot(), ensure_ascii=False)


engine = EngineState()
