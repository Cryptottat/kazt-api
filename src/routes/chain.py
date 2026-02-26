"""
체인 데이터 엔드포인트
- GET /balance/{wallet} -- SOL 잔고 조회
- GET /tier/{wallet} -- 토큰 기반 티어 조회
- auth 선택적 (공개 조회 가능)
"""
from fastapi import APIRouter, HTTPException

from src.models.common import APIResponse
from src.services.solana_service import solana_service
from src.utils.logger import logger

router = APIRouter()


@router.get("/balance/{wallet}", response_model=APIResponse)
async def get_balance(wallet: str):
    """SOL 잔고 조회 (공개 API)"""
    # 지갑 주소 기본 검증 (Solana 주소 = base58, 32~44자)
    if not wallet or len(wallet) < 32 or len(wallet) > 44:
        raise HTTPException(status_code=400, detail="Invalid wallet address")

    try:
        balance = await solana_service.get_balance(wallet)
        return APIResponse(
            success=True,
            data={
                "wallet": wallet,
                "balance_sol": balance,
            },
        )
    except Exception as e:
        logger.error(f"잔고 조회 실패: {e}")
        # 외부 API 실패해도 서비스는 죽지 않음
        return APIResponse(
            success=True,
            data={
                "wallet": wallet,
                "balance_sol": 0.0,
                "note": "RPC unavailable, showing default value",
            },
        )


@router.get("/tier/{wallet}", response_model=APIResponse)
async def get_tier(wallet: str):
    """$KAZT 토큰 보유량 기반 티어 조회 (공개 API)"""
    if not wallet or len(wallet) < 32 or len(wallet) > 44:
        raise HTTPException(status_code=400, detail="Invalid wallet address")

    try:
        tier = await solana_service.determine_tier(wallet)
        token_balance = await solana_service.get_token_balance(wallet)

        return APIResponse(
            success=True,
            data={
                "wallet": wallet,
                "tier": tier,
                "kazt_balance": token_balance,
                "thresholds": {
                    "free": 0,
                    "basic": 1_000,
                    "pro": 10_000,
                    "elite": 100_000,
                    "whale": 1_000_000,
                },
            },
        )
    except Exception as e:
        logger.error(f"티어 조회 실패: {e}")
        return APIResponse(
            success=True,
            data={
                "wallet": wallet,
                "tier": "free",
                "kazt_balance": 0.0,
                "note": "RPC unavailable, showing default tier",
            },
        )
