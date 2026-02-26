import os
import secrets
import time
from typing import Optional
from src.utils.logger import logger
from src.config import config


class AuthService:
    """지갑 인증 + API 키 관리 + 티어 시스템"""

    TIER_FEATURES = {
        "free": ["design", "simulate"],
        "basic": ["design", "simulate", "save", "export_json"],
        "pro": ["design", "simulate", "save", "export_json", "export_anchor", "bam_config"],
        "elite": ["design", "simulate", "save", "export_json", "export_anchor", "bam_config", "deploy", "templates"],
        "whale": ["design", "simulate", "save", "export_json", "export_anchor", "bam_config", "deploy", "templates", "priority_support"],
    }

    TIER_LIMITS = {
        "free": 3,
        "basic": 50,
        "pro": 500,
        "elite": -1,
        "whale": -1,
    }

    def __init__(self):
        # In-memory storage (Phase 5에서 Redis/PostgreSQL로 교체)
        self.api_keys: dict[str, dict] = {}
        self.usage: dict[str, dict] = {}  # key -> {"date": "2026-02-26", "count": 5}

    def generate_api_key(self) -> str:
        return secrets.token_urlsafe(32)

    async def connect_wallet(self, wallet: str, signature: str, message: str) -> dict:
        """지갑 서명 인증 후 API 키 발급"""
        # 서명 검증 (MVP는 간소화 -- Phase 5에서 실제 nacl 검증 추가)
        # 실제 구현에서는 PyNaCl로 ed25519 서명 검증
        logger.info(f"Wallet connect: {wallet[:8]}...")

        # API 키 생성
        api_key = self.generate_api_key()
        tier = await self.determine_tier(wallet)

        self.api_keys[api_key] = {
            "wallet": wallet,
            "tier": tier,
            "created_at": int(time.time()),
        }

        return {
            "api_key": api_key,
            "wallet": wallet,
            "tier": tier,
        }

    async def verify_api_key(self, api_key: str) -> Optional[dict]:
        """API 키 검증"""
        return self.api_keys.get(api_key)

    async def determine_tier(self, wallet: str) -> str:
        """$KAZT 보유량 기반 티어 결정 (MVP: 기본 free)"""
        # Phase 6에서 실제 온체인 잔액 확인 구현
        # 현재는 기본 free 반환
        return "free"

    def check_rate_limit(self, api_key: str) -> tuple[bool, int, int]:
        """Rate limit 확인. (allowed, used, limit) 반환"""
        key_data = self.api_keys.get(api_key)
        if not key_data:
            # 비인증 유저 = free 티어
            tier = "free"
            usage_key = "anonymous"
        else:
            tier = key_data["tier"]
            usage_key = api_key

        limit = self.TIER_LIMITS.get(tier, 3)
        if limit == -1:
            return True, 0, -1  # 무제한

        today = time.strftime("%Y-%m-%d")
        usage = self.usage.get(usage_key, {"date": "", "count": 0})
        if usage["date"] != today:
            usage = {"date": today, "count": 0}

        if usage["count"] >= limit:
            return False, usage["count"], limit

        return True, usage["count"], limit

    def increment_usage(self, api_key: Optional[str] = None):
        """사용량 증가"""
        usage_key = api_key or "anonymous"
        today = time.strftime("%Y-%m-%d")
        usage = self.usage.get(usage_key, {"date": "", "count": 0})
        if usage["date"] != today:
            usage = {"date": today, "count": 0}
        usage["count"] += 1
        self.usage[usage_key] = usage

    def get_tier_info(self, api_key: str) -> dict:
        """티어 정보 조회"""
        key_data = self.api_keys.get(api_key)
        tier = key_data["tier"] if key_data else "free"
        today = time.strftime("%Y-%m-%d")
        usage_key = api_key or "anonymous"
        usage = self.usage.get(usage_key, {"date": "", "count": 0})
        used = usage["count"] if usage["date"] == today else 0
        limit = self.TIER_LIMITS.get(tier, 3)

        return {
            "tier": tier,
            "daily_limit": limit,
            "used_today": used,
            "features": self.TIER_FEATURES.get(tier, []),
        }


auth_service = AuthService()
