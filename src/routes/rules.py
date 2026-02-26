from fastapi import APIRouter, Header, HTTPException
from typing import Optional
from src.models.rules import (
    ValidateRequest, ValidateResponse, SimulateRequest, SimulationReport,
    RuleSetCreate, RuleSetResponse, ExportRequest,
)
from src.models.common import APIResponse
from src.services.rule_service import rule_service
from src.services.auth_service import auth_service

router = APIRouter()


@router.post("/validate", response_model=APIResponse)
async def validate_rules(req: ValidateRequest):
    """규칙 블록 세트의 유효성을 검증한다."""
    result = rule_service.validate(req.blocks)
    return APIResponse(success=True, data=result.model_dump())


@router.post("/simulate", response_model=APIResponse)
async def simulate_rules(
    req: SimulateRequest,
    x_api_key: Optional[str] = Header(None),
):
    """규칙 세트에 대해 샘플 TX를 시뮬레이션한다."""
    # Rate limit 확인
    allowed, used, limit = auth_service.check_rate_limit(x_api_key or "")
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Used {used}/{limit} today.",
        )

    result = rule_service.simulate(req)
    auth_service.increment_usage(x_api_key)
    return APIResponse(success=True, data=result.model_dump())


@router.post("/save", response_model=APIResponse)
async def save_rules(
    req: RuleSetCreate,
    x_api_key: Optional[str] = Header(None),
):
    """규칙 세트를 저장한다."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API key required to save rules")

    key_data = await auth_service.verify_api_key(x_api_key)
    if not key_data:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # 기능 권한 확인
    tier_info = auth_service.get_tier_info(x_api_key)
    if "save" not in tier_info["features"]:
        raise HTTPException(
            status_code=403,
            detail=f"Save is not available for {tier_info['tier']} tier. Upgrade to Basic or above.",
        )

    result = rule_service.save_rule_set(req, owner=key_data["wallet"])
    return APIResponse(success=True, data=result)


@router.get("/my", response_model=APIResponse)
async def my_rules(x_api_key: Optional[str] = Header(None)):
    """내 규칙 목록 조회"""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API key required")

    key_data = await auth_service.verify_api_key(x_api_key)
    if not key_data:
        raise HTTPException(status_code=401, detail="Invalid API key")

    rules = rule_service.get_user_rules(key_data["wallet"])
    return APIResponse(success=True, data=rules)


@router.get("/{rule_id}", response_model=APIResponse)
async def get_rule(rule_id: str):
    """규칙 세트 단일 조회"""
    rule = rule_service.get_rule_set(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule set not found")
    return APIResponse(success=True, data=rule)


@router.post("/export", response_model=APIResponse)
async def export_rules(
    req: ExportRequest,
    x_api_key: Optional[str] = Header(None),
):
    """규칙을 JSON 또는 Anchor 코드로 내보낸다."""
    if req.format == "anchor":
        if not x_api_key:
            raise HTTPException(status_code=401, detail="API key required for Anchor export")

        key_data = await auth_service.verify_api_key(x_api_key)
        if not key_data:
            raise HTTPException(status_code=401, detail="Invalid API key")

        tier_info = auth_service.get_tier_info(x_api_key)
        if "export_anchor" not in tier_info["features"]:
            raise HTTPException(
                status_code=403,
                detail=f"Anchor export is not available for {tier_info['tier']} tier. Upgrade to Pro or above.",
            )

    result = rule_service.export_rules(req.blocks, req.format)
    return APIResponse(success=True, data=result)
