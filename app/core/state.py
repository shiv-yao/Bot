class EngineState:
    def __init__(self):
        self.running = True

        # 資金 / 狀態
        self.capital = 1.0
        self.peak_capital = 1.0

        # 持倉 / 交易
        self.positions = []
        self.trade_history = []

        # 統計
        self.stats = {
            "signals": 0,
            "executed": 0,
            "rejected": 0,
            "errors": 0,
            "wins": 0,
            "losses": 0,
        }

        # 其他
        self.logs = []
        self.regime = "unknown"
        self.last_signal = ""
        self.last_trade = ""

    def log(self, msg):
        msg = str(msg)
        print(msg)
        self.logs.append(msg)
        self.logs = self.logs[-300:]


engine = EngineState()
