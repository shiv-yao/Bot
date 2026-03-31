class EngineState:
    def __init__(self):
        self.running = True
        self.capital = 30.0
        self.logs = []
        self.trade_history = []
        self.positions = []
        self.regime = "neutral"
        self.threshold = 0.02
        self.last_signal = ""
        self.stats = {
            "signals": 0,
            "executed": 0,
            "rejected": 0,
            "errors": 0,
        }


engine = EngineState()
