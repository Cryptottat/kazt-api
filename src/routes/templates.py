from fastapi import APIRouter, HTTPException
from src.models.common import APIResponse

router = APIRouter()

# 템플릿 마켓플레이스 데이터 (Phase 4 -- 2차 후킹 메커니즘)
# 실제 DB 연동 전까지 인메모리 데이터 사용
TEMPLATES = [
    {
        "id": "tpl_dex_amm",
        "name": "DEX AMM Protection Pack",
        "description": "Standard MEV protection rules for AMM-based DEX protocols. Includes FIFO ordering, batch auctions, and sandwich attack filters.",
        "block_count": 4,
        "downloads": 1200,
        "price_kazt": 50000,
        "author": "0xAbc...123",
        "tags": ["defi", "amm", "mev-protection"],
        "created_at": "2025-12-01T00:00:00Z",
        "blocks": [
            {"type": "ordering", "name": "FIFO Queue"},
            {"type": "batching", "name": "Batch Auction Window"},
            {"type": "filter", "name": "Sandwich Filter"},
            {"type": "priority", "name": "Stake-Weighted Priority"},
        ],
    },
    {
        "id": "tpl_lending",
        "name": "Lending Protocol Pack",
        "description": "Ordering and batching rules optimized for lending protocols. Prevents oracle manipulation and ensures fair liquidation sequencing.",
        "block_count": 3,
        "downloads": 890,
        "price_kazt": 30000,
        "author": "0xDef...456",
        "tags": ["defi", "lending", "liquidation"],
        "created_at": "2025-12-15T00:00:00Z",
        "blocks": [
            {"type": "ordering", "name": "Time-Priority Ordering"},
            {"type": "batching", "name": "Liquidation Batcher"},
            {"type": "filter", "name": "Oracle Manipulation Guard"},
        ],
    },
    {
        "id": "tpl_orderbook",
        "name": "Orderbook Fairness Pack",
        "description": "Price-time priority ordering with anti-frontrunning filters. Full CLOB matching engine integration with partial fill support.",
        "block_count": 5,
        "downloads": 650,
        "price_kazt": 80000,
        "author": "0x789...abc",
        "tags": ["defi", "orderbook", "clob", "fairness"],
        "created_at": "2026-01-05T00:00:00Z",
        "blocks": [
            {"type": "ordering", "name": "Price-Time Priority"},
            {"type": "matching", "name": "CLOB Engine"},
            {"type": "filter", "name": "Frontrun Detector"},
            {"type": "batching", "name": "Order Aggregator"},
            {"type": "priority", "name": "Maker Priority Boost"},
        ],
    },
    {
        "id": "tpl_nft_mint",
        "name": "NFT Mint Guard",
        "description": "Fair minting protection for NFT launches. Randomized ordering prevents bot sniping and ensures equitable distribution.",
        "block_count": 2,
        "downloads": 2100,
        "price_kazt": 15000,
        "author": "0xMnt...789",
        "tags": ["nft", "minting", "anti-bot"],
        "created_at": "2026-01-20T00:00:00Z",
        "blocks": [
            {"type": "ordering", "name": "Randomized Sequencer"},
            {"type": "filter", "name": "Bot Address Blacklist"},
        ],
    },
    {
        "id": "tpl_perp_mev",
        "name": "Perpetuals MEV Shield",
        "description": "MEV protection suite for perpetual futures protocols. Shields against oracle frontrunning and ensures fair position entry ordering.",
        "block_count": 4,
        "downloads": 430,
        "price_kazt": 65000,
        "author": "0xPrp...321",
        "tags": ["defi", "perpetuals", "mev-protection", "derivatives"],
        "created_at": "2026-02-01T00:00:00Z",
        "blocks": [
            {"type": "ordering", "name": "Position Queue"},
            {"type": "filter", "name": "Oracle Frontrun Filter"},
            {"type": "batching", "name": "Funding Rate Batcher"},
            {"type": "matching", "name": "Counterparty Matcher"},
        ],
    },
    {
        "id": "tpl_staking_seq",
        "name": "Staking Reward Sequencer",
        "description": "Optimized reward distribution sequencing for staking protocols. Batches reward claims and orders by stake weight for gas efficiency.",
        "block_count": 3,
        "downloads": 780,
        "price_kazt": 40000,
        "author": "0xStk...654",
        "tags": ["staking", "rewards", "gas-optimization"],
        "created_at": "2026-02-10T00:00:00Z",
        "blocks": [
            {"type": "ordering", "name": "Stake-Weight Ordering"},
            {"type": "batching", "name": "Reward Claim Batcher"},
            {"type": "priority", "name": "Validator Priority"},
        ],
    },
]

# ID 기반 빠른 조회용 인덱스
_TEMPLATE_INDEX = {t["id"]: t for t in TEMPLATES}


@router.get("/", response_model=APIResponse)
async def list_templates():
    """템플릿 마켓 목록 (2차 후킹 메커니즘)"""
    return APIResponse(success=True, data=TEMPLATES)


@router.get("/{template_id}", response_model=APIResponse)
async def get_template(template_id: str):
    """단일 템플릿 상세 조회"""
    template = _TEMPLATE_INDEX.get(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return APIResponse(success=True, data=template)
