class EngineState:
    def __init__(self):
        self.capital = 30.0
        self.logs = []
        self.trade_history = []
        self.positions = []
        self.regime = "neutral"
        self.threshold = 0.02
        self.stats = {"signals":0,"executed":0,"rejected":0,"errors":0,"jito_sent":0,"pump_seen":0,"mempool_seen":0}
        self.wallets = {}
        self.candidates = set()

engine = EngineState()
