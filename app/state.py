class EngineState:
    def __init__(self):
        self.running = True

        self.capital = 5.0
        self.start_capital = 5.0
        self.peak_capital = 5.0
        self.regime = "unknown"

        self.positions = []
        self.trade_history = []
        self.logs = []

        self.stats = {
            "signals": 0,
            "executed": 0,
            "wins": 0,
            "losses": 0,
            "errors": 0,
            "rejected": 0,
        }

        self.win_streak = 0
        self.loss_streak = 0
        self.last_signal = ""
        self.last_trade = ""

    def log(self, msg):
        msg = str(msg)
        print(msg)
        self.logs.append(msg)
        self.logs = self.logs[-300:]


engine = EngineState()
