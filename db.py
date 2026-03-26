import sqlite3
from pathlib import Path

DB_PATH = Path("trading.db")

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

conn = get_conn()

conn.execute("""
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    side TEXT NOT NULL,
    mint TEXT NOT NULL,
    amount_sol REAL,
    token_amount REAL,
    entry_price REAL,
    exit_price REAL,
    pnl REAL,
    pnl_pct REAL,
    reason TEXT,
    mode TEXT,
    txid TEXT,
    status TEXT NOT NULL
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS positions (
    mint TEXT PRIMARY KEY,
    amount_sol REAL NOT NULL,
    token_amount REAL DEFAULT 0,
    entry_price REAL DEFAULT 0,
    opened_at TEXT NOT NULL,
    score REAL DEFAULT 0,
    status TEXT NOT NULL,
    last_sync_at TEXT
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL
)
""")

conn.commit()

def log_event(level: str, message: str):
    conn.execute(
        "INSERT INTO events (ts, level, message) VALUES (datetime('now'), ?, ?)",
        (level, message)
    )
    conn.commit()

def insert_trade(side, mint, amount_sol, token_amount, entry_price, exit_price, pnl, pnl_pct, reason, mode, txid, status):
    conn.execute(
        """
        INSERT INTO trades
        (ts, side, mint, amount_sol, token_amount, entry_price, exit_price, pnl, pnl_pct, reason, mode, txid, status)
        VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (side, mint, amount_sol, token_amount, entry_price, exit_price, pnl, pnl_pct, reason, mode, txid, status)
    )
    conn.commit()

def upsert_position(mint, amount_sol, token_amount, entry_price, score, status="OPEN"):
    conn.execute(
        """
        INSERT INTO positions (mint, amount_sol, token_amount, entry_price, opened_at, score, status, last_sync_at)
        VALUES (?, ?, ?, ?, datetime('now'), ?, ?, datetime('now'))
        ON CONFLICT(mint) DO UPDATE SET
            amount_sol=excluded.amount_sol,
            token_amount=excluded.token_amount,
            entry_price=excluded.entry_price,
            score=excluded.score,
            status=excluded.status,
            last_sync_at=datetime('now')
        """,
        (mint, amount_sol, token_amount, entry_price, score, status)
    )
    conn.commit()

def close_position(mint):
    conn.execute("DELETE FROM positions WHERE mint = ?", (mint,))
    conn.commit()

def fetch_open_positions():
    rows = conn.execute("SELECT * FROM positions ORDER BY opened_at DESC").fetchall()
    return [dict(r) for r in rows]

def fetch_recent_trades(limit=50):
    rows = conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
