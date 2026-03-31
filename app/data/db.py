import sqlite3

conn = sqlite3.connect("trades.db", check_same_thread=False)
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY,
    mint TEXT,
    pnl REAL,
    timestamp REAL
)
""")

def save_trade(mint, pnl):
    c.execute(
        "INSERT INTO trades (mint, pnl, timestamp) VALUES (?, ?, strftime('%s','now'))",
        (mint, pnl),
    )
    conn.commit()
