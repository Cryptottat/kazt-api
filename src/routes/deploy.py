"""
배포 엔드포인트
- POST /simulate -- BAM 배포 시뮬레이션
- POST /devnet -- Devnet 배포 패키지 준비
- GET /{deployment_id} -- 배포 상태 조회
- auth 필수, pro 이상 티어만 배포 가능
"""
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional

from src.models.common import APIResponse
from src.models.rules import RuleBlock
from src.services.auth_service import auth_service
from src.services.bam_service import bam_service
from src.services.deploy_service import prepare_devnet_deploy
from src.utils.notifier import notifier
from src.utils.logger import logger

router = APIRouter()


class DeploySimulateRequest(BaseModel):
    """배포 시뮬레이션 요청"""
    blocks: list[RuleBlock]
    name: str = Field(min_length=1, max_length=100, default="Untitled Rule")


class FileInput(BaseModel):
    path: str
    content: str
    language: str = "unknown"


class DevnetDeployRequest(BaseModel):
    """Devnet 배포 패키지 요청"""
    files: list[FileInput] = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=100, default="my_program")


@router.post("/simulate", response_model=APIResponse)
async def simulate_deploy(
    req: DeploySimulateRequest,
    x_api_key: Optional[str] = Header(None),
):
    """BAM 배포 시뮬레이션 (pro 이상 티어 필요)"""
    # 인증 필수
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API key required for deployment")

    key_data = await auth_service.verify_api_key(x_api_key)
    if not key_data:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # 티어 체크 -- pro 이상만 배포 가능
    tier_info = auth_service.get_tier_info(x_api_key)
    allowed_tiers = ("pro", "elite", "whale")
    if tier_info["tier"] not in allowed_tiers:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Deployment requires Pro tier or above. "
                f"Current tier: {tier_info['tier']}"
            ),
        )

    # BAM 배포 시뮬레이션
    try:
        result = await bam_service.deploy_rules(
            blocks=[b.model_dump() for b in req.blocks],
            wallet=key_data["wallet"],
        )

        # 알림 전송 (실패해도 응답에는 영향 없음)
        await notifier.rule_deployed(
            wallet=key_data["wallet"],
            rule_name=req.name,
        )

        return APIResponse(success=True, data=result)

    except Exception as e:
        logger.error(f"배포 시뮬레이션 실패: {e}")
        await notifier.system_error(f"배포 시뮬레이션 실패: {e}")
        raise HTTPException(status_code=500, detail="Deployment simulation failed")


@router.post("/devnet", response_model=APIResponse)
async def deploy_devnet(
    req: DevnetDeployRequest,
    x_api_key: Optional[str] = Header(None),
):
    """Devnet 배포 패키지 준비 (pro 이상 티어 필요)"""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API key required for deployment")

    key_data = await auth_service.verify_api_key(x_api_key)
    if not key_data:
        raise HTTPException(status_code=401, detail="Invalid API key")

    tier_info = auth_service.get_tier_info(x_api_key)
    allowed_tiers = ("pro", "elite", "whale")
    if tier_info["tier"] not in allowed_tiers:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Deployment requires Pro tier or above. "
                f"Current tier: {tier_info['tier']}"
            ),
        )

    try:
        files_data = [f.model_dump() for f in req.files]
        result = await prepare_devnet_deploy(files_data, req.name)

        if result.get("ready"):
            await notifier.rule_deployed(
                wallet=key_data["wallet"],
                rule_name=f"devnet:{req.name}",
            )

        return APIResponse(success=True, data=result)

    except Exception as e:
        logger.error(f"Devnet 배포 준비 실패: {e}")
        raise HTTPException(status_code=500, detail="Devnet deployment preparation failed")


@router.get("/{deployment_id}", response_model=APIResponse)
async def get_deployment_status(
    deployment_id: str,
    x_api_key: Optional[str] = Header(None),
):
    """배포 상태 조회"""
    # 인증 필수
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API key required")

    key_data = await auth_service.verify_api_key(x_api_key)
    if not key_data:
        raise HTTPException(status_code=401, detail="Invalid API key")

    try:
        result = await bam_service.get_deployment_status(deployment_id)
        return APIResponse(success=True, data=result)
    except Exception as e:
        logger.error(f"배포 상태 조회 실패: {e}")
        raise HTTPException(status_code=500, detail="Failed to get deployment status")
