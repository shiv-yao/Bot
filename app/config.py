from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    RPC_URL: str = "https://api.mainnet-beta.solana.com"
    HELIUS_GEYSER_WS: str = ""                    # 填你的 Helius Enhanced WS
    JUPITER_QUOTE_URL: str = "https://quote-api.jup.ag/v6/quote"
    JUPITER_SWAP_URL: str = "https://quote-api.jup.ag/v6/swap"
    JITO_BUNDLE_URL: str = "https://mainnet.block-engine.jito.wtf/api/v1/bundles"
    PRIVATE_KEY: str = ""                         # base58 私鑰（建議 burner wallet）
    WALLET_ADDRESS: str = ""
    MAX_SLIPPAGE_BPS: int = 80
    JITO_TIP_LAMPORTS: int = 500000               # 動態調整
    MAX_POSITION_PCT: float = 0.08
    SMART_MONEY_WEIGHT: float = 0.40
    MIN_SMART_INFLOW_SOL: float = 0.8
    MIN_LIQUIDITY_USD: float = 35000
    RUG_CHECK_ENABLED: bool = True

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
