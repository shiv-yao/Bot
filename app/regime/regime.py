from config.settings import SETTINGS

class Regime:
    def __init__(self):
        self.mode = "neutral"
    def update(self, score: float):
        if score > 0.05: self.mode = "bull"
        elif score < 0.01: self.mode = "bear"
        else: self.mode = "neutral"
    def multiplier(self):
        if self.mode == "bull": return SETTINGS["BULL_MULTIPLIER"]
        if self.mode == "bear": return SETTINGS["BEAR_MULTIPLIER"]
        return 1.0

regime = Regime()
