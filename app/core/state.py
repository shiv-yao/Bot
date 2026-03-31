class EngineState:
    def __init__(self):
        self.running = True
        self.capital = 1.0
        self.peak_capital = 1.0

        self.positions = []
        self.trade_history = []
        self.logs = []

        self.regime = "unknown"

        self.stats = {
            "signals": 0,
            "executed": 0,
            "rejected": 0,
            "errors": 0,
            "wins": 0,
            "losses": 0,
        }

    def log(self, msg):
        msg = str(msg)
        print(msg)
        self.logs.append(msg)
        self.logs = self.logs[-300:]


engine = EngineState()
