import json
import threading
from collections import deque
from itertools import islice
from typing import Any, Iterable


class SliceableDeque(deque):
    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            return list(islice(self, start, stop, step))
        return super().__getitem__(index)

    def to_list(self):
        return list(self)


class EngineState:
    def __init__(self):
        self._lock = threading.Lock()

        self.running = True
        self.mode = "PAPER"

        # ===== 狀態 =====
        self.wallet_ok = False
        self.jup_ok = False
        self.bot_ok = True
        self.bot_error = ""

        self.sol_balance = 30.0
        self.capital = 30.0

        self.last_signal = ""
        self.last_trade = ""

        self.positions = []
        self.trade_history = []

        # 保留 maxlen 功能，但支援 slicing
        self.logs = SliceableDeque(maxlen=500)

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

    # ===== 基本工具 =====
    def _ensure_logs_type(self):
        if not isinstance(self.logs, SliceableDeque):
            old_logs = list(self.logs) if self.logs is not None else []
            self.logs = SliceableDeque(old_logs, maxlen=500)

    def _ensure_list(self, value: Any) -> list:
        if isinstance(value, list):
            return value
        if value is None:
            return []
        if isinstance(value, tuple):
            return list(value)
        if isinstance(value, set):
            return list(value)
        if isinstance(value, deque):
            return list(value)
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [value]
        try:
            return list(value)
        except Exception:
            return []

    def normalize(self):
        with self._lock:
            self.positions = self._ensure_list(self.positions)
            self.trade_history = self._ensure_list(self.trade_history)
            self._ensure_logs_type()

            if not isinstance(self.stats, dict):
                self.stats = {
                    "signals": 0,
                    "buys": 0,
                    "sells": 0,
                    "errors": 0,
                    "adds": 0,
                }

            if not isinstance(self.engine_stats, dict):
                self.engine_stats = {
                    "stable": {"pnl": 0.0, "trades": 0, "wins": 0},
                    "degen": {"pnl": 0.0, "trades": 0, "wins": 0},
                    "sniper": {"pnl": 0.0, "trades": 0, "wins": 0},
                }

            if not isinstance(self.engine_allocator, dict):
                self.engine_allocator = {
                    "stable": 0.4,
                    "degen": 0.4,
                    "sniper": 0.2,
                }

    # ===== 日誌 =====
    def log(self, message: str):
        with self._lock:
            self._ensure_logs_type()
            self.logs.append(str(message))

    def log_many(self, messages: Iterable[str]):
        with self._lock:
            self._ensure_logs_type()
            for message in messages:
                self.logs.append(str(message))

    def clear_logs(self):
        with self._lock:
            self.logs = SliceableDeque(maxlen=500)

    # ===== 錯誤狀態 =====
    def set_error(self, message: str):
        with self._lock:
            self._ensure_logs_type()
            self.bot_ok = False
            self.bot_error = str(message)
            self.logs.append(f"ERROR: {message}")

    def clear_error(self):
        with self._lock:
            self.bot_ok = True
            self.bot_error = ""

    # ===== 狀態重置 =====
    def reset_runtime(self):
        with self._lock:
            self.positions = []
            self.trade_history = []
            self.logs = SliceableDeque(maxlen=500)
            self.stats = {
                "signals": 0,
                "buys": 0,
                "sells": 0,
                "errors": 0,
                "adds": 0,
            }
            self.last_signal = ""
            self.last_trade = ""
            self.bot_ok = True
            self.bot_error = ""
            self.candidate_count = 0

    # ===== 快照 =====
    def snapshot(self):
        with self._lock:
            self._ensure_logs_type()

            return {
                "running": self.running,
                "mode": self.mode,
                "wallet_ok": self.wallet_ok,
                "jup_ok": self.jup_ok,
                "bot_ok": self.bot_ok,
                "bot_error": self.bot_error,
                "sol_balance": self.sol_balance,
                "capital": self.capital,
                "last_signal": self.last_signal,
                "last_trade": self.last_trade,
                "positions": list(self.positions),
                "logs": list(self.logs),
                "stats": dict(self.stats),
                "trade_history": list(self.trade_history[-200:]),
                "engine_stats": dict(self.engine_stats),
                "engine_allocator": dict(self.engine_allocator),
                "candidate_count": self.candidate_count,
            }

    def to_json(self):
        return json.dumps(self.snapshot(), ensure_ascii=False)


engine = EngineState()
