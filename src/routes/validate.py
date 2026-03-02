"""
코드 검증 엔드포인트
- POST / -- AI 기반 Anchor 코드 검증 (build, test, security)
- POST /build -- 실제 anchor build + test SSE 스트리밍
- POST /autofix -- Auto-fix loop SSE 스트리밍 (pro+ 전용)
- auth 필수, 레이트 리밋 적용
"""

import os
import json

from fastapi import APIRouter, Request, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional

from src.models.common import APIResponse
from src.services.validate_service import validate_code
from src.services.auth_service import auth_service
from src.services.autofix_service import autofix_stream
from src.services.build_service import build_stream

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


class BuildRequest(BaseModel):
    files: list[FileInput] = Field(min_length=1, max_length=20)
    program_name: str = "kazt_program"


@router.post("/build")
async def build(
    req: BuildRequest,
    request: Request,
    x_api_key: Optional[str] = Header(None),
):
    """실제 anchor build + test SSE 스트리밍"""
    if not x_api_key:
        raise HTTPException(
            status_code=401,
            detail={"error": "MISSING_API_KEY", "message": "API key required for build"},
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

    tier = key_data.get("tier", "free")

    # Build limit check (separate from rate limit)
    build_allowed, build_used, build_limit = await auth_service.check_build_limit(x_api_key, tier)
    if not build_allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "BUILD_LIMIT_EXCEEDED",
                "message": f"Daily build limit exceeded ({build_used}/{build_limit}). Upgrade tier for more builds.",
            },
        )

    files_data = [f.model_dump() for f in req.files]

    async def event_generator():
        try:
            async for event in build_stream(files_data, req.program_name):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


class AutofixRequest(BaseModel):
    files: list[FileInput] = Field(min_length=1, max_length=20)
    validate_result: dict


AUTOFIX_TIERS = {"pro", "elite", "whale"}


@router.post("/autofix")
async def autofix(
    req: AutofixRequest,
    request: Request,
    x_api_key: Optional[str] = Header(None),
):
    """Auto-fix loop SSE 스트리밍 -- pro/elite/whale 전용"""
    if not x_api_key:
        raise HTTPException(
            status_code=401,
            detail={"error": "MISSING_API_KEY", "message": "API key required for auto-fix"},
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

    tier = key_data.get("tier", "free")
    if tier not in AUTOFIX_TIERS:
        raise HTTPException(
            status_code=403,
            detail={"error": "TIER_RESTRICTED", "message": "Auto-fix requires Pro tier or above"},
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

    files_data = [f.model_dump() for f in req.files]

    async def event_generator():
        try:
            async for event in autofix_stream(files_data, req.validate_result):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
