class EngineState:
    def __init__(self):
        self.running = True
        self.logs = []
        self.positions = []
        self.stats = {
            "signals": 0,
            "executed": 0,
            "rejected": 0,
            "errors": 0,
        }
        self.last_signal = ""
        self.capital = 1.0


engine = EngineState()
