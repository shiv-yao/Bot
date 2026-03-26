class Engine:
    def __init__(self):
        self.running = True
        self.mode = "LIVE"

        self.sol_balance = 0.0
        self.capital = 0.0

        self.last_signal = ""
        self.last_trade = ""

        self.positions = []
        self.logs = []

        self.stats = {
            "signals": 0,
            "buys": 0,
            "sells": 0,
            "errors": 0
        }

        self.trade_history = []

engine = Engine()
