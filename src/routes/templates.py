from fastapi import APIRouter
from src.models.common import APIResponse

router = APIRouter()


@router.get("/", response_model=APIResponse)
async def list_templates():
    """템플릿 마켓 목록 (2차 후킹 -- 현재 placeholder)"""
    sample_templates = [
        {
            "id": "tpl_dex_amm",
            "name": "DEX AMM Protection Pack",
            "description": "Standard MEV protection rules for AMM-based DEX protocols",
            "block_count": 4,
            "downloads": 1200,
            "price_kazt": 500,
            "author": "0xAbc...123",
        },
        {
            "id": "tpl_lending",
            "name": "Lending Protocol Pack",
            "description": "Ordering and batching rules optimized for lending protocols",
            "block_count": 3,
            "downloads": 890,
            "price_kazt": 300,
            "author": "0xDef...456",
        },
        {
            "id": "tpl_orderbook",
            "name": "Orderbook Fairness Pack",
            "description": "Price-time priority ordering with anti-frontrunning filters",
            "block_count": 5,
            "downloads": 650,
            "price_kazt": 800,
            "author": "0x789...abc",
        },
    ]
    return APIResponse(success=True, data=sample_templates)
