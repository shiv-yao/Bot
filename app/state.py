class EngineState:
    def __init__(self):
        self.running = True

        # capital
        self.capital = 5.0
        self.peak_capital = 5.0
        self.start_capital = 5.0

        # positions / trades
        self.positions = []
        self.trade_history = []

        # logs
        self.logs = []

        # stats
        self.stats = {
            "signals": 0,
            "executed": 0,
            "wins": 0,
            "losses": 0,
            "errors": 0,
            "rejected": 0,
        }

        # misc
        self.regime = "unknown"
        self.last_signal = ""
        self.last_trade = ""
        self.win_streak = 0
        self.loss_streak = 0

    def log(self, msg):
        msg = str(msg)
        print(msg)
        self.logs.append(msg)
        self.logs = self.logs[-300:]


engine = EngineState()
