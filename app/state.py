class Engine:
    def __init__(self):
        self.running = True
        self.mode = "PAPER"

        self.positions = []
        self.logs = []

        self.stats = {
            "rejected": 0
        }

        self.last_trade = {}
        self.last_signal = ""

        self.threshold = 0.02


engine = Engine()
