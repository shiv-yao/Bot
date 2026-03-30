from config.settings import SETTINGS
async def insider_score(flow):
    if flow > 0.7: return 1.0
    if flow > SETTINGS["INSIDER_MIN"]: return 0.7
    return 0.0
