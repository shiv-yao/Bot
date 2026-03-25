from .config import MAX_ORDER_SOL, MAX_SLIPPAGE_BPS

LAMPORTS_PER_SOL = 1_000_000_000

def validate_order_ui_amount(ui_amount: float):
    if ui_amount <= 0:
        raise ValueError("Amount must be positive")
    if ui_amount > MAX_ORDER_SOL:
        raise ValueError(f"Amount exceeds MAX_ORDER_SOL={MAX_ORDER_SOL}")

def validate_slippage_bps(slippage_bps: int):
    if slippage_bps <= 0:
        raise ValueError("slippage_bps must be positive")
    if slippage_bps > MAX_SLIPPAGE_BPS:
        raise ValueError(f"slippage_bps exceeds MAX_SLIPPAGE_BPS={MAX_SLIPPAGE_BPS}")

def ui_sol_to_lamports(ui_sol: float) -> str:
    return str(int(ui_sol * LAMPORTS_PER_SOL))
