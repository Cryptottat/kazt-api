from fastapi import APIRouter, Header, HTTPException
from typing import Optional
from src.models.auth import ConnectRequest, ConnectResponse, TierInfo
from src.models.common import APIResponse
from src.services.auth_service import auth_service

router = APIRouter()


@router.post("/connect", response_model=APIResponse)
async def connect_wallet(req: ConnectRequest):
    """지갑 서명 인증 후 API 키 발급"""
    try:
        result = await auth_service.connect_wallet(
            wallet=req.wallet,
            signature=req.signature,
            message=req.message,
        )
        return APIResponse(success=True, data=result)
    except Exception as e:
        return APIResponse(success=False, error="AUTH_FAILED", message=str(e))


@router.get("/verify", response_model=APIResponse)
async def verify_api_key(x_api_key: Optional[str] = Header(None)):
    """API 키 검증 + 온체인 티어 실시간 갱신"""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API key required")

    key_data = await auth_service.verify_and_refresh_tier(x_api_key)
    if not key_data:
        raise HTTPException(status_code=401, detail="Invalid API key")

    tier = key_data.get("tier", "free")
    limit = auth_service.TIER_LIMITS.get(tier, 3)
    features = auth_service.TIER_FEATURES.get(tier, [])

    return APIResponse(success=True, data={
        "wallet": key_data.get("wallet", ""),
        "tier": tier,
        "daily_limit": limit,
        "features": features,
    })
