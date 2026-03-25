import os
from dotenv import load_dotenv

load_dotenv()

JUP_API_KEY = os.getenv("JUP_API_KEY", "")
JUP_BASE_URL = os.getenv("JUP_BASE_URL", "https://api.jup.ag")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
MANUAL_CONFIRM = os.getenv("MANUAL_CONFIRM", "true").lower() == "true"
DEFAULT_INPUT_MINT = os.getenv("DEFAULT_INPUT_MINT", "So11111111111111111111111111111111111111112")
MAX_ORDER_SOL = float(os.getenv("MAX_ORDER_SOL", "0.02"))
MAX_SLIPPAGE_BPS = int(os.getenv("MAX_SLIPPAGE_BPS", "500"))
