"""
코드 검증 엔드포인트
- POST / -- AI 기반 Anchor 코드 검증 (build, test, security)
- auth 필수, 레이트 리밋 적용 (AI 호출)
"""

import os

from fastapi import APIRouter, Request, Header, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from src.models.common import APIResponse
from src.services.validate_service import validate_code
from src.services.auth_service import auth_service

router = APIRouter()


class FileInput(BaseModel):
    path: str
    content: str
    language: str = "unknown"


class ValidateCodeRequest(BaseModel):
    files: list[FileInput] = Field(min_length=1, max_length=20)


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/", response_model=APIResponse)
async def validate(
    req: ValidateCodeRequest,
    request: Request,
    x_api_key: Optional[str] = Header(None),
):
    """AI 기반 Anchor 코드 검증 -- build, test, security 분석"""
    ai_mode = bool(os.getenv("ANTHROPIC_API_KEY"))

    if ai_mode:
        if not x_api_key:
            raise HTTPException(
                status_code=401,
                detail={"error": "MISSING_API_KEY", "message": "API key required for code validation"},
            )

        key_data = await auth_service.verify_and_refresh_tier(x_api_key)
        if not key_data:
            raise HTTPException(
                status_code=401,
                detail={"error": "INVALID_API_KEY", "message": "Invalid or expired API key"},
            )

        client_ip = _get_client_ip(request)
        ip_ok = await auth_service.check_ip_lock(x_api_key, client_ip)
        if not ip_ok:
            raise HTTPException(
                status_code=403,
                detail={"error": "IP_MISMATCH", "message": "API key is locked to a different IP address"},
            )

        allowed, used, limit = await auth_service.check_rate_limit_async(x_api_key)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "RATE_LIMIT_EXCEEDED",
                    "message": f"Daily limit exceeded ({used}/{limit})",
                },
            )

    try:
        files_data = [f.model_dump() for f in req.files]
        result = await validate_code(files_data)
        return APIResponse(success=True, data=result)
    except Exception as e:
        return APIResponse(success=False, error="VALIDATE_FAILED", message=str(e))
