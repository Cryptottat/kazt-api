"""
Helius RPC 연동 서비스
- Solana 온체인 데이터 조회
- $KAZT 토큰 잔고 기반 티어 결정
"""
import time
from typing import Optional

from src.config import config
from src.services.api_client import RateLimitedClient
from src.utils.logger import logger


# 티어 임계값 (KAZT 토큰 수량)
TIER_THRESHOLDS = [
    (1_000_000, "whale"),
    (100_000, "elite"),
    (10_000, "pro"),
    (1_000, "basic"),
]


class SolanaService:
    """Helius RPC를 통한 Solana 온체인 데이터 조회"""

    def __init__(self):
        self.rpc_url = config.HELIUS_RPC_URL
        self.token_mint = config.TOKEN_CA
        self._client: Optional[RateLimitedClient] = None
        # 인메모리 티어 캐시: wallet -> {"tier": str, "expires": float}
        self._tier_cache: dict[str, dict] = {}
        self._cache_ttl = 300  # 5분

    @property
    def is_configured(self) -> bool:
        """RPC URL이 설정되어 있는지 확인 (mainnet-beta도 허용)"""
        return bool(self.rpc_url)

    def _get_client(self) -> RateLimitedClient:
        """HTTP 클라이언트 lazy 초기화"""
        if self._client is None:
            self._client = RateLimitedClient(
                requests_per_second=5.0,
                timeout=10.0,
                max_retries=3,
            )
        return self._client

    async def close(self):
        """클라이언트 종료"""
        if self._client:
            await self._client.close()
            self._client = None

    async def _rpc_call(self, method: str, params: list) -> dict:
        """Helius JSON-RPC 호출"""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }
        client = self._get_client()
        response = await client.post(self.rpc_url, json=payload)
        result = response.json()
        if "error" in result:
            raise Exception(f"RPC error: {result['error']}")
        return result.get("result")

    async def get_balance(self, wallet: str) -> float:
        """
        SOL 잔고 조회
        반환: SOL 단위 (lamports / 1e9)
        """
        if not self.is_configured:
            logger.warning("HELIUS_RPC_URL 미설정 -- 스텁 잔고 반환")
            return 0.0

        try:
            result = await self._rpc_call("getBalance", [wallet])
            lamports = result.get("value", 0)
            return lamports / 1e9
        except Exception as e:
            logger.error(f"SOL 잔고 조회 실패 (wallet={wallet[:8]}...): {e}")
            return 0.0

    async def get_token_balance(self, wallet: str, mint: Optional[str] = None) -> float:
        """
        SPL 토큰 잔고 조회
        mint가 None이면 config.TOKEN_CA 사용 (KAZT 토큰)
        반환: 토큰 수량 (UI amount)
        """
        token_mint = mint or self.token_mint
        if not self.is_configured:
            logger.warning("HELIUS_RPC_URL 미설정 -- 스텁 토큰 잔고 반환")
            return 0.0

        if not token_mint:
            logger.warning("TOKEN_CA 미설정 -- 토큰 잔고 조회 불가")
            return 0.0

        try:
            # getTokenAccountsByOwner로 해당 토큰의 잔고 조회
            result = await self._rpc_call(
                "getTokenAccountsByOwner",
                [
                    wallet,
                    {"mint": token_mint},
                    {"encoding": "jsonParsed"},
                ],
            )
            accounts = result.get("value", [])
            if not accounts:
                return 0.0

            # 첫 번째 토큰 계정에서 잔고 추출
            token_info = accounts[0]["account"]["data"]["parsed"]["info"]
            token_amount = token_info["tokenAmount"]
            return float(token_amount.get("uiAmount", 0) or 0)

        except Exception as e:
            logger.error(f"토큰 잔고 조회 실패 (wallet={wallet[:8]}...): {e}")
            return 0.0

    async def get_recent_transactions(self, wallet: str, limit: int = 10) -> list:
        """최근 트랜잭션 목록 조회"""
        if not self.is_configured:
            logger.warning("HELIUS_RPC_URL 미설정 -- 빈 트랜잭션 목록 반환")
            return []

        try:
            result = await self._rpc_call(
                "getSignaturesForAddress",
                [wallet, {"limit": limit}],
            )
            return result or []
        except Exception as e:
            logger.error(f"트랜잭션 조회 실패 (wallet={wallet[:8]}...): {e}")
            return []

    async def get_account_info(self, address: str) -> dict:
        """계정 정보 조회"""
        if not self.is_configured:
            logger.warning("HELIUS_RPC_URL 미설정 -- 빈 계정 정보 반환")
            return {}

        try:
            result = await self._rpc_call(
                "getAccountInfo",
                [address, {"encoding": "jsonParsed"}],
            )
            return result or {}
        except Exception as e:
            logger.error(f"계정 정보 조회 실패 (address={address[:8]}...): {e}")
            return {}

    async def determine_tier(self, wallet: str) -> str:
        """
        $KAZT 토큰 보유량 기반 티어 결정
        - 0 KAZT = free
        - 1,000+ = basic
        - 10,000+ = pro
        - 100,000+ = elite
        - 1,000,000+ = whale

        캐시: 5분간 유효
        RPC 호출 실패 시 "free" 폴백
        """
        # 캐시 확인
        cached = self._tier_cache.get(wallet)
        if cached and cached["expires"] > time.time():
            return cached["tier"]

        # RPC 미설정이면 free
        if not self.is_configured or not self.token_mint:
            return "free"

        try:
            balance = await self.get_token_balance(wallet)
            tier = "free"
            for threshold, tier_name in TIER_THRESHOLDS:
                if balance >= threshold:
                    tier = tier_name
                    break

            # 캐시 저장
            self._tier_cache[wallet] = {
                "tier": tier,
                "expires": time.time() + self._cache_ttl,
            }
            logger.info(f"티어 결정: wallet={wallet[:8]}..., balance={balance}, tier={tier}")
            return tier

        except Exception as e:
            logger.error(f"티어 결정 실패 (wallet={wallet[:8]}...): {e}")
            return "free"


# 싱글톤 인스턴스
solana_service = SolanaService()
