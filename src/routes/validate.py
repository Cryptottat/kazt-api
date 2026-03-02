"""
코드 검증 엔드포인트
- POST / -- AI 기반 Anchor 코드 검증 (build, test, security)
- POST /autofix -- Auto-fix loop SSE 스트리밍 (pro+ 전용)
- auth 필수, 레이트 리밋 적용 (AI 호출)
"""

import asyncio
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


class AutofixRequest(BaseModel):
    files: list[FileInput] = Field(min_length=1, max_length=20)
    validate_result: dict
    max_attempts: int = Field(default=1, ge=1, le=5)
    attempt_offset: int = Field(default=0, ge=0, le=4)


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
        """SSE generator with keepalive to prevent proxy timeout.

        autofix_stream() yields events between long Claude API calls.
        During those calls (30-60s each), no data flows and Railway's
        proxy may kill the connection after 300s of total silence.
        We use a queue + periodic keepalive to keep the stream alive.
        """
        queue: asyncio.Queue = asyncio.Queue()

        async def producer():
            try:
                async for event in autofix_stream(files_data, req.validate_result, max_attempts=req.max_attempts, attempt_offset=req.attempt_offset):
                    await queue.put(event)
            except Exception as e:
                await queue.put({"type": "error", "message": str(e)})
            finally:
                await queue.put(None)  # sentinel

        task = asyncio.create_task(producer())

        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    if event is None:
                        break
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    # SSE comment — ignored by client, keeps proxy alive
                    yield ": keepalive\n\n"
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
