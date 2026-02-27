import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    PORT = int(os.getenv("PORT", 8000))
    DEBUG = os.getenv("DEBUG", "false").lower() == "true"
    DATABASE_URL = os.getenv("DATABASE_URL", "")
    REDIS_URL = os.getenv("REDIS_URL", "")
    CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
    API_SECRET_KEY = os.getenv("API_SECRET_KEY", "dev-secret-key")
    HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
    HELIUS_RPC_URL = os.getenv("HELIUS_RPC_URL", "https://api.mainnet-beta.solana.com")
    TOKEN_CA = os.getenv("TOKEN_CA", "")

    # Tier thresholds (% of total supply, 1B = 1,000,000,000)
    TIER_WHALE_THRESHOLD = float(os.getenv("TIER_WHALE_THRESHOLD", "0.025"))    # 25,000,000
    TIER_ELITE_THRESHOLD = float(os.getenv("TIER_ELITE_THRESHOLD", "0.005"))    # 5,000,000
    TIER_PRO_THRESHOLD = float(os.getenv("TIER_PRO_THRESHOLD", "0.001"))        # 1,000,000
    TIER_BASIC_THRESHOLD = float(os.getenv("TIER_BASIC_THRESHOLD", "0.0001"))   # 100,000

    # Rate limits (per day)
    TIER_FREE_LIMIT = int(os.getenv("TIER_FREE_LIMIT", "3"))
    TIER_BASIC_LIMIT = int(os.getenv("TIER_BASIC_LIMIT", "50"))
    TIER_PRO_LIMIT = int(os.getenv("TIER_PRO_LIMIT", "500"))


config = Config()
