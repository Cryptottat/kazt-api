"""
텔레그램 알림 시스템
- 규칙 배포, 시뮬레이션 완료, 사용량 경고, 시스템 에러 알림
- 환경변수 미설정 시 로그만 남기고 정상 동작
"""
import os
from typing import Literal

import httpx

from src.utils.logger import logger

PriorityLevel = Literal["info", "warning", "error", "critical"]

# 우선순위별 접두사
PRIORITY_PREFIX = {
    "info": "[INFO]",
    "warning": "[WARNING]",
    "error": "[ERROR]",
    "critical": "[CRITICAL]",
}


class Notifier:
    """텔레그램 봇 알림 전송"""

    TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    @property
    def is_configured(self) -> bool:
        """텔레그램 설정 여부 확인"""
        return bool(self.bot_token) and bool(self.chat_id)

    async def send(self, message: str, priority: PriorityLevel = "info") -> bool:
        """
        텔레그램 메시지 전송
        설정 미완료 시 로그만 남기고 True 반환 (서비스 중단 방지)
        """
        prefix = PRIORITY_PREFIX.get(priority, "[INFO]")
        full_message = f"{prefix} Kazt\n{message}"

        if not self.is_configured:
            logger.info(f"텔레그램 미설정 -- 알림 로그만 기록: {full_message}")
            return True

        try:
            url = self.TELEGRAM_API_URL.format(token=self.bot_token)
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    url,
                    json={
                        "chat_id": self.chat_id,
                        "text": full_message,
                        "parse_mode": "HTML",
                    },
                )
                if response.status_code == 200:
                    return True
                else:
                    logger.warning(
                        f"텔레그램 전송 실패: status={response.status_code}, "
                        f"body={response.text}"
                    )
                    return False
        except Exception as e:
            logger.error(f"텔레그램 전송 에러: {e}")
            return False

    async def rule_deployed(self, wallet: str, rule_name: str) -> bool:
        """규칙 배포 알림"""
        message = (
            f"규칙 배포 완료\n"
            f"- Wallet: {wallet[:8]}...{wallet[-4:]}\n"
            f"- Rule: {rule_name}"
        )
        return await self.send(message, priority="info")

    async def simulation_complete(
        self, wallet: str, total_txs: int, filtered: int
    ) -> bool:
        """시뮬레이션 완료 알림"""
        message = (
            f"시뮬레이션 완료\n"
            f"- Wallet: {wallet[:8]}...{wallet[-4:]}\n"
            f"- Total TXs: {total_txs}\n"
            f"- Filtered: {filtered}"
        )
        return await self.send(message, priority="info")

    async def high_usage_alert(self, wallet: str, tier: str, usage: int) -> bool:
        """사용량 경고"""
        message = (
            f"높은 사용량 감지\n"
            f"- Wallet: {wallet[:8]}...{wallet[-4:]}\n"
            f"- Tier: {tier}\n"
            f"- Usage: {usage}"
        )
        return await self.send(message, priority="warning")

    async def system_error(self, error_message: str) -> bool:
        """시스템 에러 알림"""
        message = f"시스템 에러 발생\n- {error_message}"
        return await self.send(message, priority="critical")


# 싱글톤 인스턴스
notifier = Notifier()
