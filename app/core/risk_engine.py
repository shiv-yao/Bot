import time


class RiskEngine:
    def __init__(
        self,
        max_drawdown: float = 0.20,
        max_daily_loss_sol: float = 0.10,
        daily_stop_pct: float = -0.06,
        kill_switch_loss_streak: int = 6,
        max_daily_trades: int = 40,
    ):
        self.equity_peak = 0.0
        self.cooldown_until = 0.0

        self.max_drawdown = float(max_drawdown)
        self.max_daily_loss_sol = float(max_daily_loss_sol)
        self.daily_stop_pct = float(daily_stop_pct)
        self.kill_switch_loss_streak = int(kill_switch_loss_streak)
        self.max_daily_trades = int(max_daily_trades)

        self.session_day = time.strftime("%Y-%m-%d")
        self.daily_realized_pnl = 0.0
        self.daily_trades = 0

        self.manual_kill = False

    def _roll_day(self):
        today = time.strftime("%Y-%m-%d")
        if today != self.session_day:
            self.session_day = today
            self.daily_realized_pnl = 0.0
            self.daily_trades = 0

    def update(self, equity: float):
        self._roll_day()
        self.equity_peak = max(self.equity_peak, float(equity))

    def record_realized(self, pnl_sol: float):
        self._roll_day()
        self.daily_realized_pnl += float(pnl_sol)

    def record_trade(self):
        self._roll_day()
        self.daily_trades += 1

    def drawdown(self, equity: float) -> float:
        if self.equity_peak <= 0:
            return 0.0
        return max(0.0, (self.equity_peak - float(equity)) / self.equity_peak)

    def trigger_cooldown(self, seconds: int = 120):
        self.cooldown_until = time.time() + int(seconds)

    def set_manual_kill(self, is_killed: bool):
        self.manual_kill = bool(is_killed)

    def allow_trade(
        self,
        equity: float,
        loss_streak: int,
        portfolio_can_add_more: bool,
    ):
        """
        回傳: (allow: bool, reason: str)
        """
        self._roll_day()

        # 1. manual kill
        if self.manual_kill:
            return False, "manual_kill"

        # 2. loss streak kill switch
        if int(loss_streak) >= self.kill_switch_loss_streak:
            return False, "loss_streak_kill"

        # 3. daily stop %
        capital = max(float(equity), 1e-9)
        if (self.daily_realized_pnl / capital) < self.daily_stop_pct:
            return False, "daily_stop_pct"

        # 4. max daily loss in SOL
        if self.daily_realized_pnl <= -abs(self.max_daily_loss_sol):
            return False, "daily_loss_sol"

        # 5. max drawdown
        if self.drawdown(equity) >= self.max_drawdown:
            return False, "max_drawdown"

        # 6. cooldown
        if time.time() < self.cooldown_until:
            return False, "cooldown"

        # 7. portfolio exposure
        if not portfolio_can_add_more:
            return False, "portfolio_exposure"

        # extra: daily trade count
        if self.daily_trades >= self.max_daily_trades:
            return False, "max_daily_trades"

        return True, "ok"
