from config.settings import SETTINGS

class Tuner:
    def __init__(self):
        self.threshold = SETTINGS["ENTRY_THRESHOLD"]
    def update(self, pnl: float):
        self.threshold += -0.001 if pnl > 0 else 0.001
        self.threshold = max(SETTINGS["TUNER_MIN"], min(SETTINGS["TUNER_MAX"], self.threshold))

tuner = Tuner()
