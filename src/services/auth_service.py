import os
import secrets
import time
from typing import Optional

import base58
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError

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
        # In-memory 폴백 저장소 (DB/Redis 미연결시 사용)
        self.api_keys: dict[str, dict] = {}
        self.usage: dict[str, dict] = {}  # key -> {"date": "2026-02-26", "count": 5}

    def generate_api_key(self) -> str:
        return secrets.token_urlsafe(32)

    async def check_ip_lock(self, api_key: str, client_ip: str) -> bool:
        """
        IP 잠금 확인. 첫 사용 시 IP 잠금, 이후 다른 IP 차단.
        Returns True if allowed, False if IP mismatch.
        Redis 미연결 시 항상 허용.
        """
        from src.services import cache_service

        lock_key = f"kazt:iplock:{api_key}"
        locked_ip = await cache_service.get(lock_key)

        if locked_ip is None:
            # 첫 사용 -- IP 잠금 (24시간)
            await cache_service.set(lock_key, client_ip, ttl=86400)
            return True

        return locked_ip == client_ip

    def _verify_signature(self, wallet: str, signature: str, message: str) -> bool:
        """ed25519 서명 검증"""
        try:
            pubkey_bytes = base58.b58decode(wallet)
            sig_bytes = base58.b58decode(signature)
            verify_key = VerifyKey(pubkey_bytes)
            verify_key.verify(message.encode("utf-8"), sig_bytes)
            return True
        except (BadSignatureError, Exception) as e:
            logger.warning(f"서명 검증 실패 (wallet={wallet[:8]}...): {e}")
            return False

    async def connect_wallet(self, wallet: str, signature: str, message: str) -> dict:
        """지갑 서명 인증 후 API 키 발급"""
        # ed25519 서명 검증
        if not self._verify_signature(wallet, signature, message):
            raise ValueError("Invalid signature")

        logger.info(f"Wallet connect: {wallet[:8]}...")

        # 토큰 잔액 조회 + 티어 결정
        balance = 0.0
        try:
            from src.services.solana_service import solana_service
            balance = await solana_service.get_token_balance(wallet)
        except Exception as e:
            logger.warning(f"토큰 잔액 조회 실패: {e}")

        # API 키 생성
        api_key = self.generate_api_key()
        tier = await self.determine_tier(wallet)
        limit = self.TIER_LIMITS.get(tier, 3)

        # In-memory 저장 (폴백용)
        self.api_keys[api_key] = {
            "wallet": wallet,
            "tier": tier,
            "created_at": int(time.time()),
        }

        # DB에 유저 upsert
        from src.services import db_service
        await db_service.upsert_user(wallet=wallet, api_key=api_key, tier=tier)

        return {
            "api_key": api_key,
            "wallet": wallet,
            "tier": tier,
            "balance": balance,
            "daily_limit": limit,
            "features": self.TIER_FEATURES.get(tier, []),
        }

    async def verify_api_key(self, api_key: str) -> Optional[dict]:
        """API 키 검증 -- 캐시 -> DB -> in-memory 순으로 조회"""
        from src.services import cache_service
        from src.services import db_service

        cache_key = f"kazt:session:{api_key}"

        # 1. 캐시에서 조회
        cached = await cache_service.get(cache_key)
        if cached is not None:
            return cached

        # 2. DB에서 조회
        db_user = await db_service.get_user_by_api_key(api_key)
        if db_user is not None:
            result = {
                "wallet": db_user["wallet"],
                "tier": db_user["tier"],
                "created_at": (
                    db_user["created_at"].isoformat()
                    if hasattr(db_user["created_at"], "isoformat")
                    else db_user["created_at"]
                ),
            }
            # 캐시에 저장 (5분 -- 실시간 검증 방식이므로 짧게)
            await cache_service.set(cache_key, result, ttl=300)
            return result

        # 3. In-memory 폴백
        return self.api_keys.get(api_key)

    async def verify_and_refresh_tier(self, api_key: str) -> Optional[dict]:
        """
        API 키 검증 + 온체인 잔액으로 티어 실시간 갱신.
        앱 실행 시, AI 요청 시마다 호출.
        """
        from src.services import cache_service, db_service

        key_data = await self.verify_api_key(api_key)
        if not key_data:
            return None

        wallet = key_data.get("wallet")
        if not wallet:
            return key_data

        # 온체인 잔액으로 티어 재결정 (solana_service 내부에 5분 캐시 있음)
        new_tier = await self.determine_tier(wallet)
        old_tier = key_data.get("tier", "free")

        if new_tier != old_tier:
            key_data["tier"] = new_tier
            # DB + 캐시 갱신
            await db_service.upsert_user(wallet=wallet, tier=new_tier)
            await cache_service.delete(f"kazt:session:{api_key}")
            # in-memory도 갱신
            if api_key in self.api_keys:
                self.api_keys[api_key]["tier"] = new_tier
            logger.info(f"티어 실시간 갱신: {wallet[:8]}... {old_tier} -> {new_tier}")

        return key_data

    async def determine_tier(self, wallet: str) -> str:
        """
        $KAZT 보유량 기반 티어 결정
        solana_service를 통해 온체인 잔액 확인
        RPC 호출 실패 시 "free" 폴백
        """
        try:
            from src.services.solana_service import solana_service
            tier = await solana_service.determine_tier(wallet)
            return tier
        except Exception as e:
            logger.error(f"온체인 티어 결정 실패 (wallet={wallet[:8]}...): {e}")
            return "free"

    def check_rate_limit(self, api_key: str) -> tuple[bool, int, int]:
        """
        Rate limit 확인 (동기). (allowed, used, limit) 반환
        Redis 기반 비동기 버전은 check_rate_limit_async 사용
        """
        key_data = self.api_keys.get(api_key)
        if not key_data:
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

    async def check_rate_limit_async(self, api_key: str) -> tuple[bool, int, int]:
        """
        Rate limit 확인 (비동기 Redis 버전)
        Redis 미연결시 in-memory 폴백
        """
        from src.services import cache_service

        # 티어 결정
        key_data = await self.verify_api_key(api_key) if api_key else None
        if not key_data:
            tier = "free"
            usage_key = "anonymous"
        else:
            tier = key_data.get("tier", "free")
            usage_key = api_key

        limit = self.TIER_LIMITS.get(tier, 3)
        if limit == -1:
            return True, 0, -1  # 무제한

        # Redis 카운터 시도
        today = time.strftime("%Y-%m-%d")
        rate_key = f"kazt:rate:{usage_key}:{today}"
        count = await cache_service.increment(rate_key, ttl=86400)

        if count > 0:
            # Redis가 동작중 -- Redis 카운터 사용
            if count > limit:
                return False, count, limit
            return True, count, limit

        # Redis 미연결 -- in-memory 폴백
        return self.check_rate_limit(api_key)

    def increment_usage(self, api_key: Optional[str] = None):
        """사용량 증가 (in-memory)"""
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
