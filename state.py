
engine = {
    "running": True,
    "capital": 100,
    "last_signal": "",
    "logs": []
}

def log(msg):
    engine["logs"].append(msg)
    if len(engine["logs"]) > 50:
        engine["logs"].pop(0)
