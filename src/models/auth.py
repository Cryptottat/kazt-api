from pydantic import BaseModel, Field


class ConnectRequest(BaseModel):
    wallet: str = Field(min_length=32, max_length=44)
    signature: str
    message: str


class ConnectResponse(BaseModel):
    api_key: str
    wallet: str
    tier: str


class TierInfo(BaseModel):
    tier: str
    daily_limit: int
    used_today: int
    features: list[str]
