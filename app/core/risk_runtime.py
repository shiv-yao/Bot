from app.core.risk_engine import RiskEngine

risk_engine = RiskEngine(
    max_drawdown=0.20,
    max_daily_loss_sol=0.10,
    daily_stop_pct=-0.06,
    kill_switch_loss_streak=6,
    max_daily_trades=40,
)
