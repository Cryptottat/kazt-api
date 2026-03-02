import os
import json

from fastapi import APIRouter, Request, Header, HTTPException
from fastapi.responses import StreamingResponse
from typing import Optional

from src.models.generate import GenerateRequest
from src.models.common import APIResponse
from src.services.generate_service import generate_program, ai_generate_stream
from src.services.auth_service import auth_service

router = APIRouter()


def _get_client_ip(request: Request) -> str:
    """Railway 프록시 대응 -- x-forwarded-for 우선"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/", response_model=APIResponse)
async def generate(
    req: GenerateRequest,
    request: Request,
    x_api_key: Optional[str] = Header(None),
):
    """자연어 설명으로 Solana 프로그램을 생성한다."""
    ai_mode = bool(os.getenv("ANTHROPIC_API_KEY"))

    if ai_mode:
        # AI 모드 -- 인증 필수
        if not x_api_key:
            raise HTTPException(
                status_code=401,
                detail={"error": "MISSING_API_KEY", "message": "API key required for AI generation"},
            )

        # 키 검증 + 실시간 티어 갱신
        key_data = await auth_service.verify_and_refresh_tier(x_api_key)
        if not key_data:
            raise HTTPException(
                status_code=401,
                detail={"error": "INVALID_API_KEY", "message": "Invalid or expired API key"},
            )

        # IP 잠금 확인
        client_ip = _get_client_ip(request)
        ip_ok = await auth_service.check_ip_lock(x_api_key, client_ip)
        if not ip_ok:
            raise HTTPException(
                status_code=403,
                detail={"error": "IP_MISMATCH", "message": "API key is locked to a different IP address"},
            )

        # 레이트 리밋 확인 (갱신된 티어 기준)
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
        result = await generate_program(req.description)
        return APIResponse(success=True, data=result)
    except Exception as e:
        return APIResponse(success=False, error="GENERATE_FAILED", message=str(e))


@router.post("/stream")
async def generate_stream(
    req: GenerateRequest,
    request: Request,
    x_api_key: Optional[str] = Header(None),
):
    """SSE 스트림으로 AI 코드 생성 진행상황을 실시간 전달한다."""
    ai_mode = bool(os.getenv("ANTHROPIC_API_KEY"))

    if ai_mode:
        if not x_api_key:
            raise HTTPException(
                status_code=401,
                detail={"error": "MISSING_API_KEY", "message": "API key required for AI generation"},
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

    async def event_generator():
        async for event in ai_generate_stream(req.description):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
