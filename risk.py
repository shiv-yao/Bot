MAX_DAILY_LOSS = -0.2
LOSS_STREAK_LIMIT = 5

loss_streak = 0

def risk_guard(equity):
    global loss_streak

    if equity < MAX_DAILY_LOSS:
        return False

    if loss_streak >= LOSS_STREAK_LIMIT:
        return False

    return True

def update_loss(pnl):
    global loss_streak
    if pnl < 0:
        loss_streak += 1
    else:
        loss_streak = 0
